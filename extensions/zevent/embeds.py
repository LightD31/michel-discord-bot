"""All Zevent embed builders + a size-enforcement helper for the 6000-char limit."""

import os
from datetime import UTC, datetime, timedelta

from interactions import Embed, TimestampStyles, utils

from features.zevent.models import Show
from features.zevent.stats import upcoming_shows
from src.core import logging as logutil

from ._common import (
    EVENT_START_DATE,
    MAIN_EVENT_START_DATE,
    TWITCH_URL,
    StreamerInfo,
    split_streamer_list,
)

# Cap on planning entries so a full-event listing can't crowd out the rest of
# the message (Discord allows 25 fields / 6000 chars across all embeds).
MAX_PLANNING_ENTRIES = 6

logger = logutil.init_logger(os.path.basename(__file__))


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

    def ensure_embeds_fit_limit(self, embeds: list[Embed], max_size: int = 5800) -> list[Embed]:
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
            event_timestamp = utils.timestamp_converter(EVENT_START_DATE)
            embed.description = (
                f"🕒 Le concert pré-événement commence {event_timestamp.format(TimestampStyles.RelativeTime)}\n\n"
                f"📅 Concert : {event_timestamp.format(TimestampStyles.LongDateTime)}\n"
                f"📅 Zevent : {utils.timestamp_converter(MAIN_EVENT_START_DATE).format(TimestampStyles.LongDateTime)}"
            )
        elif concert_status == "concert_live":
            main_event_timestamp = utils.timestamp_converter(MAIN_EVENT_START_DATE)
            embed.description = (
                f"🎵 **Concert en direct !** 🔴\n"
                f"Total récolté : {total_amount}\n\n"
                f"{f'▶️ [Regarder sur Twitch]({TWITCH_URL})' if TWITCH_URL else ''}\n\n"
                f"🕒 Le Zevent commence {main_event_timestamp.format(TimestampStyles.RelativeTime)}\n"
                f"📅 Début du marathon: {main_event_timestamp.format(TimestampStyles.LongDateTime)}"
            )
        elif not self._is_main_event_started():
            main_event_timestamp = utils.timestamp_converter(MAIN_EVENT_START_DATE)
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

    def create_location_embed(
        self,
        title: str,
        streams: dict[str, StreamerInfo],
        withlink=True,
        finished=False,
        viewers_count: str | None = None,
        total_count: int | None = None,
    ) -> Embed:
        displayed_count = len(streams)
        actual_count = total_count if total_count is not None else displayed_count

        if "distance" in title and actual_count > displayed_count and not finished:
            embed_title = f"Top {displayed_count}/{actual_count} {title}"
        else:
            embed_title = f"Les {actual_count} {title}"

        embed = Embed(title=embed_title, color=0x59AF37)

        if viewers_count and not finished and self._is_event_started():
            embed.description = f"Viewers: {viewers_count}"

        embed.set_footer("Source: zevent.fr / Twitch ❤️")
        embed.timestamp = utils.timestamp_converter(datetime.now())

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

        for stream_status, streamers in [
            (status, online_streamers),
            ("Hors-ligne", offline_streamers),
        ]:
            if not streamers:
                continue

            streamer_list = ", ".join(
                f"[{s.display_name}](https://www.twitch.tv/{s.twitch_name})"
                if withlink
                else s.display_name.replace("_", "\\_")
                for s in streamers
            )

            chunks = split_streamer_list(streamer_list, max_length=1024)
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
        embed.set_footer("Source: evenmorestats.fr ❤️")
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

                max_streamers = max(10, max_streamers - 5)

            embed.add_field(name="Top donations", value=leaderboard_text, inline=False)

            return embed
        except Exception as e:
            logger.error(f"Error creating top donations embed: {e}")
            return None
