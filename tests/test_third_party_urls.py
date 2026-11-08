"""Third-party endpoints come from the config, and an unset one pauses its feature.

ZUnivers (global ``zunivers``), Raider.IO (``raiderio``), olympics.com
(``olympics``), the official Zevent API (``zevent.zeventApiUrl``), and
Streamlabs Charity, whose API is found on the site of the team URL a guild set.
"""

from types import SimpleNamespace

import pytest

from features.coloc.api_client import ZuniversAPIClient, ZuniversAPIError
from features.coloc.constants import (
    ADVENT_CALENDAR_PAGE,
    CORPORATION_PATH,
    format_calendar_link,
    zunivers_link,
)
from src.core.errors import IntegrationError

# ── ZUnivers ─────────────────────────────────────────────────────────────


def test_the_zunivers_client_reads_its_base_url_at_each_request():
    base = {"url": "https://zu.test/public/"}
    client = ZuniversAPIClient(lambda: base["url"])

    assert client._url(CORPORATION_PATH, corp_id="c1") == "https://zu.test/public/corporation/c1"
    base["url"] = "https://other.test/api"
    assert client._url("event/current") == "https://other.test/api/event/current"


async def test_the_zunivers_client_refuses_to_guess_an_unset_api():
    client = ZuniversAPIClient(lambda: None)

    with pytest.raises(ZuniversAPIError, match=r"zunivers\.apiUrl"):
        await client.get_corporation("c1")


def test_zunivers_links_need_the_site_url():
    assert (
        zunivers_link("https://zu.test/", ADVENT_CALENDAR_PAGE, username="jean")
        == "https://zu.test/calendrier-festif/jean"
    )
    assert zunivers_link(None, ADVENT_CALENDAR_PAGE, username="jean") is None
    assert format_calendar_link("https://zu.test/c") == "[calendrier festif](https://zu.test/c)"
    assert format_calendar_link(None) == "calendrier festif"


@pytest.mark.parametrize(
    ("site", "url"), [("https://zu.test", "https://zu.test/corporation/42"), (None, None)]
)
def test_the_corporation_recap_links_to_its_page_when_the_site_is_known(site, url):
    from extensions.zunivers.embeds import create_corporation_embed

    data = {"id": 42, "name": "Les", "description": "d", "logoUrl": "https://l", "balance": 1}
    assert create_corporation_embed(data, "💰", site).url == url


# ── Raider.IO (MDI) ──────────────────────────────────────────────────────


async def test_mdi_brackets_are_read_from_the_configured_api(monkeypatch):
    import features.mdi.client as client

    client.invalidate_cache()
    fetched: list[str] = []

    async def fake_fetch(url, return_type):
        fetched.append(url)
        return {"brackets": [], "bracket": None}

    monkeypatch.setattr(client, "fetch", fake_fetch)

    await client.list_brackets("https://rio.test/api/", "mdi-s1")
    await client.get_bracket_matches("https://rio.test/api", "mdi-s1", "upper")

    assert fetched == [
        "https://rio.test/api/events/mdi-s1/brackets",
        "https://rio.test/api/events/mdi-s1/brackets/upper",
    ]
    client.invalidate_cache()


async def test_mdi_stays_idle_without_an_api_url(store_config, monkeypatch):
    import extensions.mdi.api as api
    from extensions.mdi._common import GuildConfig

    store_config({"config": {}, "servers": {}})

    async def must_not_fetch(*args):
        raise AssertionError("Raider.IO was called")

    monkeypatch.setattr(api, "list_brackets", must_not_fetch)

    team, matches, brackets = await api.ApiMixin()._collect_team_matches(GuildConfig.from_dict({}))
    assert (team, matches, brackets) == (None, [], [])


async def test_the_mdi_event_link_follows_the_configured_site(store_config, monkeypatch):
    import extensions.mdi.notifications as notifications

    async def no_shortener(url, **kwargs):
        return url

    monkeypatch.setattr(notifications, "shorten_url", no_shortener)

    store_config({"config": {"raiderio": {"webUrl": "https://rio.test/"}}})
    assert await notifications._event_url("mdi-s1") == "https://rio.test/events/mdi-s1"

    store_config({"config": {"raiderio": {"apiUrl": "https://rio.test/api"}}})
    assert await notifications._event_url("mdi-s1") is None


