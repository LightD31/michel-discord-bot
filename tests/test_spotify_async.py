"""spotipy is synchronous: every call must run off the event loop, one at a time."""

import asyncio
import importlib
import sys
import threading
import time

import pytest
import requests

import src.integrations.spotify as spotify_module
from features.spotify.search import NO_RESULT, NO_TRACK, PROMPT, UNAVAILABLE, track_choices
from src.integrations.spotify import run_spotify, spotify_api


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
    monkeypatch.setattr(spotify_module, "sp", fake)
    return fake


def test_calls_run_on_the_spotify_worker(fake_sp):
    async def main():
        return await spotify_api.track("t1", market="FR"), threading.current_thread().name

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
        await spotify_api.track("slow")
        task.cancel()

    asyncio.run(main())
    assert ticks >= 10  # the loop was not frozen for the 0.3 s call


def test_calls_are_serialized(fake_sp):
    async def main():
        await asyncio.gather(*(spotify_api.track(f"t{i}") for i in range(4)))

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
def spotify_common(monkeypatch, tmp_path):
    import src.core.config as core_config

    monkeypatch.setattr(
        core_config,
        "load_config",
        lambda module_name=None: ({"misc": {"dataFolder": str(tmp_path)}}, {}, []),
    )
    sys.modules.pop("extensions.spotify._common", None)
    module = importlib.import_module("extensions.spotify._common")
    yield module
    sys.modules.pop("extensions.spotify._common", None)


def test_autocomplete_answers_in_time_even_when_spotify_is_slow(spotify_common, monkeypatch):
    class SlowApi:
        async def search(self, *args, **kwargs):
            await asyncio.sleep(1)
            return {"tracks": {"items": [item("late")]}}

    monkeypatch.setattr(spotify_common, "spotify_api", SlowApi())
    monkeypatch.setattr(spotify_common, "AUTOCOMPLETE_TIMEOUT_SECONDS", 0.05)
    choices = asyncio.run(spotify_common.autocomplete_track_choices("late"))
    assert choices == [{"name": UNAVAILABLE, "value": NO_TRACK}]


def test_autocomplete_results_and_prompt(spotify_common, monkeypatch):
    class Api:
        async def search(self, query, **kwargs):
            return {"tracks": {"items": [item(query)]}}

    monkeypatch.setattr(spotify_common, "spotify_api", Api())
    assert asyncio.run(spotify_common.autocomplete_track_choices("")) == [
        {"name": PROMPT, "value": NO_TRACK}
    ]
    choices = asyncio.run(spotify_common.autocomplete_track_choices("hit"))
    assert choices[0]["value"] == "spotify:track:hit"


def test_autocomplete_survives_spotify_errors(spotify_common, monkeypatch):
    class BrokenApi:
        async def search(self, *args, **kwargs):
            raise requests.exceptions.ReadTimeout("slow")

    monkeypatch.setattr(spotify_common, "spotify_api", BrokenApi())
    choices = asyncio.run(spotify_common.autocomplete_track_choices("x"))
    assert choices == [{"name": UNAVAILABLE, "value": NO_TRACK}]


def test_no_extension_calls_the_blocking_client():
    """Extensions run on the event loop: they go through spotify_api, never sp."""
    import ast
    from pathlib import Path

    offenders = []
    for path in Path("extensions").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "src.integrations.spotify":
                offenders += [f"{path}: imports sp" for a in node.names if a.name == "sp"]
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sp"
            ):
                offenders.append(f"{path}:{node.lineno} sp.{node.func.attr}()")
    assert offenders == []
