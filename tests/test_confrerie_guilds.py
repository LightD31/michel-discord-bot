"""The confrérie extension serves every enabled guild from its own settings."""

from test_confrerie_review import OWNER_ID, FakeCtx, FakeNotion, FakeRepo, _draft

from extensions.confrerie.review import ReviewMixin
from extensions.confrerie.stats import StatsMixin


class FakeMessages:
    def __init__(self) -> None:
        self.kept: list[str] = []

    def keep_only(self, guild_ids):
        self.kept = list(guild_ids)


def confrerie(db=None, token="tok"):
    settings = {"enabled": True, "notionSecret": token}
    return {"moduleConfrerie": {**settings, "confrerieNotionDbOeuvresId": db} if db else settings}


async def test_statistics_are_aggregated_once_per_notion_database(store_config):
    store_config(
        {
            "servers": {
                "1": confrerie("db1"),
                "2": confrerie("db1"),
                "3": confrerie("db2"),
                "4": confrerie(),  # no base: skipped
                "5": confrerie("db1", token=""),  # no Notion token of its own (P37): skipped
                "6": confrerie("db1", token="other"),  # another integration: its own read
            }
        }
    )
    fetched: list[str] = []
    updated: list[tuple[str, str]] = []

    class Harness(StatsMixin):
        recap_messages = FakeMessages()

        async def _fetch_statistics(self, settings, db_id):
            fetched.append(f"{settings['notionSecret']}/{db_id}")
            return f"stats-{db_id}"

        async def _update_statistics_message(self, guild_id, settings, stats):
            updated.append((guild_id, stats))

    harness = Harness()
    await StatsMixin.confrerie.callback(harness)

    assert fetched == ["tok/db1", "tok/db2", "other/db1"]
    assert updated == [
        ("1", "stats-db1"),
        ("2", "stats-db1"),
        ("3", "stats-db2"),
        ("6", "stats-db1"),
    ]
    assert harness.recap_messages.kept == ["1", "2", "3", "4", "5", "6"]


async def test_a_review_from_a_dm_finds_the_draft_in_its_guild(store_config):
    other_owner = 777
    store_config(
        {
            "config": {"discord": {}},
            "servers": {
                "8": {"moduleConfrerie": {"enabled": True, "confrerieOwnerId": str(other_owner)}},
                "9": {
                    "moduleConfrerie": {
                        "enabled": True,
                        "confrerieOwnerId": str(OWNER_ID),
                        "confrerieNotionDbOeuvresId": "db",
                    }
                },
            },
        }
    )
    repos = {"8": FakeRepo(), "9": FakeRepo(_draft())}

    class Harness(ReviewMixin):
        notion_client = FakeNotion()

        def notion(self, settings):
            return self.notion_client

        def __init__(self) -> None:
            from test_confrerie_review import FakeBot

            self.bot = FakeBot()

        def _drafts(self, guild_id):
            return repos[str(guild_id)]

    harness = Harness()
    # Guild 8's owner can't decide on guild 9's draft…
    refused = FakeCtx("confrerie_draft:approve:defi-1-2", other_owner)
    await ReviewMixin.on_draft_button.callback(harness, refused)
    assert "propriétaire" in refused.texts[0]

    # …guild 9's owner can, and the page goes to guild 9's base.
    await ReviewMixin.on_draft_button.callback(
        harness, FakeCtx("confrerie_draft:approve:defi-1-2", OWNER_ID)
    )
    assert repos["9"].status("defi-1-2") == "approved"
    assert len(harness.notion_client.created) == 1


def test_each_server_reads_notion_with_its_own_integration():
    """P37: no token, no client (members are told); one client per token."""
    import pytest

    from extensions.confrerie import ConfrerieExtension, NotionNotConnected

    ext = object.__new__(ConfrerieExtension)
    ext._notion_clients = {}
    with pytest.raises(NotionNotConnected):
        ext.notion({"notionSecret": " "})
    first = ext.notion({"notionSecret": "a"})
    assert ext.notion({"notionSecret": "a"}) is first
    assert ext.notion({"notionSecret": "b"}) is not first


async def test_the_recap_is_a_v2_card_carrying_its_notion_link(monkeypatch):
    """P40: a V2 card has no text beside it, so the link closes the card itself."""
    from interactions import MessageFlags

    import extensions.confrerie.stats as stats_mod
    from features.confrerie.stats import ConfrerieStats

    edits: list = []

    async def fake_edit(message, **kwargs):
        edits.append(kwargs)

    async def keyed(key, url, **kwargs):
        return url

    class Messages:
        async def get(self, guild_id, settings):
            return object()

    monkeypatch.setattr(stats_mod, "edit_message_if_changed", fake_edit)
    monkeypatch.setattr(stats_mod, "shorten_keyed", keyed)

    class Harness(StatsMixin):
        recap_messages = Messages()

        def _footer_icon(self, guild_id):
            return None

    settings = {"confrerieTextsUrl": "https://notion.example/textes"}
    await Harness()._update_statistics_message("1", settings, ConfrerieStats(total_works=3))
    ((kwargs),) = edits
    assert kwargs["flags"] == MessageFlags.IS_COMPONENTS_V2 and "content" not in kwargs
    texts = " ".join(getattr(c, "content", "") for c in kwargs["components"][0].components)
    assert "https://notion.example/textes" in texts
