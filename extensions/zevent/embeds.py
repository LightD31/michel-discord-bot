"""All Zevent embed builders + a size-enforcement helper for the 6000-char limit."""

import os
from datetime import UTC, datetime, timedelta

from interactions import Embed, TimestampStyles, utils

from features.zevent.models import Participant, Show
from features.zevent.stats import is_live, upcoming_goals, upcoming_shows
from src.core import logging as logutil
from src.core.text import take_within_budget

from ._common import (
    GOALS_OFFLINE_FACTOR,
    GOALS_PROGRESS_WEIGHT,
    GOALS_VELOCITY_WEIGHT,
    TWITCH_URL,
    StreamerInfo,
    split_streamer_list,
)

# Cap on planning entries so a full-event listing can't crowd out the rest of
# the message (Discord allows 25 fields / 6000 chars across all embeds).
MAX_PLANNING_ENTRIES = 6
# Discord's ceiling is 6000 characters across every embed in a message; the
# margin absorbs the parts the size estimate cannot see.
EMBED_TOTAL_BUDGET = 5800
# Below this there is no room for a useful roster, so don't strand the embed
# with two names — let it render empty and say so.
MIN_ROSTER_BUDGET = 120
# Same reasoning for the donation-goal leaderboard.
MAX_DONATION_GOALS = 5

logger = logutil.init_logger(os.path.basename(__file__))


def format_euros(amount: float) -> str:
    """Format euros the way the rest of the tracker does: spaces, no decimals."""
    return f"{amount:,.0f} €".replace(",", " ")


def escape_markdown(text: str) -> str:
    """Neutralise the markdown Discord would interpret in free-text values.

    Goal names are written by streamers, so they can carry any of these.
    """
    for char in ("\\", "*", "_", "~", "`", "|"):
        text = text.replace(char, f"\\{char}")
    return text


