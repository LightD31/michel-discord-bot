"""/suggest, the vote buttons and the staff decisions (T04)."""

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, FakeRole, bare_extension, run
from interactions import Permissions

import extensions.suggestions.buttons as buttons_module
import extensions.suggestions.commands as commands_module
from extensions.suggestions import SuggestionsExtension


class MemorySuggestions:
    def __init__(self):
        self.items = {}

    async def next_id(self):
        return len(self.items) + 1

    async def add(self, suggestion):
        self.items[suggestion.id] = suggestion
        return suggestion

    async def get(self, sugg_id):
        return self.items.get(sugg_id)

    async def set_message(self, sugg_id, message_id):
        self.items[sugg_id].message_id = message_id

    async def update_status(self, sugg_id, status, reason, decided_by):
        item = self.items[sugg_id]
        item.status, item.reason, item.decided_by = status, reason, decided_by

    async def toggle_vote(self, sugg_id, user_id, direction):
        item = self.items.get(sugg_id)
        if item is None or item.status != "pending":
            return None
        if item.votes.get(user_id) == direction:
            del item.votes[user_id]
        else:
            item.votes[user_id] = direction
        return item.model_copy(deep=True)


STAFF_ROLE = 600


@pytest.fixture
def setup(monkeypatch, store_config):
    config = store_config(
        {
            "servers": {
                "1": {
                    "moduleSuggestions": {
                        "enabled": True,
                        "suggestChannelId": "20",
                        "staffRoleId": str(STAFF_ROLE),
                    }
                }
            }
        }
    )

    async def edit(message, **kwargs):
        await message.edit(**kwargs)

    monkeypatch.setattr(commands_module, "edit_message_if_changed", edit)
    monkeypatch.setattr(buttons_module, "edit_message_if_changed", edit)
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    board = guild.add_channel(FakeChannel(20, "suggestions"))
    author = guild.add_member(FakeMember(7, "bob"))
    staff = guild.add_member(FakeMember(3, "modo", roles=[FakeRole(STAFF_ROLE)]))
    repo = MemorySuggestions()
    ctx = FakeCtx(author, channel)
    ext = bare_extension(SuggestionsExtension, ctx.bot, repository=lambda guild_id: repo)
    return ext, ctx, repo, board, staff, config


async def suggest(ext, ctx, text="Un salon musique"):
    await run(SuggestionsExtension.suggest, ext, ctx, text)


async def test_a_suggestion_is_posted_with_its_vote_buttons(setup):
    ext, ctx, repo, board, _, _ = setup
    await suggest(ext, ctx)
    (post,) = board.sent
    (suggestion,) = repo.items.values()
    assert suggestion.message_id == str(post.id) and suggestion.author_id == "7"
    assert post.embeds[0].description == "Un salon musique"
    ids = [b.custom_id for b in post.sent_with["components"][0].components]
    assert ids == ["suggvote:1:up", "suggvote:1:down"]
    assert "#1" in ctx.last


async def test_a_too_long_suggestion_is_refused(setup):
    ext, ctx, repo, board, _, _ = setup
    await suggest(ext, ctx, "x" * 1501)
    assert "trop longue" in ctx.last and board.sent == [] and repo.items == {}


async def test_without_a_board_channel_nothing_is_posted(setup):
    ext, ctx, repo, _, _, config = setup
    del config["servers"]["1"]["moduleSuggestions"]["suggestChannelId"]
    await suggest(ext, ctx)
    assert "Aucun salon" in ctx.last and repo.items == {}


async def click(ext, member, board, direction, sugg_id=1):
    message = board.sent[0]
    ctx = FakeCtx(member, board, message=message, custom_id=f"suggvote:{sugg_id}:{direction}")
    await run(SuggestionsExtension.on_vote_button, ext, ctx)
    return ctx


def votes_shown(board):
    return next(f.value for f in board.sent[0].embeds[0].fields if f.name == "Votes")


async def test_votes_toggle_and_show_on_the_post(setup):
    ext, ctx, repo, board, staff, _ = setup
    await suggest(ext, ctx)
    first = await click(ext, ctx.author, board, "up")
    assert first.last == "Vote 👍 enregistré" and votes_shown(board) == "👍 1 · 👎 0"
    await click(ext, staff, board, "down")
    assert votes_shown(board) == "👍 1 · 👎 1"
    again = await click(ext, ctx.author, board, "up")
    assert again.last == "Vote retiré" and votes_shown(board) == "👍 0 · 👎 1"


async def test_staff_decide_and_votes_close(setup):
    ext, ctx, repo, board, staff, _ = setup
    await suggest(ext, ctx)
    decide = FakeCtx(staff, ctx.channel, bot=ctx.bot)
    await run(SuggestionsExtension.suggestion_approve, ext, decide, 1, reason="Bonne idée")
    suggestion = repo.items[1]
    assert (suggestion.status, suggestion.decided_by, suggestion.reason) == (
        "approved",
        "3",
        "Bonne idée",
    )
    post = board.sent[0]
    assert all(b.disabled for b in post.edits[-1]["components"][0].components)
    assert board.sent[-1].content.startswith("<@7> ta suggestion **#1**")
    late = await click(ext, ctx.author, board, "up")
    assert "clos" in late.last


async def test_only_staff_decide(setup):
    ext, ctx, repo, _, _, _ = setup
    await suggest(ext, ctx)
    await run(SuggestionsExtension.suggestion_deny, ext, ctx, 1)
    assert ctx.last.endswith("Réservé au staff.") and repo.items[1].status == "pending"


async def test_an_admin_counts_as_staff(setup):
    ext, ctx, repo, _, _, _ = setup
    await suggest(ext, ctx)
    ctx.author.guild_permissions = Permissions.ADMINISTRATOR
    await run(SuggestionsExtension.suggestion_implement, ext, ctx, 1)
    assert repo.items[1].status == "implemented"


async def test_deciding_an_unknown_suggestion(setup):
    ext, ctx, _, _, staff, _ = setup
    decide = FakeCtx(staff, ctx.channel, bot=ctx.bot)
    await run(SuggestionsExtension.suggestion_deny, ext, decide, 9)
    assert "#9 introuvable" in decide.last
