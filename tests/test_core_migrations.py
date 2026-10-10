"""Tests for the startup config migrations (``src.core.migrations``)."""

import src.core.migrations as migrations
from src.core.migrations import migrate_ai_section, migrate_guildeux, run_migrations


def test_guildeux_becomes_embed_manager():
    data = {
        "servers": {
            "1": {
                "moduleGuildeux": {"enabled": True, "lienChannelId": "10", "lienMessageId": "20"},
                "publicDashboardToken": "tok",
            }
        }
    }
    assert migrate_guildeux(data) == ["1"]
    assert data["servers"]["1"] == {
        "moduleEmbedManager": {
            "enabled": True,
            "embeds": [],
            "channelId": "10",
            "messageId": "20",
        },
        "publicDashboardToken": "tok",
    }


def test_existing_embed_manager_wins_and_old_key_goes():
    existing = {"enabled": True, "channelId": "99", "embeds": [{"title": "x"}]}
    data = {
        "servers": {
            "1": {
                "moduleGuildeux": {"enabled": False, "lienChannelId": "10"},
                "moduleEmbedManager": dict(existing),
            }
        }
    }
    migrate_guildeux(data)
    assert data["servers"]["1"] == {"moduleEmbedManager": existing}


def test_disabled_and_partial_guildeux():
    data = {"servers": {"1": {"moduleGuildeux": {}}, "2": {"moduleXp": {}}, "3": "junk"}}
    assert migrate_guildeux(data) == ["1"]
    assert data["servers"]["1"] == {"moduleEmbedManager": {"enabled": False, "embeds": []}}
    assert migrate_guildeux(data) == []  # idempotent


class _Store:
    def __init__(self, data):
        self.data = data
        self.writes = 0

    def get(self):
        return self.data

    def mutate(self, mutator):
        mutator(self.data)
        self.writes += 1
        return self.data


def test_run_migrations_writes_only_when_needed(monkeypatch):
    clean = _Store({"servers": {"1": {"moduleEmbedManager": {}}}})
    monkeypatch.setattr(migrations, "config_store", clean)
    run_migrations()
    assert clean.writes == 0

    stale = _Store({"servers": {"1": {"moduleGuildeux": {"enabled": False}}}})
    monkeypatch.setattr(migrations, "config_store", stale)
    run_migrations()
    assert stale.writes == 1
    assert "moduleGuildeux" not in stale.data["servers"]["1"]
    run_migrations()
    assert stale.writes == 1


def test_ai_models_move_out_of_openrouter():
    models = [
        {"provider": "gpt", "model_id": "openai/gpt-4.1", "display_name": "GPT"},
        {"provider": "nano", "api": "nanogpt", "model_id": "x"},
    ]
    data = {
        "config": {"OpenRouter": {"openrouterApiKey": "k", "modelsToCompare": 2, "models": models}}
    }
    assert migrate_ai_section(data) == ["models", "modelsToCompare"]
    assert data["config"]["OpenRouter"] == {"openrouterApiKey": "k"}
    assert data["config"]["AI"] == {
        "models": [
            {
                "provider": "gpt",
                "model_id": "openai/gpt-4.1",
                "display_name": "GPT",
                "api": "openrouter",
            },
            {"provider": "nano", "api": "nanogpt", "model_id": "x"},
        ],
        "modelsToCompare": 2,
    }
    assert migrate_ai_section(data) == []  # idempotent


def test_ai_section_values_already_set_win():
    data = {
        "config": {
            "OpenRouter": {"models": [{"provider": "old", "model_id": "o"}]},
            "AI": {"models": [], "defaultModel": "x"},
        }
    }
    assert migrate_ai_section(data) == ["models"]
    assert data["config"]["AI"] == {"models": [], "defaultModel": "x"}
    assert data["config"]["OpenRouter"] == {}
    assert migrate_ai_section({"config": {"OpenRouter": "junk"}}) == []
    assert migrate_ai_section({}) == []


def test_run_migrations_applies_the_ai_move(monkeypatch):
    stale = _Store({"config": {"OpenRouter": {"modelsToCompare": 4}}, "servers": {}})
    monkeypatch.setattr(migrations, "config_store", stale)
    run_migrations()
    assert stale.writes == 1
    assert stale.data["config"]["AI"] == {"modelsToCompare": 4}
    run_migrations()
    assert stale.writes == 1
