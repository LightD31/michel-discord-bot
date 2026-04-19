"""MedalsMixin — slash commands and embed display logic for the Olympics extension."""

from datetime import datetime
from typing import Any, Optional

from interactions import Embed, OptionType, SlashContext, Timestamp, slash_command, slash_option

from ._common import (
    COUNTRY_CODE,
    EMBED_COLOR_FRANCE,
    MEDAL_COLORS,
    MEDAL_EMOJIS,
    MEDAL_LABELS,
    MEDALS_URL,
    enabled_servers,
    logger,
    _get_flag,
)


class MedalsMixin:
    """Mixin providing medal-table slash commands and embed builders."""

    # ─── Récupération de données ──────────────────────────────────────────────

    async def _fetch_france_medals(self) -> list[dict[str, Any]]:
        """Récupère toutes les médailles de la France via l'API.

        Returns:
            Liste des médailles françaises avec détails.
        """
        data = await self._olympics_fetch(MEDALS_URL)
        medal_table = data.get("medalStandings", {}).get("medalsTable", [])

        for country in medal_table:
            if country.get("organisation") == COUNTRY_CODE:
                all_medals = []
                for discipline in country.get("disciplines", []):
                    for winner in discipline.get("medalWinners", []):
                        winner["disciplineName"] = discipline.get("name", "")
                        winner["disciplineCode"] = discipline.get("code", "")
                        all_medals.append(winner)
                return all_medals

        return []

    async def _fetch_medal_standings(self) -> list[dict[str, Any]]:
        """Récupère le classement complet des médailles.

        Returns:
            Liste du classement par pays.
        """
        data = await self._olympics_fetch(MEDALS_URL)
        return data.get("medalStandings", {}).get("medalsTable", [])

    # ─── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _medal_key(medal: dict[str, Any]) -> str:
        """Génère une clé unique pour identifier une médaille."""
        return (
            f"{medal.get('eventCode', '')}_{medal.get('medalType', '')}"
            f"_{medal.get('competitorCode', '')}"
        )

    @staticmethod
    def _get_country_standing(
        standings: list[dict[str, Any]], country_code: str
    ) -> dict[str, Any] | None:
        """Retourne le classement d'un pays spécifique."""
        for country in standings:
            if country.get("organisation") == country_code:
                return country
        return None

    # ─── Embeds ───────────────────────────────────────────────────────────────

    def _build_medal_alert_embed(
        self, medal: dict[str, Any], france_standing: dict[str, Any] | None
    ) -> Embed:
        """Construit l'embed d'alerte pour une nouvelle médaille française.

        Args:
            medal: Données de la médaille.
            france_standing: Classement actuel de la France.

        Returns:
            Embed Discord formaté.
        """
        medal_type = medal.get("medalType", "")
        emoji = MEDAL_EMOJIS.get(medal_type, "🏅")
        label = MEDAL_LABELS.get(medal_type, "Médaille")
        color = MEDAL_COLORS.get(medal_type, EMBED_COLOR_FRANCE)

        athlete = medal.get("competitorDisplayName", "France")
        event = medal.get("eventDescription", "Épreuve inconnue")
        discipline = medal.get("disciplineName", "")
        date = medal.get("date", "")
        is_team = medal.get("competitorType") == "T"

        title = f"{emoji} Médaille de {label} pour la France ! {emoji}"

        description_parts = []
        if is_team:
            description_parts.append("🇫🇷 **Équipe de France**")
        else:
            description_parts.append(f"🇫🇷 **{athlete}**")

        description_parts.append(f"📋 **{discipline}** — {event}")

        if date:
            try:
                dt = datetime.strptime(date, "%Y-%m-%d")
                description_parts.append(f"📅 {dt.strftime('%d/%m/%Y')}")
            except ValueError:
                description_parts.append(f"📅 {date}")

        embed = Embed(
            title=title,
            description="\n".join(description_parts),
            color=color,
        )

        # Ajouter le décompte total de la France
        if france_standing:
            totals = None
            for mn in france_standing.get("medalsNumber", []):
                if mn.get("type") == "Total":
                    totals = mn
                    break

            if totals:
                embed.add_field(
                    name="🇫🇷 Bilan France",
                    value=(
                        f"🥇 {totals.get('gold', 0)} | "
                        f"🥈 {totals.get('silver', 0)} | "
                        f"🥉 {totals.get('bronze', 0)} | "
                        f"**Total : {totals.get('total', 0)}**"
                    ),
                    inline=False,
                )

            rank = france_standing.get("rank")
            if rank:
                embed.add_field(
                    name="📊 Classement",
                    value=f"**{rank}{'er' if rank == 1 else 'e'}** au tableau des médailles",
                    inline=False,
                )

        embed.set_footer(text="JO d'hiver Milan-Cortina 2026")
        embed.set_thumbnail(
            url="https://stillmed.olympics.com/media/Images/OlympicOrg/Games/Winter/Milano-Cortina-2026/Milano-Cortina-2026-Logo.png"
        )

        return embed

    def _build_standings_embed(self, standings: list[dict[str, Any]], top_n: int = 15) -> Embed:
        """Construit l'embed du tableau des médailles.

        Args:
            standings: Données du classement.
            top_n: Nombre de pays à afficher.

        Returns:
            Embed Discord du classement.
        """
        embed = Embed(
            title="🏅 Tableau des médailles — Milan-Cortina 2026",
            color=EMBED_COLOR_FRANCE,
        )

        lines = []
        lines.append("```")
        lines.append(f"{'#':>2} {'Pays':<15} {'🥇':>2} {'🥈':>2} {'🥉':>2} {' Tot':>4}")
        lines.append("─" * 37)

        for country in standings[:top_n]:
            rank = country.get("rank", "-")
            org = country.get("organisation", "???")
            name = country.get("description", org)
            flag = _get_flag(org)

            totals = {}
            for mn in country.get("medalsNumber", []):
                if mn.get("type") == "Total":
                    totals = mn
                    break

            gold = totals.get("gold", 0)
            silver = totals.get("silver", 0)
            bronze = totals.get("bronze", 0)
            total = totals.get("total", 0)

            marker = "◄" if org == COUNTRY_CODE else ""
            lines.append(
                f"{rank:>2} {flag} {name:<12} {gold:>3} {silver:>3} {bronze:>3} {total:>4}{marker}"
            )

        lines.append("```")

        embed.description = "\n".join(lines)

        # Trouver les infos de France si pas dans le top_n
        france_in_list = any(c.get("organisation") == COUNTRY_CODE for c in standings[:top_n])
        if not france_in_list:
            france = self._get_country_standing(standings, COUNTRY_CODE)
            if france:
                totals = {}
                for mn in france.get("medalsNumber", []):
                    if mn.get("type") == "Total":
                        totals = mn
                        break
                embed.add_field(
                    name=f"🇫🇷 France (#{france.get('rank', '?')})",
                    value=(
                        f"🥇 {totals.get('gold', 0)} | "
                        f"🥈 {totals.get('silver', 0)} | "
                        f"🥉 {totals.get('bronze', 0)} | "
                        f"Total : {totals.get('total', 0)}"
                    ),
                    inline=False,
                )

        # Info événements
        embed.set_footer(text="JO d'hiver Milan-Cortina 2026 • Mis à jour")
        embed.timestamp = Timestamp.now()

        return embed

    def _build_france_medals_embed(
        self, medals: list[dict[str, Any]], france_standing: dict[str, Any] | None
    ) -> Embed:
        """Construit l'embed détaillé des médailles françaises.

        Args:
            medals: Liste des médailles françaises.
            france_standing: Classement actuel de la France.

        Returns:
            Embed Discord avec toutes les médailles.
        """
        embed = Embed(
            title="🇫🇷 Médailles de la France — Milan-Cortina 2026",
            color=EMBED_COLOR_FRANCE,
        )

        if not medals:
            embed.description = "Aucune médaille pour le moment."
            return embed

        # Trier par type de médaille (or, argent, bronze) puis par date
        medal_order = {"ME_GOLD": 0, "ME_SILVER": 1, "ME_BRONZE": 2}
        sorted_medals = sorted(
            medals,
            key=lambda m: (
                medal_order.get(m.get("medalType", ""), 3),
                m.get("date", ""),
            ),
        )

        for medal in sorted_medals:
            medal_type = medal.get("medalType", "")
            emoji = MEDAL_EMOJIS.get(medal_type, "🏅")
            athlete = medal.get("competitorDisplayName", "France")
            event = medal.get("eventDescription", "?")
            discipline = medal.get("disciplineName", "")
            date = medal.get("date", "")
            is_team = medal.get("competitorType") == "T"

            name = f"{emoji} {discipline} — {event}"
            if is_team:
                value = "Équipe de France"
            else:
                value = f"{athlete}"
            if date:
                try:
                    dt = datetime.strptime(date, "%Y-%m-%d")
                    value += f" • {dt.strftime('%d/%m')}"
                except ValueError:
                    value += f" • {date}"

            embed.add_field(name=name, value=value, inline=False)

        # Bilan total
        if france_standing:
            totals = None
            for mn in france_standing.get("medalsNumber", []):
                if mn.get("type") == "Total":
                    totals = mn
                    break
            if totals:
                embed.add_field(
                    name="📊 Bilan",
                    value=(
                        f"🥇 {totals.get('gold', 0)} | "
                        f"🥈 {totals.get('silver', 0)} | "
                        f"🥉 {totals.get('bronze', 0)} | "
                        f"**Total : {totals.get('total', 0)}** "
                        f"(#{france_standing.get('rank', '?')})"
                    ),
                    inline=False,
                )

        embed.set_footer(text="JO d'hiver Milan-Cortina 2026")
        embed.timestamp = Timestamp.now()

        return embed

    # ─── Commandes Slash ──────────────────────────────────────────────────────

    @slash_command(
        name="jo",
        description="Commandes des JO d'hiver Milan-Cortina 2026",
        sub_cmd_name="medailles",
        sub_cmd_description="Affiche le tableau des médailles",
        scopes=enabled_servers,
    )
    @slash_option(
        name="top",
        description="Nombre de pays à afficher (défaut : 15)",
        opt_type=OptionType.INTEGER,
        required=False,
        min_value=5,
        max_value=30,
    )
    async def cmd_medailles(self, ctx: SlashContext, top: int = 15) -> None:
        """Affiche le tableau des médailles."""
        await ctx.defer()
        try:
            standings = await self._fetch_medal_standings()
            if not standings:
                await ctx.send(
                    "❌ Impossible de récupérer le tableau des médailles.", ephemeral=True
                )
                return

            embed = self._build_standings_embed(standings, top_n=top)
            await ctx.send(embeds=[embed])
        except Exception as e:
            logger.exception(f"Erreur commande /jo medailles : {e}")
            await ctx.send("❌ Une erreur est survenue.", ephemeral=True)

    @slash_command(
        name="jo",
        description="Commandes des JO d'hiver Milan-Cortina 2026",
        sub_cmd_name="france",
        sub_cmd_description="Affiche les médailles de la France",
        scopes=enabled_servers,
    )
    async def cmd_france(self, ctx: SlashContext) -> None:
        """Affiche le détail des médailles françaises."""
        await ctx.defer()
        try:
            medals = await self._fetch_france_medals()
            standings = await self._fetch_medal_standings()
            france_standing = self._get_country_standing(standings, COUNTRY_CODE)

            embed = self._build_france_medals_embed(medals, france_standing)
            await ctx.send(embeds=[embed])
        except Exception as e:
            logger.exception(f"Erreur commande /jo france : {e}")
            await ctx.send("❌ Une erreur est survenue.", ephemeral=True)

    @slash_command(
        name="jo",
        description="Commandes des JO d'hiver Milan-Cortina 2026",
        sub_cmd_name="pays",
        sub_cmd_description="Affiche les médailles d'un pays spécifique",
        scopes=enabled_servers,
    )
    @slash_option(
        name="code",
        description="Code IOC du pays (ex: FRA, USA, NOR, ITA...)",
        opt_type=OptionType.STRING,
        required=True,
    )
    async def cmd_pays(self, ctx: SlashContext, code: str) -> None:
        """Affiche les médailles d'un pays donné."""
        await ctx.defer()
        code = code.upper().strip()
        try:
            data = await self._olympics_fetch(MEDALS_URL)
            medal_table = data.get("medalStandings", {}).get("medalsTable", [])

            country_data = None
            for country in medal_table:
                if country.get("organisation") == code:
                    country_data = country
                    break

            if not country_data:
                await ctx.send(
                    f"❌ Pays avec le code **{code}** non trouvé dans le tableau des médailles.",
                    ephemeral=True,
                )
                return

            # Construire les médailles comme pour la France
            all_medals = []
            for discipline in country_data.get("disciplines", []):
                for winner in discipline.get("medalWinners", []):
                    winner["disciplineName"] = discipline.get("name", "")
                    winner["disciplineCode"] = discipline.get("code", "")
                    all_medals.append(winner)

            country_name = country_data.get("description", code)
            flag = _get_flag(code)

            embed = Embed(
                title=f"{flag} Médailles — {country_name} — Milan-Cortina 2026",
                color=EMBED_COLOR_FRANCE,
            )

            if not all_medals:
                embed.description = "Aucune médaille pour le moment."
            else:
                medal_order = {"ME_GOLD": 0, "ME_SILVER": 1, "ME_BRONZE": 2}
                sorted_medals = sorted(
                    all_medals,
                    key=lambda m: (
                        medal_order.get(m.get("medalType", ""), 3),
                        m.get("date", ""),
                    ),
                )

                for medal in sorted_medals:
                    medal_type = medal.get("medalType", "")
                    emoji = MEDAL_EMOJIS.get(medal_type, "🏅")
                    athlete = medal.get("competitorDisplayName", country_name)
                    event = medal.get("eventDescription", "?")
                    discipline = medal.get("disciplineName", "")
                    date = medal.get("date", "")
                    is_team = medal.get("competitorType") == "T"

                    field_name = f"{emoji} {discipline} — {event}"
                    value = "Équipe" if is_team else athlete
                    if date:
                        try:
                            dt = datetime.strptime(date, "%Y-%m-%d")
                            value += f" • {dt.strftime('%d/%m')}"
                        except ValueError:
                            value += f" • {date}"

                    embed.add_field(name=field_name, value=value, inline=False)

            # Bilan
            totals = {}
            for mn in country_data.get("medalsNumber", []):
                if mn.get("type") == "Total":
                    totals = mn
                    break

            rank = country_data.get("rank", "?")
            embed.add_field(
                name="📊 Bilan",
                value=(
                    f"🥇 {totals.get('gold', 0)} | "
                    f"🥈 {totals.get('silver', 0)} | "
                    f"🥉 {totals.get('bronze', 0)} | "
                    f"**Total : {totals.get('total', 0)}** "
                    f"(#{rank})"
                ),
                inline=False,
            )

            embed.set_footer(text="JO d'hiver Milan-Cortina 2026")
            embed.timestamp = Timestamp.now()

            await ctx.send(embeds=[embed])

        except Exception as e:
            logger.exception(f"Erreur commande /jo pays : {e}")
            await ctx.send("❌ Une erreur est survenue.", ephemeral=True)

    @slash_command(
        name="jo",
        description="Commandes des JO d'hiver Milan-Cortina 2026",
        sub_cmd_name="recap",
        sub_cmd_description="Envoie un récapitulatif complet (classement + France)",
        scopes=enabled_servers,
    )
    async def cmd_recap(self, ctx: SlashContext) -> None:
        """Envoie un récapitulatif complet : classement + médailles France."""
        await ctx.defer()
        try:
            standings = await self._fetch_medal_standings()
            medals = await self._fetch_france_medals()
            france_standing = self._get_country_standing(standings, COUNTRY_CODE)

            embed_standings = self._build_standings_embed(standings, top_n=10)
            embed_france = self._build_france_medals_embed(medals, france_standing)

            await ctx.send(embeds=[embed_standings, embed_france])
        except Exception as e:
            logger.exception(f"Erreur commande /jo recap : {e}")
            await ctx.send("❌ Une erreur est survenue.", ephemeral=True)
