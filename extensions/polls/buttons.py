"""Button-based voting for anonymous and ranked-choice polls."""

import re

from interactions import ActionRow, Button, ButtonStyle, ComponentContext, Embed, component_callback

from features.polls import (
    POLL_EMOJIS,
    Poll,
    PollRepository,
    render_bar,
    tally_first_past_post,
    tally_ranked_choice,
)
from src.discord_ext.embeds import Colors

VOTE_PREFIX = "poll_vote"
RESET_PREFIX = "poll_reset"

_VOTE_RE = re.compile(rf"^{VOTE_PREFIX}:([0-9a-fA-F]+):(\d+)$")
_RESET_RE = re.compile(rf"^{RESET_PREFIX}:([0-9a-fA-F]+)$")


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

    if poll.mode == "ranked" and not poll.closed:
        rows.append(
            ActionRow(
                Button(
                    label="Réinitialiser mon classement",
                    style=ButtonStyle.SECONDARY,
                    custom_id=f"{RESET_PREFIX}:{poll.id}",
                )
            )
        )
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
        embed.color = Colors.WARNING
    return embed


class PollButtonsMixin:
    """Component callbacks for button-based and ranked-choice polls."""

    def _poll_repo(self, guild_id: str | int) -> PollRepository: ...  # supplied by extension

    @component_callback(_VOTE_RE)
    async def on_poll_vote(self, ctx: ComponentContext) -> None:
        match = _VOTE_RE.match(ctx.custom_id)
        if not match or not ctx.guild_id:
            return
        poll_id, option_idx = match.group(1), int(match.group(2))

        repo = self._poll_repo(ctx.guild_id)
        poll = await repo.get_by_message(str(ctx.message.id))
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
                feedback = (
                    f"Cette option est déjà à la position #{existing.index(option_idx) + 1}."
                )
                await ctx.send(feedback, ephemeral=True)
                return
            new_ranking = [*existing, option_idx]
            await repo.set_vote(poll.id, user_id, new_ranking)
            ranking_text = " → ".join(
                f"#{pos + 1} {poll.options[idx]}" for pos, idx in enumerate(new_ranking)
            )
            feedback = f"Classement enregistré : {ranking_text}"
        else:
            new_ranking = [option_idx]
            await repo.set_vote(poll.id, user_id, new_ranking)
            anonymous_note = " (anonyme)" if poll.mode == "anonymous" else ""
            feedback = f"Vote pour **{poll.options[option_idx]}** enregistré{anonymous_note}."

        # Refresh the public embed for non-anonymous polls; anonymous polls keep
        # the same embed view (counts are still public — only voter identities
        # are hidden, since reactions aren't used).
        poll.votes[user_id] = new_ranking
        try:
            embed = ctx.message.embeds[0]
            update_poll_embed(embed, poll)
            await ctx.message.edit(embed=embed)
        except Exception:
            pass
        await ctx.send(feedback, ephemeral=True)

    @component_callback(_RESET_RE)
    async def on_poll_reset(self, ctx: ComponentContext) -> None:
        match = _RESET_RE.match(ctx.custom_id)
        if not match or not ctx.guild_id:
            return
        poll_id = match.group(1)
        repo = self._poll_repo(ctx.guild_id)
        poll = await repo.get_by_message(str(ctx.message.id))
        if poll is None or poll.id != poll_id or poll.closed:
            await ctx.send("Sondage introuvable ou fermé.", ephemeral=True)
            return
        user_id = str(ctx.author.id)
        if user_id not in poll.votes:
            await ctx.send("Vous n'avez pas encore voté.", ephemeral=True)
            return
        await repo.clear_vote(poll.id, user_id)
        poll.votes.pop(user_id, None)
        try:
            embed = ctx.message.embeds[0]
            update_poll_embed(embed, poll)
            await ctx.message.edit(embed=embed)
        except Exception:
            pass
        await ctx.send("Classement réinitialisé.", ephemeral=True)
