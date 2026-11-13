"""Tests for ConfigStore.mutate (atomic read-modify-write) and its WebUI wrapper."""

import json

import pytest
from fastapi import HTTPException

from src.core import config as core_config
from src.core.config import config_store
from src.core.errors import ConfigError
from src.webui.auth import DiscordOAuth
from src.webui.context import WebUIContext


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"config": {"discord": {"devGuildId": "1"}}, "servers": {}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(core_config, "CONFIG_PATH", str(path))
    # Reset the singleton cache so tests don't see each other's data.
    config_store._data = None
    yield path
    config_store._data = None


def read_config(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_mutate_applies_and_persists(config_file):
    def mutator(data):
        data["servers"]["42"] = {"moduleXp": {"enabled": True}}

    result = config_store.mutate(mutator)
    assert result["servers"]["42"]["moduleXp"]["enabled"] is True
    on_disk = read_config(config_file)
    assert on_disk["servers"]["42"]["moduleXp"]["enabled"] is True
    # The store cache reflects the mutation too.
    assert config_store.get()["servers"]["42"]["moduleXp"]["enabled"] is True


def test_sequential_mutations_do_not_lose_updates(config_file):
    config_store.mutate(lambda d: d["servers"].update({"1": {"a": 1}}))
    config_store.mutate(lambda d: d["servers"].update({"2": {"b": 2}}))
    on_disk = read_config(config_file)
    assert on_disk["servers"] == {"1": {"a": 1}, "2": {"b": 2}}


def test_mutate_notifies_subscribers(config_file):
    seen = []
    unsubscribe = config_store.subscribe(seen.append)
    try:
        config_store.mutate(lambda d: d["servers"].update({"7": {}}))
    finally:
        unsubscribe()
    assert len(seen) == 1
    assert "7" in seen[0]["servers"]


def test_mutate_refuses_missing_or_empty_config(tmp_path, monkeypatch):
    monkeypatch.setattr(core_config, "CONFIG_PATH", str(tmp_path / "absent.json"))
    config_store._data = None
    called = []
    with pytest.raises(ConfigError):
        config_store.mutate(called.append)
    assert called == []


def test_mutate_does_not_write_when_mutator_raises(config_file):
    before = read_config(config_file)

    def mutator(data):
        data["servers"]["evil"] = {}
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        config_store.mutate(mutator)
    assert read_config(config_file) == before


@pytest.fixture
def ctx():
    oauth = DiscordOAuth(client_id="", client_secret="", redirect_uri="")
    return WebUIContext(bot=None, bot_loop=None, oauth=oauth)


def test_mutate_config_maps_config_error_to_503(ctx, monkeypatch):
    def boom(_mutator):
        raise ConfigError("unreadable")

    monkeypatch.setattr(config_store, "mutate", boom)
    with pytest.raises(HTTPException) as exc:
        ctx.mutate_config(lambda d: None)
    assert exc.value.status_code == 503
    assert "refusée" in exc.value.detail


def test_mutate_config_surfaces_permission_error(ctx, monkeypatch):
    def boom(_mutator):
        raise PermissionError(13, "Permission denied", "config/.config-x.json.tmp")

    monkeypatch.setattr(config_store, "mutate", boom)
    with pytest.raises(HTTPException) as exc:
        ctx.mutate_config(lambda d: None)
    assert exc.value.status_code == 500
    assert "chown -R 1000:1000" in exc.value.detail


def test_mutate_config_passes_through_result(ctx, monkeypatch, config_file):
    result = ctx.mutate_config(lambda d: d["servers"].update({"9": {}}))
    assert "9" in result["servers"]


def test_the_cache_follows_the_disk_across_threads(config_file, monkeypatch):
    """The cache was set after the write lock: two threads could leave it on the older write (B16)."""
    import threading
    import time

    real_lock = config_store._state_lock
    a_wrote = threading.Event()

    class SlowForA:
        """A pauses as it updates the cache, giving B the time to write."""

        def __enter__(self):
            if threading.current_thread().name == "A" and a_wrote.is_set():
                time.sleep(0.2)
            return real_lock.__enter__()

        def __exit__(self, *exc):
            return real_lock.__exit__(*exc)

    monkeypatch.setattr(config_store, "_state_lock", SlowForA())

    def set_to(value):
        def mutator(data):
            data["servers"]["who"] = {"v": value}
            if value == "A":
                a_wrote.set()

        return mutator

    a = threading.Thread(target=config_store.mutate, args=(set_to("A"),), name="A")
    a.start()
    a_wrote.wait(2)
    time.sleep(0.05)  # A is now past its write
    b = threading.Thread(target=config_store.mutate, args=(set_to("B"),), name="B")
    b.start()
    a.join()
    b.join()

    assert read_config(config_file)["servers"]["who"] == {"v": "B"}
    assert config_store.get()["servers"]["who"] == {"v": "B"}
