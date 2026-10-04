"""Re-attributing playlist tracks stored under a Spotify user id."""

from features.spotify.attribution import (
    is_discord_id,
    reattribution_map,
    unmapped_contributors,
)


def test_discord_ids_are_snowflakes():
    assert is_discord_id("108967780224614400")
    assert not is_discord_id("ilea_music")
    assert not is_discord_id("31abcdefghijklmnopqrstuvwxyz")
    assert not is_discord_id("1112345678")  # old numeric Spotify account


def test_reattribution_map_skips_blank_and_identity_entries():
    mapping = {
        "spotify_a": "111111111111111111",
        "": "222222222222222222",
        "spotify_b": "",
        "333333333333333333": "333333333333333333",
    }
    assert reattribution_map(mapping) == {"spotify_a": "111111111111111111"}


def test_unmapped_contributors_keeps_only_non_discord_ids():
    counts = {"108967780224614400": 22, "tom_spotify": 7}
    assert unmapped_contributors(counts) == {"tom_spotify": 7}