class EmbedsMixin:
    """Build the main / location / planning / top-donations embeds."""

    def calculate_embed_size(self, embed: Embed) -> int:
        size = 0
        if embed.title:
            size += len(embed.title)
        if embed.description:
            size += len(embed.description)
        if embed.footer and embed.footer.text:
            size += len(embed.footer.text)
        if embed.author and embed.author.name:
            size += len(embed.author.name)

        for field in embed.fields:
            if field.name:
                size += len(field.name)
            if field.value:
                size += len(field.value)

        return size

    def calculate_total_embeds_size(self, embeds: list[Embed]) -> int:
        return sum(self.calculate_embed_size(embed) for embed in embeds)

    def ensure_embeds_fit_limit(
        self, embeds: list[Embed], max_size: int = EMBED_TOTAL_BUDGET
    ) -> list[Embed]:
        """Trim trailing fields / embeds so the message stays under Discord's limit."""
        total_size = self.calculate_total_embeds_size(embeds)

        if total_size <= max_size:
            return embeds

        logger.warning(f"Embeds size ({total_size}) exceeds limit ({max_size}), reducing content")

        reduced_embeds = [embeds[0]]
        remaining_size = max_size - self.calculate_embed_size(embeds[0])

        for embed in embeds[1:]:
            embed_size = self.calculate_embed_size(embed)
            if embed_size <= remaining_size:
                reduced_embeds.append(embed)
                remaining_size -= embed_size
            else:
                if embed.fields and remaining_size > 200:
                    reduced_embed = Embed(
                        title=embed.title, description=embed.description, color=embed.color
                    )
                    if embed.footer and embed.footer.text:
                        reduced_embed.set_footer(embed.footer.text)
                    reduced_embed.timestamp = embed.timestamp

                    for field in embed.fields:
                        field_size = len(field.name or "") + len(field.value or "")
                        if field_size + 50 <= remaining_size:
                            reduced_embed.add_field(
                                name=field.name, value=field.value, inline=field.inline
                            )
                            remaining_size -= field_size
                        else:
                            break

                    if reduced_embed.fields:
                        reduced_embeds.append(reduced_embed)
                break

        logger.info(
            f"Reduced embeds from {total_size} to {self.calculate_total_embeds_size(reduced_embeds)} characters"
        )
        return reduced_embeds

    def create_main_embed(
        self,
        total_amount: str,
        nombre_viewers: str | None = None,
        finished: bool = False,
        concert_status: str | None = None,
    ) -> Embed:
        embed = Embed(title=self._event_title, color=0x59AF37)

        if finished:
            embed.description = f"Total récolté: {total_amount}"
        elif not self._is_event_started():
            event_timestamp = utils.timestamp_converter(self._event_start)
            embed.description = (
                f"🕒 Le concert pré-événement commence {event_timestamp.format(TimestampStyles.RelativeTime)}\n\n"
                f"📅 Concert : {event_timestamp.format(TimestampStyles.LongDateTime)}\n"
                f"📅 Zevent : {utils.timestamp_converter(self._main_event_start).format(TimestampStyles.LongDateTime)}"
            )
        elif concert_status == "concert_live":
            main_event_timestamp = utils.timestamp_converter(self._main_event_start)
            embed.description = (
                f"🎵 **Concert en direct !** 🔴\n"
                f"Total récolté : {total_amount}\n\n"
                f"{f'▶️ [Regarder sur Twitch]({TWITCH_URL})' if TWITCH_URL else ''}\n\n"
                f"🕒 Le Zevent commence {main_event_timestamp.format(TimestampStyles.RelativeTime)}\n"
                f"📅 Début du marathon: {main_event_timestamp.format(TimestampStyles.LongDateTime)}"
            )
        elif not self._is_main_event_started():
            main_event_timestamp = utils.timestamp_converter(self._main_event_start)
            embed.description = (
                f"🕒 Le Zevent commence {main_event_timestamp.format(TimestampStyles.RelativeTime)}\n\n"
                f"📅 Début du marathon: {main_event_timestamp.format(TimestampStyles.LongDateTime)}\n\n"
                f"💰 Total récolté: {total_amount}"
            )
        else:
            embed.description = (
                f"Total récolté: {total_amount}\nViewers cumulés: {nombre_viewers or 'N/A'}"
            )

        embed.timestamp = utils.timestamp_converter(datetime.now())
        embed.set_thumbnail("attachment://Zevent_logo.png")
        embed.set_footer("Source: zevent.fr / Twitch ❤️")

        return embed

    def remaining_embed_budget(
        self, embeds: list[Embed], max_size: int = EMBED_TOTAL_BUDGET
    ) -> int:
        """Characters still free once ``embeds`` are accounted for."""
        return max(max_size - self.calculate_total_embeds_size(embeds), 0)

    def create_location_embed(
        self,
        title: str,
        streams: dict[str, StreamerInfo],
        withlink=True,
        finished=False,
        viewers_count: str | None = None,
        total_count: int | None = None,
        max_chars: int | None = None,
    ) -> Embed:
        actual_count = total_count if total_count is not None else len(streams)

        if finished:
            online_streamers = list(streams.values())
            offline_streamers = []
            status = f"Les {actual_count} {title}"
            withlink = False
        elif not self._is_event_started():
            all_streamers = list(streams.values())
            offline_streamers = []
            status = f"Les {actual_count} {title}"
            online_streamers = all_streamers
        else:
            online_streamers = [s for s in streams.values() if s.is_online]
            offline_streamers = [s for s in streams.values() if not s.is_online]
            status = "Streamers en ligne"

        def render(streamer: StreamerInfo) -> str:
            if withlink:
                return f"[{streamer.display_name}](https://www.twitch.tv/{streamer.twitch_name})"
            return streamer.display_name.replace("_", "\\_")

        # Spend the budget on the groups in order, so a tight message keeps the
        # live streamers and drops offline ones rather than truncating blindly.
        groups: list[tuple[str, list[str]]] = []
        displayed_count = 0
        budget = max_chars if max_chars is not None else None
        for stream_status, streamers in [
            (status, online_streamers),
            ("Hors-ligne", offline_streamers),
        ]:
            if not streamers:
                continue
            names = [render(s) for s in streamers]
            if budget is not None:
                names, spent = take_within_budget(names, budget)
                budget -= spent
            displayed_count += len(names)
            if names:
                groups.append((stream_status, names))

        if "distance" in title and actual_count > displayed_count and not finished:
            embed_title = f"Top {displayed_count}/{actual_count} {title}"
        else:
            embed_title = f"Les {actual_count} {title}"

        embed = Embed(title=embed_title, color=0x59AF37)
        if viewers_count and not finished and self._is_event_started():
            embed.description = f"Viewers: {viewers_count}"
        embed.set_footer("Source: zevent.fr / Twitch ❤️")
        embed.timestamp = utils.timestamp_converter(datetime.now())

        for stream_status, names in groups:
            chunks = split_streamer_list(", ".join(names), max_length=1024)
            for i, chunk in enumerate(chunks, 1):
                field_name = (
                    stream_status if len(chunks) == 1 else f"{stream_status} {i}/{len(chunks)}"
                )
                embed.add_field(name=field_name, value=chunk or "Aucun streamer", inline=True)

        if len(embed.fields) == 0:
            embed.add_field(name="Status", value="Aucun streamer en ce moment", inline=False)

        return embed

    def create_planning_embed(self, shows: list[Show]) -> Embed:
        """Upcoming planning entries, rendered from the stats API's ``shows``."""
        embed = Embed(title="Prochains évènements", color=0x59AF37)
        embed.set_footer("Source: zevent.gdoc.fr ❤️")
        embed.timestamp = utils.timestamp_converter(datetime.now())

        pending = upcoming_shows(shows, datetime.now(UTC), limit=MAX_PLANNING_ENTRIES)

        for show in pending:
            try:
                if show.start is None:
                    continue

                start_ts = utils.timestamp_converter(show.start)
                if show.all_day or show.end is None:
                    time_str = start_ts.format(TimestampStyles.LongDate)
                elif show.end - show.start >= timedelta(minutes=20):
                    time_str = (
                        f"{start_ts.format(TimestampStyles.LongDateTime)} - "
                        f"{utils.timestamp_converter(show.end).format(TimestampStyles.ShortTime)}"
                    )
                else:
                    time_str = start_ts.format(TimestampStyles.LongDateTime)

                field_value = f"{time_str}\n"

                if show.description:
                    field_value += f"{show.description}\n"

                if show.hosts:
                    hosts = ", ".join(name.replace("_", "\\_") for name in show.hosts)
                    field_value += f"Hosts: {hosts}\n"

                if show.guests:
                    guests = [name.replace("_", "\\_") for name in show.guests]
                    if len(guests) > 20:
                        shown = ", ".join(guests[:20])
                        field_value += f"Participants ({len(guests)}): {shown}..."
                    else:
                        field_value += f"Participants: {', '.join(guests)}"

                embed.add_field(name=show.name, value=field_value, inline=True)
            except Exception as e:
                logger.error(f"Error processing show '{show.name}': {e}")

        if not embed.fields:
            embed.add_field(name="Status", value="Aucun évènement à venir", inline=False)

        return embed

    def create_donation_goals_embed(self, participants: list[Participant]) -> Embed | None:
        """Streamers' next donation goals, closest to being reached first.

        Returns ``None`` when nobody has a pending goal, so the caller can drop
        the embed entirely rather than render an empty one.
        """
        live_logins = getattr(self, "_live_logins", None)
        etas = self.goal_etas()
        pending = upcoming_goals(
            participants,
            limit=MAX_DONATION_GOALS,
            progress_weight=GOALS_PROGRESS_WEIGHT,
            offline_factor=GOALS_OFFLINE_FACTOR,
            live_logins=live_logins,
            etas=etas,
            velocity_weight=GOALS_VELOCITY_WEIGHT,
        )
        if not pending:
            return None

        embed = Embed(title="🎯 Prochains donation goals", color=0x59AF37)
        embed.set_footer("Source: zevent.gdoc.fr ❤️")
        embed.timestamp = utils.timestamp_converter(datetime.now())

        # Build up to the 1024-char field limit a whole entry at a time; a
        # blind slice would cut mid-line and leave dangling markdown.
        lines: list[str] = []
        used = 0
        for participant in pending:
            goal = participant.next_goal
            if goal is None:
                continue
            name = escape_markdown(participant.display_name)
            marker = "🔴" if is_live(participant, live_logins) else "⚫"
            remaining = max(goal.amount - participant.amount_raised, 0.0)
            eta = etas.get(participant.twitch_login)
            soon = f" · ~{eta:.0f} min à ce rythme" if eta is not None and eta <= 60 else ""
            entry = (
                f"{marker} **{name}** — {escape_markdown(goal.name)}\n"
                f"　{format_euros(goal.amount)} (reste {format_euros(remaining)}){soon}"
            )
            if used + len(entry) + 1 > 1024:
                break
            lines.append(entry)
            used += len(entry) + 1

        if not lines:
            return None

        embed.add_field(name="À venir", value="\n".join(lines), inline=False)
        return embed

    def create_top_donations_embed(self, streams: list[dict]) -> Embed | None:
        """Leaderboard embed for top streamers by donation amount (top 5, gold theme)."""
        try:
            if not streams:
                return None

            streamers_with_donations = []
            for stream in streams:
                donation_amount = self._safe_get_data(stream, ["donationAmount", "number"], 0)
                if donation_amount > 0:
                    streamers_with_donations.append(
                        {
                            "display": stream.get("display", "Unknown"),
                            "donation_amount": donation_amount,
                            "donation_formatted": self._safe_get_data(
                                stream, ["donationAmount", "formatted"], "0 €"
                            ),
                            "twitch": stream.get("twitch", ""),
                        }
                    )

            top_streamers = sorted(
                streamers_with_donations, key=lambda x: x["donation_amount"], reverse=True
            )

            if not top_streamers:
                return None

            embed = Embed(title="🏆 Top Donations par streamer", color=0xFFD700)
            embed.set_footer("Source: zevent.fr ❤️")
            embed.timestamp = utils.timestamp_converter(datetime.now())

            leaderboard_text = ""
            max_streamers = 5

            for _ in range(3):
                leaderboard_text = ""
                current_top = top_streamers[:max_streamers]

                for i, streamer in enumerate(current_top, 1):
                    if i == 1:
                        medal = "🥇"
                    elif i == 2:
                        medal = "🥈"
                    elif i == 3:
                        medal = "🥉"
                    else:
                        medal = f"{i}."

                    display_name = streamer["display"].replace("_", "\\_")
                    leaderboard_text += (
                        f"{medal} **{display_name}** - {streamer['donation_formatted']}\n"
                    )

                if len(leaderboard_text) <= 1000:
                    break

                max_streamers = max(3, max_streamers - 1)

            embed.add_field(name="Top donations", value=leaderboard_text, inline=False)

            return embed
        except Exception as e:
            logger.error(f"Error creating top donations embed: {e}")
            return None
