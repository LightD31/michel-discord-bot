"""spotipy is synchronous: every call must run off the event loop, one at a time.

And each server goes through its own account (P36): its app, its token file.
"""

import asyncio
import importlib
import json
import stat
import sys
import threading
import time

import pytest
import requests

import src.integrations.spotify as spotify_module
from features.spotify.search import (
    NO_RESULT,
    NO_TRACK,
    NOT_CONNECTED,
    PROMPT,
    UNAVAILABLE,
    track_choices,
)
from src.discord_ext.errors import user_message
from src.integrations.spotify import (
    SpotifyNotConnected,
    blocking_client,
    is_connected,
    run_spotify,
    spotify_for,
)


class FakeSpotify:
    """Stands in for the spotipy client; records which thread ran each call."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.threads: list[str] = []
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def track(self, track_id, market=None):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.threads.append(threading.current_thread().name)
        time.sleep(self.delay)  # what spotipy does: a blocking HTTP round trip
        with self._lock:
            self.active -= 1
        return {"id": track_id, "market": market}


@pytest.fixture
def fake_sp(monkeypatch):
    fake = FakeSpotify(delay=0.05)
    monkeypatch.setattr(spotify_module, "blocking_client", lambda guild_id: fake)
    return fake


def test_calls_run_on_the_spotify_worker(fake_sp):
    async def main():
        return await spotify_for("1").track("t1", market="FR"), threading.current_thread().name

    result, loop_thread = asyncio.run(main())
    assert result == {"id": "t1", "market": "FR"}
    assert fake_sp.threads and fake_sp.threads[0].startswith("spotify")
    assert fake_sp.threads[0] != loop_thread


def test_event_loop_keeps_running_during_a_slow_call(fake_sp):
    fake_sp.delay = 0.3
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    async def main():
        task = asyncio.create_task(ticker())
        await spotify_for("1").track("slow")
        task.cancel()

    asyncio.run(main())
    assert ticks >= 10  # the loop was not frozen for the 0.3 s call


def test_calls_are_serialized(fake_sp):
    async def main():
        await asyncio.gather(*(spotify_for("1").track(f"t{i}") for i in range(4)))

    asyncio.run(main())
    assert fake_sp.max_active == 1  # no two spotipy calls race on the token refresh


def test_errors_propagate():
    def boom():
        raise requests.exceptions.ConnectionError("no route")

    with pytest.raises(requests.exceptions.ConnectionError):
        asyncio.run(run_spotify(boom))


def test_transport_errors_are_part_of_spotify_errors():
    assert issubclass(requests.exceptions.Timeout, spotify_module.SPOTIFY_ERRORS)
    assert not issubclass(requests.exceptions.ConnectionError, ConnectionError)


# ── Autocomplete ────────────────────────────────────────────────────────


def item(name: str, artist: str = "Artist", album: str = "Album") -> dict:
    return {
        "name": name,
        "uri": f"spotify:track:{name}",
        "artists": [{"name": artist}],
        "album": {"name": album},
    }


def test_track_choices_formatting():
    assert track_choices([]) == [{"name": NO_RESULT, "value": NO_TRACK}]
    choices = track_choices([item("Song"), item("x" * 200)])
    assert choices[0] == {"name": "Artist - Song (Album: Album)", "value": "spotify:track:Song"}
    assert len(choices[1]["name"]) == 100


@pytest.fixture
def spotify_common(store_config):
    store_config({"config": {}, "servers": {}})
    sys.modules.pop("extensions.spotify._common", None)
    module = importlib.import_module("extensions.spotify._common")
    yield module
    sys.modules.pop("extensions.spotify._common", None)


def test_autocomplete_answers_in_time_even_when_spotify_is_slow(spotify_common, monkeypatch):
    class SlowApi:
        async def search(self, *args, **kwargs):
            await asyncio.sleep(1)
            return {"tracks": {"items": [item("late")]}}

    monkeypatch.setattr(spotify_common, "spotify_for", lambda guild_id: SlowApi())
    monkeypatch.setattr(spotify_common, "AUTOCOMPLETE_TIMEOUT_SECONDS", 0.05)
    choices = asyncio.run(spotify_common.autocomplete_track_choices("1", "late"))
    assert choices == [{"name": UNAVAILABLE, "value": NO_TRACK}]


def test_autocomplete_results_and_prompt(spotify_common, monkeypatch):
    class Api:
        async def search(self, query, **kwargs):
            return {"tracks": {"items": [item(query)]}}

    monkeypatch.setattr(spotify_common, "spotify_for", lambda guild_id: Api())
    assert asyncio.run(spotify_common.autocomplete_track_choices("1", "")) == [
        {"name": PROMPT, "value": NO_TRACK}
    ]
    choices = asyncio.run(spotify_common.autocomplete_track_choices("1", "hit"))
    assert choices[0]["value"] == "spotify:track:hit"


def test_autocomplete_survives_spotify_errors(spotify_common, monkeypatch):
    class BrokenApi:
        async def search(self, *args, **kwargs):
            raise requests.exceptions.ReadTimeout("slow")

    monkeypatch.setattr(spotify_common, "spotify_for", lambda guild_id: BrokenApi())
    choices = asyncio.run(spotify_common.autocomplete_track_choices("1", "x"))
    assert choices == [{"name": UNAVAILABLE, "value": NO_TRACK}]


BLOCKING_CLIENT = "src.integrations.spotify.blocking_client"


def blocking_client_uses(source: str) -> list[int]:
    """Lines that import or use the synchronous client, under any alias."""
    import ast

    from source_scan import Imports

    tree = ast.parse(source)
    imports = Imports(tree)
    lines = [line for line, module in imports.modules if module == BLOCKING_CLIENT]
    for node in ast.walk(tree):
        name = imports.resolve(node) if isinstance(node, ast.Attribute) else None
        if name and (name == BLOCKING_CLIENT or name.startswith(BLOCKING_CLIENT + ".")):
            lines.append(node.lineno)
    return sorted(set(lines))


def test_nothing_on_an_event_loop_calls_the_blocking_client():
    """Extensions, features and the dashboard go through spotify_for, never blocking_client."""
    from source_scan import python_files, rel

    offenders = [
        f"{rel(path)}:{line}"
        for path in python_files("extensions", "features", "src/webui", "src/discord_ext")
        for line in blocking_client_uses(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "spotipy blocks: go through spotify_for (a thread), never blocking_client"
    )


@pytest.mark.parametrize(
    "source",
    [
        "from src.integrations.spotify import blocking_client",
        "from src.integrations.spotify import blocking_client as client",
        "import src.integrations.spotify as s\ns.blocking_client('1').track('x')",
        "from src.integrations import spotify\nspotify.blocking_client('1').track('x')",
    ],
)
def test_the_scan_sees_the_client_under_any_name(source):
    assert blocking_client_uses(source)


# ── One account per server (P36) ────────────────────────────────────────


def app(client_id):
    return {"spotifyClientId": client_id, "spotifyClientSecret": "x"}  # pragma: allowlist secret


@pytest.fixture
def two_servers(store_config, tmp_path, monkeypatch):
    """Server 1 has its app and a connected account; server 2 only its app."""
    store_config(
        {
            "config": {},
            "servers": {
                "1": {"moduleSpotify": app("app-1")},
                "2": {"moduleSpotify": app("app-2")},
                "3": {"moduleSpotify": {"enabled": True}},
            },
        }
    )
    monkeypatch.setattr(spotify_module, "TOKEN_DIR", tmp_path / "spotify")
    spotify_module._AccountFile(spotify_module.token_path("1")).save_token_to_cache(
        {"access_token": "a", "refresh_token": "r", "expires_at": 4102444800, "scope": "x"}
    )
    yield tmp_path / "spotify"
    for guild in ("1", "2", "3"):
        spotify_module.reset_client(guild)


def test_each_server_has_its_own_client_app_and_token(two_servers):
    one = blocking_client("1")
    assert one is blocking_client("1")
    assert one.auth_manager.client_id == "app-1"
    assert one.auth_manager.cache_handler.path == two_servers / "1.json"
    assert blocking_client("2").auth_manager.client_id == "app-2"
    assert (is_connected("1"), is_connected("2"), is_connected("3")) == (True, False, False)


def test_the_token_file_is_the_bots_alone(two_servers):
    path = two_servers / "1.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["refresh_token"] == "r"


def test_a_server_without_an_account_is_refused_rather_than_prompted(two_servers):
    """spotipy, with no token, reads a code from stdin: the worker would hang for good."""
    with pytest.raises(SpotifyNotConnected):
        asyncio.run(spotify_for("2").current_user())
    with pytest.raises(SpotifyNotConnected):
        asyncio.run(spotify_for("3").current_user())  # no app either


def test_changed_credentials_build_a_new_client(two_servers, store_config):
    before = blocking_client("1")
    store_config(
        {
            "config": {},
            "servers": {"1": {"moduleSpotify": app("new")}},
        }
    )
    after = blocking_client("1")
    assert after is not before and after.auth_manager.client_id == "new"


def test_a_member_is_told_who_connects_it():
    message = user_message(SpotifyNotConnected(), "ref")
    assert "administrateur" in message and "ref" not in message


def test_autocomplete_says_the_server_isnt_connected(spotify_common, monkeypatch):
    class Api:
        async def search(self, *args, **kwargs):
            raise SpotifyNotConnected()

    monkeypatch.setattr(spotify_common, "spotify_for", lambda guild_id: Api())
    choices = asyncio.run(spotify_common.autocomplete_track_choices("2", "x"))
    assert choices == [{"name": NOT_CONNECTED, "value": NO_TRACK}]
