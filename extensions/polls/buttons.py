"""Button-based voting for anonymous and ranked-choice polls, and their author's edit."""

import re
from typing import TYPE_CHECKING

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    ComponentContext,
    Embed,
    Modal,
    ParagraphText,
    ShortText,
    component_callback,
)

from features.polls import (
    POLL_EMOJIS,
    Poll,
    PollRepository,
    render_bar,
    tally_first_past_post,
    tally_ranked_choice,
)
from src.core import logging as logutil
from src.discord_ext.look import recolour
from src.discord_ext.messages import copy_embed, edit_message_if_changed, message_of, send_error

logger = logutil.init_logger(__name__)

VOTE_PREFIX = "poll_vote"
RESET_PREFIX = "poll_reset"
EDIT_PREFIX = "poll_edit"
EDIT_FORM = "poll_editform"

_VOTE_RE = re.compile(rf"^{VOTE_PREFIX}:([0-9a-fA-F]+):(\d+)$")
_RESET_RE = re.compile(rf"^{RESET_PREFIX}:([0-9a-fA-F]+)$")
_EDIT_RE = re.compile(rf"^{EDIT_PREFIX}:([0-9a-fA-F]{{24}})$")
EDIT_FORM_RE = re.compile(rf"^{EDIT_FORM}:([0-9a-fA-F]{{24}})$")


def edit_form(poll: Poll) -> Modal:
    """The author's "✏️ Modifier": the question, and the options while nobody has voted."""
    fields: list[ShortText | ParagraphText] = [
        ShortText(
            label="Question",
            custom_id="question",
            value=poll.question,
            required=True,
            max_length=250,
        )
    ]
    if not poll.votes:
        fields.append(
            ParagraphText(
                label="Options, une par ligne",
                custom_id="options",
                value="\n".join(poll.options),
                required=True,
                max_length=1000,
            )
        )
    title = "Modifier le sondage" if not poll.votes else "Modifier la question (déjà des votes)"
    return Modal(*fields, title=title, custom_id=f"{EDIT_FORM}:{poll.id}")


def vote_components(poll: Poll) -> list[ActionRow]:
    """Build button rows for a button-based poll. ≤5 buttons per row, ≤5 rows."""
    rows: list[ActionRow] = []
    buttons: list[Button] = []
    for i, option in enumerate(poll.options):
        label = option if len(option) <= 70 else option[:67] + "…"
        buttons.append(
            Button(
                label=f"{i + 1}. {label}",
                emoji=POLL_EMOJIS[i] if i < len(POLL_EMOJIS) else None,
                style=ButtonStyle.PRIMARY,
                custom_id=f"{VOTE_PREFIX}:{poll.id}:{i}",
                disabled=poll.closed,
            )
        )
        if len(buttons) == 5:
            rows.append(ActionRow(*buttons))
            buttons = []
    if buttons:
        rows.append(ActionRow(*buttons))

    if not poll.closed:
        tools = [
            Button(
                label="Modifier",
                emoji="✏️",
                style=ButtonStyle.SECONDARY,
                custom_id=f"{EDIT_PREFIX}:{poll.id}",
            )
        ]
        if poll.mode == "ranked":
            tools.insert(
                0,
                Button(
                    label="Réinitialiser mon classement",
                    style=ButtonStyle.SECONDARY,
                    custom_id=f"{RESET_PREFIX}:{poll.id}",
                ),
            )
        rows.append(ActionRow(*tools))
    return rows


def render_results_field(poll: Poll) -> tuple[str, str]:
    """Build a (name, value) results field reflecting the poll's current state."""
    voter_count = len(poll.votes)
    if poll.mode == "ranked":
        rounds, winner = tally_ranked_choice(poll.votes, len(poll.options))
        if not rounds:
            return ("Résultats", f"Aucun vote pour le moment ({voter_count} votant(s))")
        last = rounds[-1]
        total = sum(last) or 1
        lines = []
        for i, count in enumerate(last):
            marker = "🏆 " if (winner is not None and i == winner) else ""
            lines.append(
                f"{marker}{POLL_EMOJIS[i] if i < len(POLL_EMOJIS) else '•'} "
                f"**{poll.options[i]}** — {render_bar(count, total)} {count}"
            )
        rounds_note = f"\n*{len(rounds)} tour(s) IRV — {voter_count} votant(s)*"
        return ("Résultats (vote alternatif)", "\n".join(lines) + rounds_note)

    counts = tally_first_past_post(poll.votes, len(poll.options))
    total = sum(counts) or 1
    lines = []
    for i, count in enumerate(counts):
        lines.append(
            f"{POLL_EMOJIS[i] if i < len(POLL_EMOJIS) else '•'} "
            f"**{poll.options[i]}** — {render_bar(count, total)} {count}"
        )
    return ("Résultats", "\n".join(lines) + f"\n*{voter_count} votant(s)*")


_RESULTS_FIELD_NAMES = {"Résultats", "Résultats (vote alternatif)"}


