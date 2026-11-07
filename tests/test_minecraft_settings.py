"""A guild's Minecraft settings, and stats caches kept apart per server."""

from features.minecraft import (
    MinecraftSettings,
    clear_stats_caches,
    stats_cache_for,
)


def test_settings_read_the_module_config_with_its_defaults():
    settings = MinecraftSettings.from_config(
        {
            "minecraftUrl": " mc.example.org ",
            "minecraftIp": "10.0.0.5",
            "minecraftPort": "25565",
            "minecraftServerType": "Forge",
            "minecraftSftpHostKey": "ssh-ed25519 AAAA",
        }
    )
    assert settings.address == "mc.example.org"
    assert settings.port == 25565
    assert settings.server_type == "Forge"
    # SFTP falls back to the game server's IP and the usual account.
    assert (settings.sftp_host, settings.sftp_port, settings.sftp_username) == (
        "10.0.0.5",
        2225,
        "Discord",
    )
    assert settings.sftp_host_key == "ssh-ed25519 AAAA"
    assert settings.has_server


def test_missing_or_bad_values_fall_back():
    settings = MinecraftSettings.from_config({"minecraftPort": "abc", "minecraftSftpPort": None})
    assert (settings.port, settings.sftp_port) == (0, 2225)
    assert not settings.has_server
    assert MinecraftSettings.from_config({"minecraftSftpHost": "sftp.ex"}).sftp_host == "sftp.ex"


def test_each_server_has_its_own_stats_cache():
    first = stats_cache_for("a.example", 2225)
    first.set("users_usercache.json", ["alice"])

    assert stats_cache_for("a.example", 2225) is first
    assert stats_cache_for("b.example", 2225).get("users_usercache.json") is None

    clear_stats_caches()
    assert first.get("users_usercache.json") is None