# ── olympics.com ─────────────────────────────────────────────────────────


def test_olympics_headers_name_the_configured_page_and_its_origin(store_config):
    from extensions.olympics._common import olympics_headers

    page = "https://www.olympics.test/fr/games/medals"
    store_config({"config": {"olympics": {"apiUrl": "https://x", "refererUrl": page}}})
    headers = olympics_headers()
    assert headers["Referer"] == page
    assert headers["Origin"] == "https://www.olympics.test"

    store_config({"config": {"olympics": {"apiUrl": "https://x"}}})
    assert "Referer" not in olympics_headers() and "Origin" not in olympics_headers()


async def test_olympics_requests_need_an_api_url(store_config):
    from extensions.olympics import OlympicsExtension

    store_config({"config": {}})
    with pytest.raises(IntegrationError, match=r"olympics\.apiUrl"):
        await object.__new__(OlympicsExtension)._olympics_fetch("medals")


async def test_olympics_polling_waits_for_an_api_url(store_config):
    from extensions.olympics import OlympicsExtension

    store_config({"servers": {"1": {"moduleOlympics": {"enabled": True}}}})
    ext = object.__new__(OlympicsExtension)

    async def must_not_fetch():
        raise AssertionError("polled without an API URL")

    ext._fetch_france_medals = must_not_fetch

    await OlympicsExtension.check_medals.callback(ext)


# ── Zevent ───────────────────────────────────────────────────────────────


async def test_zevent_renders_without_data_when_its_api_is_unset(monkeypatch):
    import extensions.zevent.tasks as tasks
    from extensions.zevent import Zevent

    monkeypatch.setattr(tasks, "API_URL", "")
    monkeypatch.setattr(tasks, "STREAMLABS_API_URL", "")

    async def must_not_fetch(*args, **kwargs):
        raise AssertionError("an API was called")

    monkeypatch.setattr(tasks, "fetch", must_not_fetch)

    assert await object.__new__(Zevent)._fetch_payloads() == (None, None)


# ── Streamlabs Charity ───────────────────────────────────────────────────


def test_the_streamlabs_api_is_found_on_the_team_page_site():
    import extensions.streamlabs as sl

    team = "https://streamlabscharity.com/teams/@les-teams/campagne"
    assert sl.campaign_api_url(team) == (
        "https://streamlabscharity.com/api/v1/teams/@les-teams/campagne"
    )
    assert sl.members_api_url(team, 123) == (
        "https://streamlabscharity.com/api/v1/teams/123/members"
    )


async def test_streamlabs_members_are_fetched_as_untrusted_urls(monkeypatch):
    import extensions.streamlabs as sl

    fetched: list[str] = []
    pages = {
        "https://sl.test/api/v1/teams/7/members": {
            "data": [{"user": {"display_name": "Alice"}}],
            "next_page_url": "https://sl.test/api/v1/teams/7/members?page=2",
        },
        "https://sl.test/api/v1/teams/7/members?page=2": {
            "data": [{"user": {"display_name": "Bob"}}],
        },
    }

    async def fake_untrusted(url, return_type):
        fetched.append(url)
        return pages[url]

    monkeypatch.setattr(sl, "fetch_untrusted", fake_untrusted)
    ext = object.__new__(sl.StreamlabsCharityExtension)

    members = await ext.fetch_members_data("https://sl.test/teams/@a/x", 7)

    assert list(members) == ["alice", "bob"]
    assert fetched == list(pages)


# ── Dashboard ────────────────────────────────────────────────────────────


def test_the_dashboard_has_no_default_public_url(store_config, monkeypatch):
    import src.webui.app as webui_app

    errors: list[str] = []
    monkeypatch.setattr(
        webui_app, "logger", SimpleNamespace(error=lambda msg, *a: errors.append(msg % a))
    )
    store_config({"config": {"webui": {"clientId": "1", "clientSecret": "s"}}, "servers": {}})

    webui_app.create_app()

    assert any("webui.baseUrl" in line for line in errors)
