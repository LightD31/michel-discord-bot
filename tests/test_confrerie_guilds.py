"""The confrérie extension serves every enabled guild from its own settings."""

from test_confrerie_review import OWNER_ID, FakeCtx, FakeNotion, FakeRepo, _draft

from extensions.confrerie.review import ReviewMixin
from extensions.confrerie.stats import StatsMixin


class FakeMessages:
    def __init__(self) -> None:
        self.kept: list[str] = []

    def keep_only(self, guild_ids):
        self.kept = list(guild_ids)


async def test_statistics_are_aggregated_once_per_notion_database(store_config):
    store_config(
        {
            "servers": {
                "1": {"moduleConfrerie": {"enabled": True, "confrerieNotionDbOeuvresId": "db1"}},
                "2": {"moduleConfrerie": {"enabled": True, "confrerieNotionDbOeuvresId": "db1"}},
                "3": {"moduleConfrerie": {"enabled": True, "confrerieNotionDbOeuvresId": "db2"}},
                "4": {"moduleConfrerie": {"enabled": True}},  # no base: skipped
            }
        }
    )
    fetched: list[str] = []
    updated: list[tuple[str, str]] = []

    class Harness(StatsMixin):
        recap_messages = FakeMessages()

        async def _fetch_statistics(self, db_id):
            fetched.append(db_id)
            return f"stats-{db_id}"

        async def _update_statistics_message(self, guild_id, settings, stats):
            updated.append((guild_id, stats))

    harness = Harness()
    await StatsMixin.confrerie.callback(harness)

    assert fetched == ["db1", "db2"]
    assert updated == [("1", "stats-db1"), ("2", "stats-db1"), ("3", "stats-db2")]
    assert harness.recap_messages.kept == ["1", "2", "3", "4"]


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

        def __init__(self) -> None:
            from test_confrerie_review import FakeBot

            self.bot = FakeBot()

        def _drafts(self, guild_id):
            return repos[str(guild_id)]

    harness = Harness()
    # Guild 8's owner can't decide on guild 9's draft…
    refused = FakeCtx("confrerie_draft:approve:defi-1-2", other_owner)
    await ReviewMixin.on_draft_button.callback(harness, refused)
    assert "propriétaire" in refused.replies[0]

    # …guild 9's owner can, and the page goes to guild 9's base.
    await ReviewMixin.on_draft_button.callback(
        harness, FakeCtx("confrerie_draft:approve:defi-1-2", OWNER_ID)
    )
    assert repos["9"].status("defi-1-2") == "approved"
    assert len(harness.notion_client.created) == 1
