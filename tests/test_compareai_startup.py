"""compareai's startup (extensions/compareai/__init__.py): prices, indexes, the closing task.

The APIs, the repositories and the task are replaced: what is checked is that
one failure (a guild's index, the usage index) stops neither the others nor
the task that closes the votes.
"""

from types import SimpleNamespace

import pytest

import extensions.compareai as compareai
from extensions.compareai import CompareAIExtension
from src.core.errors import DatabaseError


class Api:
    def __init__(self):
        self.priced = False

    async def load_prices(self):
        self.priced = True


class Repo:
    indexed: list = []
    failing: set = set()

    def __init__(self, gid=None):
        self.gid = gid

    async def ensure_indexes(self):
        if self.gid in Repo.failing:
            raise DatabaseError("down")
        Repo.indexed.append(self.gid)


class Task:
    def __init__(self, running=False):
        self.running = running
        self.started = 0

    def start(self):
        self.started += 1


@pytest.fixture
def startup(monkeypatch):
    apis = {"openrouter": Api(), "nanogpt": Api()}
    Repo.indexed, Repo.failing = [], set()

    async def get_api(name):
        return apis.get(name)

    monkeypatch.setattr(compareai.llm, "configured_apis", lambda: list(apis))
    monkeypatch.setattr(compareai.llm, "get_api", get_api)
    monkeypatch.setattr(compareai, "global_settings", lambda: SimpleNamespace(problems=[]))
    monkeypatch.setattr(compareai, "enabled_guild_ids", lambda: ["1", "2"])
    monkeypatch.setattr(compareai, "repo_for", Repo)
    monkeypatch.setattr(compareai, "AiUsageRepository", Repo)

    def make(task=None):
        ext = object.__new__(CompareAIExtension)
        task = task or Task()
        # The class attribute is the Task; an instance attribute shadows it here.
        ext.__dict__["close_due_comparisons"] = task
        return ext, task

    return SimpleNamespace(apis=apis, make=make)


async def test_startup_loads_prices_builds_indexes_and_starts_the_task(startup):
    ext, task = startup.make()

    await CompareAIExtension.on_startup.callback(ext)

    assert all(api.priced for api in startup.apis.values())
    assert Repo.indexed == ["1", "2", None]  # each guild, then the usage counters
    assert task.started == 1


async def test_a_failing_index_stops_neither_the_others_nor_the_task(startup):
    Repo.failing = {"1", None}
    ext, task = startup.make()

    await CompareAIExtension.on_startup.callback(ext)

    assert Repo.indexed == ["2"]
    assert task.started == 1


async def test_a_reload_does_not_start_the_task_twice(startup):
    ext, task = startup.make(Task(running=True))
    await CompareAIExtension.on_startup.callback(ext)
    assert task.started == 0


async def test_without_an_api_nothing_is_priced(startup, monkeypatch, caplog):
    monkeypatch.setattr(compareai.llm, "configured_apis", lambda: [])
    ext, task = startup.make()
    await CompareAIExtension.on_startup.callback(ext)
    assert not any(api.priced for api in startup.apis.values())
    assert "No AI API configured" in caplog.text
    assert task.started == 1
