"""/suggerer, the vote buttons and the staff decisions (T04)."""

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

    async def set_thread(self, sugg_id, thread_id):
        self.items[sugg_id].thread_id = thread_id

    async def list_in(self, statuses, limit):
        found = [s for s in self.items.values() if s.status in statuses]
        return sorted(found, key=lambda s: -s.id)[:limit]

    async def decide(self, sugg_id, status, reason, decided_by, *, from_statuses):
        item = self.items.get(sugg_id)
        if item is None or item.status not in from_statuses:
            return None
        item.status, item.reason, item.decided_by = status, reason, decided_by
        return item.model_copy(deep=True)

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


async def suggest(ext, ctx, text="Un salon musique", title="Musique"):
    """``/suggerer`` opens its form; the member sends it."""
    await run(SuggestionsExtension.suggest, ext, ctx)
    (form,) = ctx.modals[-1:]
    assert form.custom_id == "suggest_new"
    submit = FakeCtx(ctx.author, ctx.channel, bot=ctx.bot, custom_id=form.custom_id)
    await run(SuggestionsExtension.on_new_form, ext, submit, title=title, text=text)
    ctx.replies.extend(submit.replies)
    return submit


async def test_a_suggestion_is_posted_with_its_vote_buttons(setup):
    ext, ctx, repo, board, _, _ = setup
    await suggest(ext, ctx)
    (post,) = board.sent
    (suggestion,) = repo.items.values()
    assert suggestion.message_id == str(post.id) and suggestion.author_id == "7"
    assert post.embeds[0].description == "Un salon musique"
    assert post.embeds[0].title == "Suggestion #1 · Musique"
    votes, staff_row = post.sent_with["components"]
    assert [b.custom_id for b in votes.components] == ["suggvote:1:up", "suggvote:1:down"]
    assert [b.custom_id for b in staff_row.components] == [
        "suggdecide:1:approved",
        "suggdecide:1:denied",
        "suggdecide:1:implemented",
    ]
    assert "#1" in ctx.last


async def test_a_discussion_thread_opens_on_the_post(setup):
    ext, ctx, repo, board, _, config = setup
    await suggest(ext, ctx)
    thread = board.guild.channels[board.sent[0].id]
    assert thread.name == "Discussion #1 · Musique"
    assert repo.items[1].thread_id == str(thread.id)
    config["servers"]["1"]["moduleSuggestions"]["discussionThreads"] = False
    await suggest(ext, ctx, title="Autre")
    assert repo.items[2].thread_id is None


async def test_a_too_long_suggestion_is_refused(setup):
    ext, ctx, repo, board, _, _ = setup
    await suggest(ext, ctx, "x" * 1501)
    assert "trop longue" in ctx.last and board.sent == [] and repo.items == {}


async def test_without_a_board_channel_nothing_is_posted(setup):
    ext, ctx, repo, _, _, config = setup
    del config["servers"]["1"]["moduleSuggestions"]["suggestChannelId"]
    await suggest(ext, ctx)
    assert "Aucun salon" in ctx.last and "tableau de bord" in ctx.last and repo.items == {}


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
    votes, staff_row = post.edits[-1]["components"]
    assert all(b.disabled for b in votes.components)
    # Approved, it can still be marked implemented.
    assert [b.custom_id for b in staff_row.components] == ["suggdecide:1:implemented"]
    # The author hears it by DM; the channel isn't pinged (B5).
    assert ctx.author.dms[-1].startswith(
        "💡 Ta suggestion **#1** « Musique » sur **Serveur** est approuvée."
    )
    assert "Motif : Bonne idée" in ctx.author.dms[-1]
    assert len(board.sent) == 1
    late = await click(ext, ctx.author, board, "up")
    assert "clos" in late.last


async def test_a_staff_button_asks_for_a_reason_then_decides(setup):
    ext, ctx, repo, board, staff, _ = setup
    await suggest(ext, ctx)
    thread = board.guild.channels[board.sent[0].id]
    button = FakeCtx(staff, board, bot=ctx.bot, custom_id="suggdecide:1:denied")
    await run(SuggestionsExtension.on_decide_button, ext, button)
    (form,) = button.modals
    assert form.custom_id == "suggreason:1:denied" and form.title == "Refuser la suggestion #1"
    submit = FakeCtx(staff, board, bot=ctx.bot, custom_id=form.custom_id)
    await run(SuggestionsExtension.on_reason_form, ext, submit, reason="Déjà fait")
    assert repo.items[1].status == "denied"
    assert thread.sent[-1].content == "❌ Refusée par modo : Déjà fait"
    assert thread.archived and thread.locked
    # Refused: no decision left on the card.
    assert len(board.sent[0].edits[-1]["components"]) == 1
    # A second moderator, a moment later, changes nothing.
    again = FakeCtx(staff, board, bot=ctx.bot, custom_id="suggreason:1:approved")
    await run(SuggestionsExtension.on_reason_form, ext, again)
    assert again.last.startswith("❌ La suggestion **#1** est déjà ❌ Refusée")
    assert repo.items[1].status == "denied"


