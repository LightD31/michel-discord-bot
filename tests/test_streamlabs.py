"""The Streamlabs Charity tracker (extensions/streamlabs.py), from the API's payloads.

``fetch_untrusted`` is replaced by the payloads below (the shape of
streamlabscharity.com's API), so the parsing, the pagination, the live status
and the three embeds are checked without the network.
"""

from types import SimpleNamespace

import pytest
from discord_fakes import FakeMessage

from extensions import streamlabs
from extensions.streamlabs import (
    StreamlabsCharityExtension,
    campaign_api_url,
    members_api_url,
)

TEAM = "https://streamlabscharity.com/teams/@les-copains/zevent"

CAMPAIGN = {
    "id": 42,
    "amount_raised": 1234567,  # cents
    "campaign": {
        "display_name": "Zevent des copains",
        "page_settings": {"description": "On joue pour la bonne cause."},
        "causable": {
            "display_name": "Action contre la Faim",
            "description": "Lutter contre la faim.",
            "avatar": {"url": "https://cdn.example.org/acf.png"},
            "page_settings": {
                "website_url": "https://acf.example.org",
                "twitch": "https://twitch.tv/acf",
                "discord": None,
            },
        },
    },
}


def member(name):
    return {"user": {"display_name": name}}


PAGES = {
    members_api_url(TEAM, 42): {
        "data": [member("Léa_TV"), member("hugo")],
        "next_page_url": "https://streamlabscharity.com/api/v1/teams/42/members?page=2",
    },
    "https://streamlabscharity.com/api/v1/teams/42/members?page=2": {
        "data": [member("Zoé")],
        "next_page_url": None,
    },
}


@pytest.fixture
def api(monkeypatch):
    fetched = []

    async def fetch_untrusted(url, return_type):
        fetched.append(url)
        if url == campaign_api_url(TEAM):
            return CAMPAIGN
        return PAGES[url]

    monkeypatch.setattr(streamlabs, "fetch_untrusted", fetch_untrusted)
    return fetched


class Twitch:
    def __init__(self, live):
        self.live = live
        self.asked = []

    async def get_streams(self, user_login):
        self.asked.append(list(user_login))
        for login, title in self.live.items():
            if login in user_login:
                yield SimpleNamespace(user_login=login, title=title)


def extension(twitch=None):
    ext = object.__new__(StreamlabsCharityExtension)
    ext.twitch = twitch
    return ext


def test_the_api_urls_derive_from_the_team_url():
    # On the team page's own site: a team URL elsewhere is fetched there, untrusted.
    assert (
        campaign_api_url(TEAM) == "https://streamlabscharity.com/api/v1/teams/@les-copains/zevent"
    )
    assert members_api_url(TEAM, 42) == "https://streamlabscharity.com/api/v1/teams/42/members"


async def test_the_members_are_read_across_pages_and_their_lives_marked(api):
    twitch = Twitch({"hugo": "Dernière ligne droite"})
    ext = extension(twitch)

    campaign, members = await ext.fetch_team(TEAM)

    assert campaign["id"] == 42
    assert list(members) == ["léa_tv", "hugo", "zoé"]
    assert members["hugo"] == {
        "display_name": "hugo",
        "slug": "hugo",
        "is_live": True,
        "title": "Dernière ligne droite",
    }
    assert not members["zoé"]["is_live"]
    assert twitch.asked == [["léa_tv", "hugo", "zoé"]]  # one call per 100 logins


async def test_without_twitch_everyone_shows_offline(api):
    _, members = await extension(None).fetch_team(TEAM)
    assert not any(m["is_live"] for m in members.values())


def test_the_campaign_embed_says_the_total_in_euros():
    embed = extension().create_campaign_embed(CAMPAIGN, TEAM)
    assert embed.title == "Zevent des copains"
    assert "12 345,67\xa0€" in embed.description
    assert "Action contre la Faim" in embed.description and embed.url == TEAM


def test_the_cause_embed_links_only_the_networks_it_has():
    embed = extension().create_cause_embed(CAMPAIGN)
    assert "[Twitch](https://twitch.tv/acf)" in embed.description
    assert "Discord" not in embed.description
    assert embed.url == "https://acf.example.org"


def test_live_streamers_are_linked_and_names_escaped():
    members = {
        "léa_tv": {"display_name": "Léa_TV", "slug": "léa_tv", "is_live": True},
        "hugo": {"display_name": "hugo", "slug": "hugo", "is_live": False},
    }
    online, offline = extension().categorize_members(members)
    assert online == ["[Léa\\_TV](https://twitch.tv/léa_tv)"]
    assert offline == ["hugo"]

    embed = extension().create_streamers_embed(["a"], [], members)
    assert [f.name for f in embed.fields] == ["En ligne (1/2)", "Hors ligne (1/2)"]
    assert embed.fields[1].value == "Aucun streamer"


def test_a_long_list_is_split_under_discords_field_limit():
    names = [f"streamer{i:03d}" for i in range(200)]
    chunks = extension().split_members(names)
    assert len(chunks) > 1
    assert all(len(c) <= 1024 for c in chunks)
    assert ", ".join(chunks).split(", ") == names


async def test_the_refresh_fetches_a_shared_team_once(api, monkeypatch, store_config):
    store_config(
        {
            "servers": {
                gid: {
                    "moduleStreamlabsCharity": {
                        "enabled": True,
                        "streamlabsChannelId": "10",
                        "streamlabsTeamUrl": TEAM,
                    }
                }
                for gid in ("1", "2")
            }
        }
    )
    posted = {gid: FakeMessage(content="Initialisation…") for gid in ("1", "2")}

    class Messages:
        def keep_only(self, guild_ids):
            self.kept = guild_ids

        async def get(self, guild_id, settings):
            return posted[guild_id]

    ext = extension(None)
    ext.messages = Messages()

    await StreamlabsCharityExtension.streamlabscharity.callback(ext)

    assert api.count(campaign_api_url(TEAM)) == 1
    for message in posted.values():
        assert [e.title for e in message.embeds] == [
            "Zevent des copains",
            "Action contre la Faim",
            "Participants",
        ]