def update_poll_embed(embed: Embed, poll: Poll) -> Embed:
    """Refresh the results field on a poll embed in place."""
    name, value = render_results_field(poll)
    # Update an existing results field in place when present (preserves field
    # ordering and avoids relying on bulk-assignment to embed.fields, which
    # behaves inconsistently across interactions.py versions).
    for field in embed.fields or []:
        if field.name in _RESULTS_FIELD_NAMES:
            field.name = name
            field.value = value
            field.inline = False
            break
    else:
        embed.add_field(name=name, value=value, inline=False)
    if poll.closed:
        recolour(embed, "moduleUtils", "neutral")
    return embed


class PollButtonsMixin:
    """Component callbacks for button-based and ranked-choice polls."""

    if TYPE_CHECKING:

        def _poll_repo(self, guild_id: str | int) -> PollRepository: ...  # the extension's

    @component_callback(_VOTE_RE)
    async def on_poll_vote(self, ctx: ComponentContext) -> None:
        match = _VOTE_RE.match(ctx.custom_id)
        if not match or not ctx.guild_id:
            return
        poll_id, option_idx = match.group(1), int(match.group(2))

        repo = self._poll_repo(ctx.guild_id)
        poll = await repo.get_by_message(str(message_of(ctx).id))
        if poll is None or poll.id != poll_id:
            await ctx.send("Sondage introuvable.", ephemeral=True)
            return
        if poll.closed:
            await ctx.send("Ce sondage est fermé.", ephemeral=True)
            return
        if option_idx >= len(poll.options):
            return

        user_id = str(ctx.author.id)
        existing = poll.votes.get(user_id, [])

        if poll.mode == "ranked":
            if option_idx in existing:
                feedback = f"Cette option est déjà à la position #{existing.index(option_idx) + 1}."
                await ctx.send(feedback, ephemeral=True)
                return
            updated = await repo.add_to_ranking(poll_id, user_id, option_idx)
        else:
            updated = await repo.set_vote(poll_id, user_id, [option_idx])
        if updated is None:  # closed between the read and the vote
            await ctx.send("Ce sondage est fermé.", ephemeral=True)
            return
        poll = updated
        new_ranking = poll.votes.get(user_id, [])
        if poll.mode == "ranked":
            ranking_text = " → ".join(
                f"#{pos + 1} {poll.options[idx]}" for pos, idx in enumerate(new_ranking)
            )
            feedback = f"Classement enregistré : {ranking_text}"
        else:
            anonymous_note = " (anonyme)" if poll.mode == "anonymous" else ""
            feedback = f"Vote pour **{poll.options[option_idx]}** enregistré{anonymous_note}."

        # Refresh the public embed for non-anonymous polls; anonymous polls keep
        # the same embed view (counts are still public — only voter identities
        # are hidden, since reactions aren't used).
        await self._refresh_poll_embed(ctx, poll)
        await ctx.send(feedback, ephemeral=True)

    @staticmethod
    async def _refresh_poll_embed(ctx: ComponentContext, poll: Poll) -> None:
        """Show the new counts on the poll message; the vote is saved either way."""
        if not message_of(ctx).embeds:
            logger.warning("Poll %s: its message has no embed to refresh", poll.id)
            return
        embed = copy_embed(message_of(ctx).embeds[0])
        update_poll_embed(embed, poll)
        try:
            await edit_message_if_changed(message_of(ctx), embed=embed, logger=logger)
        except Exception as e:  # noqa: BLE001 — saved anyway; the next vote catches up
            logger.warning("Poll %s: could not refresh its message: %s", poll.id, e)

    @component_callback(_EDIT_RE)
    async def on_poll_edit(self, ctx: ComponentContext) -> None:
        """Open the edit form, for the poll's author only."""
        match = _EDIT_RE.match(ctx.custom_id)
        if not match or not ctx.guild_id:
            return
        poll = await self._poll_repo(ctx.guild_id).get_by_message(str(message_of(ctx).id))
        if poll is None or poll.id != match[1] or poll.closed:
            await send_error(ctx, "Ce sondage est fermé.")
            return
        if poll.author_id != str(ctx.author.id):
            await send_error(
                ctx, "Seule la personne qui a lancé ce sondage peut le modifier.", icon="🔒"
            )
            return
        await ctx.send_modal(edit_form(poll))

    @component_callback(_RESET_RE)
    async def on_poll_reset(self, ctx: ComponentContext) -> None:
        match = _RESET_RE.match(ctx.custom_id)
        if not match or not ctx.guild_id:
            return
        poll_id = match.group(1)
        repo = self._poll_repo(ctx.guild_id)
        poll = await repo.get_by_message(str(message_of(ctx).id))
        if poll is None or poll.id != poll_id or poll.closed:
            await ctx.send("Sondage introuvable ou fermé.", ephemeral=True)
            return
        user_id = str(ctx.author.id)
        if user_id not in poll.votes:
            await ctx.send("Tu n'as pas encore voté.", ephemeral=True)
            return
        updated = await repo.clear_vote(poll_id, user_id)
        if updated is None:
            await ctx.send("Sondage introuvable ou fermé.", ephemeral=True)
            return
        await self._refresh_poll_embed(ctx, updated)
        await ctx.send("Classement réinitialisé.", ephemeral=True)
