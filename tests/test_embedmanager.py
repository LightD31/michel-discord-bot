"""Embed manager rendering and publishing (``src/discord_ext/embedmanager.py``)."""

from types import SimpleNamespace

import src.discord_ext.embedmanager as embedmanager
from src.discord_ext.embedmanager import build_embeds, publish_embeds, shorten_links_config


def test_build_embeds_renders_titles_colors_and_links():
    (embed,) = build_embeds(
        [
            {
                "title": "Liens utiles",
                "color": "#ff0000",
                "links": [
                    {"title": "Wiki", "url": "https://wiki.example.org"},
                    {"title": "Sans URL", "url": ""},
                    "pas un lien",
                ],
            }
        ]
    )
    assert embed.title == "Liens utiles"
    assert embed.to_dict()["color"] == 0xFF0000
    assert [(f.name, f.value) for f in embed.fields] == [
        ("Wiki", "[Cliquez ici](https://wiki.example.org)")
    ]


def test_build_embeds_skips_bad_entries_and_defaults_bad_colors():
    embeds = build_embeds(["pas un embed", {"color": "zz"}, {"title": "Bleu"}])
    assert [e.title for e in embeds] == ["Sans titre", "Bleu"]
    assert all(e.to_dict()["color"] == 0x3498DB for e in embeds)
    assert all(not e.fields for e in embeds)


async def test_each_link_gets_a_short_url_slot_keyed_by_guild_embed_and_link(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_shorten_keyed(key, url, **_):
        calls.append((key, url))
        return f"https://s.example/{len(calls)}"

    monkeypatch.setattr(embedmanager, "shorten_keyed", fake_shorten_keyed)
    config = [
        {"title": "Liens", "links": [{"title": "Wiki", "url": "https://w"}, {"url": "https://x"}]},
        {"links": [{"title": "Vide", "url": ""}]},
        "pas un embed",
    ]

    shortened = await shorten_links_config(config, "42")

    assert calls == [("42-Liens-Wiki", "https://w"), ("42-Liens-lien-2", "https://x")]
    assert shortened[0]["links"] == [
        {"title": "Wiki", "url": "https://s.example/1"},
        {"url": "https://s.example/2"},
    ]
    assert shortened[1:] == config[1:]
    assert config[0]["links"][0]["url"] == "https://w"  # the input is left untouched


async def test_publish_edits_the_persistent_message_with_short_links(monkeypatch):
    message = SimpleNamespace(id=7)
    fetched: dict = {}
    edited: dict = {}

    async def fake_fetch(bot, **kwargs):
        fetched.update(kwargs)
        return message

    async def fake_edit(msg, **kwargs):
        edited.update(kwargs, message=msg)
        return True

    async def fake_shorten_keyed(key, url, **_):
        return "https://s.example/a"

    monkeypatch.setattr(embedmanager, "fetch_or_create_persistent_message", fake_fetch)
    monkeypatch.setattr(embedmanager, "edit_message_if_changed", fake_edit)
    monkeypatch.setattr(embedmanager, "shorten_keyed", fake_shorten_keyed)
    guild_config = {
        "channelId": "100",
        "pinMessage": True,
        "embeds": [{"title": "Liens", "links": [{"title": "Wiki", "url": "https://w"}]}],
    }

    assert await publish_embeds(object(), "42", guild_config) is True

    assert fetched["channel_id"] == "100"
    assert fetched["slot"] == "embedmanager"  # tracked in bot_messages, not in the config
    assert fetched["guild_id"] == "42"
    assert fetched["pin"] is True
    assert edited["message"] is message
    assert edited["content"] == ""
    assert edited["embeds"][0].fields[0].value == "[Cliquez ici](https://s.example/a)"


async def test_publish_reports_a_missing_message(monkeypatch):
    async def fake_fetch(bot, **kwargs):
        return None

    async def never_edit(*args, **kwargs):
        raise AssertionError("nothing to edit")

    monkeypatch.setattr(embedmanager, "fetch_or_create_persistent_message", fake_fetch)
    monkeypatch.setattr(embedmanager, "edit_message_if_changed", never_edit)

    assert await publish_embeds(object(), "42", {"channelId": "100", "embeds": []}) is False
