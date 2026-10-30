"""CommandsMixin — commandes /vlr : équipe, classement régional, résumé de match."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from interactions import (
    AutocompleteContext,
    Embed,
    OptionType,
    SlashCommandChoice,
    SlashContext,
    Timestamp,
    slash_command,
    slash_option,
)

from features.vlrgg import (
    event_and_series,
    expand_round_name,
    extract_match_id_from_url,
    fetch_match_details,
    fetch_rankings,
    fetch_team_map_stats,
    fetch_team_matches_by_id,
    fetch_team_profile,
    fetch_team_schedule,
    fetch_upcoming_matches,
    map_pool,
    normalize_team_match,
    same_team_name,
    search_teams,
    upcoming_from_schedule,
)
from src.discord_ext.embeds import format_discord_timestamp
from src.discord_ext.links import shorten_embed
from src.discord_ext.messages import send_error
from src.discord_ext.scopes import guild_scopes

from ._common import DEFAULT_EMBED_COLOR, LIVE_EMBED_COLOR, MAP_POOL_DAYS, enabled_servers, logger

# Codes acceptés par /v2/rankings
REGIONS: dict[str, str] = {
    "eu": "Europe",
    "na": "Amérique du Nord",
    "br": "Brésil",
    "la": "Amérique latine",
    "la-s": "Amérique latine Sud",
    "la-n": "Amérique latine Nord",
    "ap": "Asie-Pacifique",
    "kr": "Corée",
    "jp": "Japon",
    "cn": "Chine",
    "oce": "Océanie",
    "mn": "MENA",
    "gc": "Game Changers",
    "col": "Collegiate",
}
RANKING_SIZE = 10
STAFF_ROLES = ("coach", "manager", "analyst", "staff")
RECENT_RESULTS = 3
POOL_MAPS = 6
# Discord laisse 3 s pour répondre à une autocomplétion
AUTOCOMPLETE_SEARCH_TIMEOUT = 2.0


def _media_url(url: str) -> str:
    if url.startswith("//"):
        return f"https:{url}"
    return url if url.startswith("http") else ""


def _ts(epoch: int, style: str) -> str:
    return format_discord_timestamp(datetime.fromtimestamp(epoch, UTC), style)


class CommandsMixin:
    """Mixin providing the /vlr slash commands."""

    # ── /vlr equipe ───────────────────────────────────────────────────────────

    @slash_command(
        name="vlr",
        description="Esport Valorant (VLR.gg)",
        scopes=guild_scopes(enabled_servers),
        sub_cmd_name="equipe",
        sub_cmd_description="Profil d'une équipe : roster, résultats, prochain match, maps",
    )
    @slash_option(
        name="equipe",
        description="Nom de l'équipe (ou son ID VLR.gg)",
        opt_type=OptionType.STRING,
        required=True,
        autocomplete=True,
    )
    async def vlr_equipe(self, ctx: SlashContext, equipe: str) -> None:
        await ctx.defer()
        try:
            team_id = await self._resolve_team_id(equipe)
            embed = await self._build_team_embed(team_id) if team_id else None
        except Exception as e:
            logger.exception(f"/vlr equipe {equipe!r}: {e}")
            embed = None
        if embed is None:
            await send_error(
                ctx, f"Équipe « {equipe} » introuvable, ou API VLR.gg indisponible pour le moment."
            )
            return
        await ctx.send(embeds=[await shorten_embed(embed, tags=["vlrgg"])])

    @vlr_equipe.autocomplete("equipe")
    async def vlr_equipe_autocomplete(self, ctx: AutocompleteContext) -> None:
        text = (ctx.input_text or "").strip()
        choices: list[dict[str, str]] = []
        seen: set[str] = set()

        server = self._servers.get(str(ctx.guild_id)) if ctx.guild_id else None
        for team_state in server.teams.values() if server else []:
            tc = team_state.team_config
            if tc.vlr_team_id and (not text or text.casefold() in tc.name.casefold()):
                choices.append({"name": f"⭐ {tc.name}"[:100], "value": tc.vlr_team_id})
                seen.add(tc.vlr_team_id)

        if len(text) >= 2:
            try:
                found = await asyncio.wait_for(search_teams(text), AUTOCOMPLETE_SEARCH_TIMEOUT)
            except Exception:
                found = []
            for team in found:
                team_id = str(team.get("id") or "")
                if not team_id or team_id in seen:
                    continue
                label = f"{team.get('name', '')} {team.get('tag', '')}".strip()
                choices.append({"name": label[:100], "value": team_id})
                seen.add(team_id)

        await ctx.send(choices=choices[:25])

    @staticmethod
    async def _resolve_team_id(text: str) -> str | None:
        """ID choisi dans l'autocomplétion, sinon meilleure correspondance de recherche."""
        text = text.strip()
        if text.isdigit():
            return text
        found = await search_teams(text)
        for team in found:
            if same_team_name(text, str(team.get("name") or "")):
                return str(team["id"])
        return str(found[0]["id"]) if found and found[0].get("id") else None

    async def _build_team_embed(self, team_id: str) -> Embed | None:
        since = (datetime.now(UTC) - timedelta(days=MAP_POOL_DAYS)).date()
        profile, schedule, team_matches, homepage, stats = await asyncio.gather(
            fetch_team_profile(team_id),
            fetch_team_schedule(team_id),
            fetch_team_matches_by_id(team_id),
            fetch_upcoming_matches(),
            fetch_team_map_stats(team_id, since),
        )
        name = str(profile.get("name") or "")
        if not name:
            return None

        results = [normalize_team_match(m) for m in team_matches]
        upcoming = upcoming_from_schedule(
            schedule,
            exclude_ids={str(r.get("match_id") or "") for r in results} - {""},
            homepage=homepage,
            vlr_name=name,
            now=datetime.now(UTC),
        )

        tag = str(profile.get("tag") or "")
        description = [
            p
            for p in (
                str(profile.get("country_name") or ""),
                self._rank_line(profile),
                f"💰 Gains : {profile['total_winnings']}" if profile.get("total_winnings") else "",
            )
            if p
        ]
        embed = Embed(
            title=f"{name} ({tag})" if tag and tag != name else name,
            url=f"https://www.vlr.gg/team/{team_id}",
            description="\n".join(description),
            color=DEFAULT_EMBED_COLOR,
            timestamp=Timestamp.now(),
        )
        logo = _media_url(str(profile.get("logo") or ""))
        if logo:
            embed.set_thumbnail(url=logo)

        roster = profile.get("roster") or []
        players = [self._roster_name(p) for p in roster if not self._is_staff(p)]
        staff = [self._roster_name(p) for p in roster if self._is_staff(p)]
        if players:
            embed.add_field(name="Roster", value=", ".join(players)[:1024], inline=False)
        if staff:
            embed.add_field(name="Staff", value=", ".join(staff)[:1024], inline=False)

        if results:
            lines = [
                f"{'✅' if r.get('result') == 'win' else '❌'} {r['score1']}-{r['score2']} "
                f"vs {r['team2']} · {r['time_completed']}"
                for r in results[:RECENT_RESULTS]
            ]
            embed.add_field(name="Derniers résultats", value="\n".join(lines), inline=False)

        if upcoming:
            nxt = upcoming[0]
            when = (
                _ts(nxt["kickoff"], "R" if nxt["kickoff_precise"] else "D")
                if isinstance(nxt.get("kickoff"), int)
                else "date à confirmer"
            )
            event = " — ".join(p for p in (nxt.get("match_event"), nxt.get("round_info")) if p)
            value = f"vs **{nxt['team2']}** · {when}" + (f"\n{event}" if event else "")
            embed.add_field(name="Prochain match", value=value, inline=False)

        pool = map_pool(stats)[:POOL_MAPS]
        if pool:
            lines = [f"**{r.name}** {r.win_pct}% ({r.wins}-{r.losses})" for r in pool]
            embed.add_field(
                name=f"Maps ({MAP_POOL_DAYS} derniers jours)", value="\n".join(lines), inline=False
            )

        embed.set_footer(text="Source: VLR.gg")
        return embed

    @staticmethod
    def _is_staff(player: dict[str, Any]) -> bool:
        # Le profil ne marque pas toujours les coachs comme staff : on lit aussi le rôle
        role = str(player.get("role") or "").casefold()
        return bool(player.get("is_staff")) or any(word in role for word in STAFF_ROLES)

    @staticmethod
    def _roster_name(player: dict[str, Any]) -> str:
        alias = str(player.get("alias") or player.get("name") or "?")
        role = str(player.get("role") or "")
        suffix = " ©" if player.get("is_captain") else ""
        return f"{alias}{suffix} ({role})" if role else f"{alias}{suffix}"

    # ── /vlr classement ───────────────────────────────────────────────────────

    @vlr_equipe.subcommand(
        sub_cmd_name="classement",
        sub_cmd_description="Classement VLR.gg d'une région",
    )
    @slash_option(
        name="region",
        description="Région",
        opt_type=OptionType.STRING,
        required=True,
        choices=[SlashCommandChoice(name=label, value=code) for code, label in REGIONS.items()],
    )
    async def vlr_classement(self, ctx: SlashContext, region: str) -> None:
        await ctx.defer()
        rows = await fetch_rankings(region)
        if not rows:
            await send_error(ctx, "Classement VLR.gg indisponible pour le moment.")
            return

        lines = []
        for row in rows[:RANKING_SIZE]:
            details = [
                f"{row['rating']} pts" if row.get("rating") else "",
                str(row.get("record") or ""),
                str(row.get("earnings") or ""),
            ]
            info = " · ".join(p for p in details if p)
            lines.append(f"**{row.get('rank', '?')}.** {row.get('team', '???')} — {info}")

        embed = Embed(
            title=f"Classement VLR.gg — {REGIONS.get(region, region)}",
            description="\n".join(lines),
            color=DEFAULT_EMBED_COLOR,
            timestamp=Timestamp.now(),
        )
        logo = _media_url(str(rows[0].get("logo") or ""))
        if logo:
            embed.set_thumbnail(url=logo)
        embed.set_footer(text="Source: VLR.gg")
        await ctx.send(embeds=[embed])

    # ── /vlr match ────────────────────────────────────────────────────────────

    @vlr_equipe.subcommand(
        sub_cmd_name="match",
        sub_cmd_description="Résumé d'un match VLR.gg : score, maps, veto, meilleurs joueurs",
    )
    @slash_option(
        name="lien",
        description="Lien du match sur vlr.gg (ou son ID)",
        opt_type=OptionType.STRING,
        required=True,
    )
    async def vlr_match(self, ctx: SlashContext, lien: str) -> None:
        text = lien.strip()
        match_id = extract_match_id_from_url(text) or (text if text.isdigit() else None)
        if not match_id:
            await send_error(ctx, "Lien VLR.gg invalide (ex : https://www.vlr.gg/773433/…).")
            return
        await ctx.defer()
        details = await fetch_match_details(match_id)
        if len(details.get("teams") or []) < 2:
            await send_error(ctx, f"Match VLR.gg {match_id} introuvable.")
            return
        embed = self._build_match_summary_embed(match_id, details)
        await ctx.send(embeds=[await shorten_embed(embed, tags=["vlrgg"])])

    def _build_match_summary_embed(self, match_id: str, details: dict[str, Any]) -> Embed:
        t1, t2 = details["teams"][0], details["teams"][1]
        name1, name2 = t1.get("name") or "???", t2.get("name") or "???"
        s1, s2 = str(t1.get("score") or ""), str(t2.get("score") or "")
        title = f"{name1} {s1}-{s2} {name2}" if s1 and s2 else f"{name1} vs {name2}"

        status = str(details.get("status") or "").strip()
        live = status.lower() == "live"
        if status.lower() == "final":
            status_text = "Terminé"
        elif live:
            status_text = "🔴 En direct"
        else:
            status_text = f"À venir ({status})" if status else "À venir"

        event, series = event_and_series(details)
        description = "\n".join(
            p
            for p in (f"**{event}**" if event else "", expand_round_name(series), status_text)
            if p
        )
        embed = Embed(
            title=title,
            url=f"https://www.vlr.gg/{match_id}",
            description=description,
            color=LIVE_EMBED_COLOR if live else DEFAULT_EMBED_COLOR,
            timestamp=Timestamp.now(),
        )
        self._add_details_fields(embed, details)
        logo = self._event_logo(details)
        if logo:
            embed.set_thumbnail(url=logo)
        embed.set_footer(text="Source: VLR.gg")
        return embed