async def test_a_member_clicking_a_staff_button_is_told_who_can(setup):
    ext, ctx, repo, board, _, _ = setup
    await suggest(ext, ctx)
    button = FakeCtx(ctx.author, board, bot=ctx.bot, custom_id="suggdecide:1:approved")
    await run(SuggestionsExtension.on_decide_button, ext, button)
    assert button.modals == [] and button.last.startswith("🔒 Seuls le rôle")
    assert repo.items[1].status == "pending"


async def test_the_staff_autocomplete_lists_what_is_left_to_decide(setup):
    from extensions.suggestions.commands import open_choices

    ext, ctx, repo, _, staff, _ = setup
    for title in ("Musique", "Tournoi", "Emojis"):
        await suggest(ext, ctx, title=title)
    decide = FakeCtx(staff, ctx.channel, bot=ctx.bot)
    await run(SuggestionsExtension.suggestion_deny, ext, decide, 2)
    found = await repo.list_in(["pending", "approved"], 100)
    assert open_choices(found, "") == [
        {"name": "#3 · Emojis", "value": 3},
        {"name": "#1 · Musique", "value": 1},
    ]
    assert open_choices(found, "#1") == [{"name": "#1 · Musique", "value": 1}]
    assert open_choices(found, "emo") == [{"name": "#3 · Emojis", "value": 3}]


async def test_only_staff_decide(setup):
    ext, ctx, repo, _, _, _ = setup
    await suggest(ext, ctx)
    await run(SuggestionsExtension.suggestion_deny, ext, ctx, 1)
    # Says who can (B9): the staff role, named, and the administrators.
    assert ctx.last == (
        f"🔒 Seuls le rôle <@&{STAFF_ROLE}> et les administrateurs peuvent trancher une suggestion."
    )
    assert repo.items[1].status == "pending"


async def test_without_a_staff_role_the_refusal_says_admins_only(setup):
    ext, ctx, repo, _, _, config = setup
    del config["servers"]["1"]["moduleSuggestions"]["staffRoleId"]
    await suggest(ext, ctx)
    await run(SuggestionsExtension.suggestion_deny, ext, ctx, 1)
    assert "Seuls les administrateurs" in ctx.last and "aucun rôle staff" in ctx.last


def test_the_staff_commands_are_hidden_from_members():
    assert SuggestionsExtension.suggestion_approve.default_member_permissions == (
        Permissions.MANAGE_MESSAGES
    )
    assert SuggestionsExtension.suggest.default_member_permissions is None
    for command in (
        SuggestionsExtension.suggestion_approve,
        SuggestionsExtension.suggestion_deny,
        SuggestionsExtension.suggestion_implement,
    ):
        assert "id" in command.autocomplete_callbacks


async def test_a_message_becomes_a_suggestion_that_links_back(setup):
    from discord_fakes import FakeMessage

    ext, ctx, repo, board, _, _ = setup
    ctx.target = FakeMessage(500, content="Et si on faisait un tournoi ?", channel=ctx.channel)
    await run(SuggestionsExtension.suggest_from_message, ext, ctx)
    (form,) = ctx.modals
    assert form.custom_id == "suggest_from:10:500"
    assert form.components[1].value == "Et si on faisait un tournoi ?"
    submit = FakeCtx(ctx.author, ctx.channel, bot=ctx.bot, custom_id=form.custom_id)
    await run(
        SuggestionsExtension.on_suggest_form,
        ext,
        submit,
        title="Tournoi",
        text="Un tournoi Mario Kart",
    )
    (suggestion,) = repo.items.values()
    assert suggestion.text == "Un tournoi Mario Kart"
    assert suggestion.source_url.endswith("/1/10/500")
    fields = {f.name: f.value for f in board.sent[0].embeds[0].fields}
    assert fields["D'après"] == f"[ce message]({suggestion.source_url})"
    assert "publiée dans <#20>" in submit.last


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
