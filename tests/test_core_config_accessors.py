"""Live config accessors: ``ModuleConfig`` and ``global_config`` read the store."""

from src.core.config import ModuleConfig, config_store, global_config, load_discord2name

XP = ModuleConfig("moduleXp")


def _config() -> dict:
    return {
        "config": {"discord": {"devGuildId": "1"}},
        "servers": {
            "10": {"moduleXp": {"enabled": True, "xpRate": 2}, "discord2name": {"5": "Alice"}},
            "20": {"moduleXp": {"enabled": False, "xpRate": 3}},
            "30": {"moduleTricount": {"enabled": True}},
            "40": "pas un serveur",
            "50": {"moduleXp": "pas un module"},
        },
    }


def test_module_lists_the_guilds_where_it_is_on(store_config):
    store_config(_config())
    assert XP.guild_ids() == ["10"]
    assert XP.items() == [("10", {"enabled": True, "xpRate": 2})]


def test_module_settings_are_empty_where_it_is_off(store_config):
    store_config(_config())
    assert XP.get("10") == {"enabled": True, "xpRate": 2}
    assert XP.get(10) == XP.get(" 10 ")
    for guild_id in ("20", "30", "40", "50", "99", None):
        assert XP.get(guild_id) == {}
        assert not XP.enabled(guild_id)
    assert XP.enabled(10)


def test_module_reads_follow_every_store_update(store_config):
    config = store_config(_config())
    assert XP.get("10")["xpRate"] == 2

    # What a dashboard save does: the store swaps in the new config.
    config = {**config, "servers": {**config["servers"], "20": {"moduleXp": {"enabled": True}}}}
    config["servers"]["10"] = {"moduleXp": {"enabled": True, "xpRate": 5}}
    config_store._update_and_notify(config)

    assert XP.get("10")["xpRate"] == 5
    assert XP.guild_ids() == ["10", "20"]


def test_global_config_and_discord2name_read_the_store(store_config):
    store_config(_config())
    assert global_config() == {"discord": {"devGuildId": "1"}}
    assert load_discord2name(10) == {"5": "Alice"}
    assert load_discord2name("20") == {}


def test_accessors_tolerate_an_empty_or_odd_config(store_config):
    store_config({})
    assert global_config() == {}
    assert XP.items() == [] and XP.get("10") == {} and not XP.enabled("10")
    store_config({"config": [], "servers": []})
    assert global_config() == {}
    assert XP.guild_ids() == [] and XP.get("10") == {} and not XP.enabled("10")
    assert load_discord2name("10") == {}
