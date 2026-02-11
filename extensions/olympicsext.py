"""Extension Discord pour le suivi des médailles des JO d'hiver Milan-Cortina 2026.

Cette extension surveille les nouvelles médailles françaises et envoie
des alertes automatiques dans un canal Discord configuré.
"""

import os
import json
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Any
from functools import partial

from curl_cffi import requests as cffi_requests
from interactions import (
    Extension,
    Client,
    Task,
    IntervalTrigger,
    listen,
    Embed,
    Timestamp,
    slash_command,
    SlashContext,
    slash_option,
    OptionType,
)

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleOlympics")
module_config = module_config[enabled_servers[0]]

# ─── Constantes ───────────────────────────────────────────────────────────────
BASE_URL = "https://www.olympics.com/wmr-owg2026/competition/api/FRA"
MEDALS_URL = f"{BASE_URL}/medals"
MEDALLISTS_URL = f"{BASE_URL}/medallists"
EVENT_MEDALS_URL = f"{BASE_URL}/eventmedals"
SCHEDULE_URL_TEMPLATE = f"{BASE_URL.replace('/competition/', '/schedules/')}/schedule/lite/day/{{date}}"

POLL_INTERVAL_MINUTES = 3
COUNTRY_CODE = "FRA"
COUNTRY_NAME = "France"

STATE_FILE = "config/olympics_state.json"

# Headers requis pour l'API Olympics.com (anti-bot)
OLYMPICS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.olympics.com/fr/olympic-games/milan-cortina-2026/medals",
    "Origin": "https://www.olympics.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

# Emojis pour les médailles
MEDAL_EMOJIS = {
    "ME_GOLD": "🥇",
    "ME_SILVER": "🥈",
    "ME_BRONZE": "🥉",
}

MEDAL_LABELS = {
    "ME_GOLD": "Or",
    "ME_SILVER": "Argent",
    "ME_BRONZE": "Bronze",
}

MEDAL_COLORS = {
    "ME_GOLD": 0xFFD700,
    "ME_SILVER": 0xC0C0C0,
    "ME_BRONZE": 0xCD7F32,
}

# Drapeaux des pays fréquents
COUNTRY_FLAGS = {
    "FRA": "🇫🇷", "USA": "🇺🇸", "GER": "🇩🇪", "NOR": "🇳🇴", "ITA": "🇮🇹",
    "SWE": "🇸🇪", "SUI": "🇨🇭", "AUT": "🇦🇹", "CAN": "🇨🇦", "JPN": "🇯🇵",
    "KOR": "🇰🇷", "CHN": "🇨🇳", "GBR": "🇬🇧", "NED": "🇳🇱", "AUS": "🇦🇺",
    "CZE": "🇨🇿", "SLO": "🇸🇮", "FIN": "🇫🇮", "POL": "🇵🇱", "ESP": "🇪🇸",
    "BEL": "🇧🇪", "RUS": "🇷🇺", "BUL": "🇧🇬", "ROC": "🏳️",
}

EMBED_COLOR_FRANCE = 0x002395  # Bleu France


def _get_flag(code: str) -> str:
    """Retourne l'émoji drapeau pour un code pays IOC."""
    return COUNTRY_FLAGS.get(code, "🏳️")


class Olympics(Extension):
    """Extension pour le suivi des JO d'hiver Milan-Cortina 2026.

    Surveille les médailles de la France et envoie des alertes
    dans le canal Discord configuré.
    """

    def __init__(self, bot: Client) -> None:
        self.bot = bot
        self.channel = None
        # État : ensemble des clés de médailles déjà connues
        # Format : "{eventCode}_{medalType}_{competitorCode}"
        self.known_medals: set[str] = set()
        self._load_state()

    # ─── HTTP Client dédié Olympics ──────────────────────────────────────────────

    async def _olympics_fetch(self, url: str, retries: int = 3) -> dict:
        """Effectue une requête GET vers l'API Olympics.com.

        Utilise curl_cffi pour impersonner le fingerprint TLS de Chrome,
        nécessaire pour contourner le WAF d'olympics.com.

        Args:
            url: URL de l'API.
            retries: Nombre de tentatives.

        Returns:
            Données JSON.
        """
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.olympics.com/fr/olympic-games/milan-cortina-2026/medals",
            "Origin": "https://www.olympics.com",
        }

        for attempt in range(retries):
            try:
                response = await asyncio.to_thread(
                    partial(
                        cffi_requests.get,
                        url,
                        headers=headers,
                        impersonate="chrome",
                        timeout=30,
                    )
                )
                if response.status_code == 200:
                    return response.json()
                logger.warning(
                    f"Olympics API {url} - status {response.status_code} (tentative {attempt + 1}/{retries})"
                )
            except Exception as e:
                logger.warning(f"Olympics API erreur: {e} (tentative {attempt + 1}/{retries})")

            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)  # Backoff exponentiel

        raise Exception(f"Impossible de récupérer {url} après {retries} tentatives")

    # ─── Persistance ──────────────────────────────────────────────────────────

    def _load_state(self) -> None:
        """Charge l'état des médailles déjà notifiées."""
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.known_medals = set(data.get("known_medals", []))
            logger.info(f"État Olympics chargé : {len(self.known_medals)} médailles connues")
        except FileNotFoundError:
            logger.info("Aucun fichier d'état Olympics trouvé, première exécution")
        except Exception as e:
            logger.error(f"Erreur lors du chargement de l'état Olympics : {e}")

    def _save_state(self) -> None:
        """Sauvegarde l'état des médailles notifiées."""
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"known_medals": list(self.known_medals)}, f, indent=2)
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde de l'état Olympics : {e}")

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    @listen()
    async def on_startup(self) -> None:
        """Initialise le canal et démarre la tâche de surveillance."""
        try:
            channel_id = module_config.get("olympicsChannelId")
            if channel_id:
                self.channel = await self.bot.fetch_channel(channel_id)
                logger.info(f"Canal Olympics initialisé : {self.channel.name}")
            else:
                logger.error("olympicsChannelId non configuré dans moduleOlympics")
                return

            # Initialisation silencieuse : enregistrer les médailles existantes
            # sans envoyer de notifications
            if not self.known_medals:
                await self._initialize_known_medals()

            self.check_medals.start()
            logger.info("Tâche de surveillance des médailles Olympics démarrée")
        except Exception as e:
            logger.exception(f"Erreur lors de l'initialisation Olympics : {e}")

    async def _initialize_known_medals(self) -> None:
        """Enregistre silencieusement les médailles déjà existantes au démarrage."""
        try:
            medals = await self._fetch_france_medals()
            for medal in medals:
                key = self._medal_key(medal)
                self.known_medals.add(key)
            self._save_state()
            logger.info(f"Initialisation : {len(self.known_medals)} médailles FRA existantes enregistrées")
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation des médailles : {e}")

    # ─── Tâche planifiée ──────────────────────────────────────────────────────

    @Task.create(IntervalTrigger(minutes=POLL_INTERVAL_MINUTES))
    async def check_medals(self) -> None:
        """Vérifie périodiquement les nouvelles médailles françaises."""
        logger.debug("Vérification des médailles Olympics...")
        try:
            medals = await self._fetch_france_medals()
            new_medals = []

            for medal in medals:
                key = self._medal_key(medal)
                if key not in self.known_medals:
                    new_medals.append(medal)
                    self.known_medals.add(key)

            if new_medals:
                logger.info(f"{len(new_medals)} nouvelle(s) médaille(s) détectée(s) pour la France !")
                self._save_state()

                # Récupérer le classement à jour pour le contexte
                standings = await self._fetch_medal_standings()
                france_standing = self._get_country_standing(standings, COUNTRY_CODE)

                for medal in new_medals:
                    embed = self._build_medal_alert_embed(medal, france_standing)
                    if self.channel:
                        await self.channel.send(embeds=[embed])
                        await asyncio.sleep(1)  # Petite pause entre les messages
            else:
                logger.debug("Aucune nouvelle médaille pour la France")

        except Exception as e:
            logger.exception(f"Erreur lors de la vérification des médailles : {e}")

    # ─── Récupération de données ──────────────────────────────────────────────

    async def _fetch_france_medals(self) -> List[Dict[str, Any]]:
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

    async def _fetch_medal_standings(self) -> List[Dict[str, Any]]:
        """Récupère le classement complet des médailles.

        Returns:
            Liste du classement par pays.
        """
        data = await self._olympics_fetch(MEDALS_URL)
        return data.get("medalStandings", {}).get("medalsTable", [])

    async def _fetch_all_medallists(self) -> List[Dict[str, Any]]:
        """Récupère la liste de tous les médaillés.

        Returns:
            Liste de tous les athlètes médaillés.
        """
        data = await self._olympics_fetch(MEDALLISTS_URL)
        return data.get("athletes", [])

    async def _fetch_event_medals(self) -> Dict[str, Any]:
        """Récupère les médailles par épreuve.

        Returns:
            Données des médailles par discipline/épreuve.
        """
        data = await self._olympics_fetch(EVENT_MEDALS_URL)
        return data.get("eventMedals", {})

    # ─── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _medal_key(medal: Dict[str, Any]) -> str:
        """Génère une clé unique pour identifier une médaille."""
        return (
            f"{medal.get('eventCode', '')}_{medal.get('medalType', '')}"
            f"_{medal.get('competitorCode', '')}"
        )

    @staticmethod
    def _get_country_standing(
        standings: List[Dict[str, Any]], country_code: str
    ) -> Optional[Dict[str, Any]]:
        """Retourne le classement d'un pays spécifique."""
        for country in standings:
            if country.get("organisation") == country_code:
                return country
        return None

    # ─── Embeds ───────────────────────────────────────────────────────────────

    def _build_medal_alert_embed(
        self, medal: Dict[str, Any], france_standing: Optional[Dict[str, Any]]
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
        embed.set_thumbnail(url="https://stillmed.olympics.com/media/Images/OlympicOrg/Games/Winter/Milano-Cortina-2026/Milano-Cortina-2026-Logo.png")

        return embed

    def _build_standings_embed(
        self, standings: List[Dict[str, Any]], top_n: int = 15
    ) -> Embed:
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
        lines.append(f"{'#':>3} {'Pays':<14} {'🥇':>4} {'🥈':>4} {'🥉':>4} {'Tot':>5}")
        lines.append("─" * 45)

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

            marker = " ◄" if org == COUNTRY_CODE else ""
            lines.append(
                f"{rank:>3} {flag} {name:<12} {gold:>4} {silver:>4} {bronze:>4} {total:>5}{marker}"
            )

        lines.append("```")

        embed.description = "\n".join(lines)

        # Trouver les infos de France si pas dans le top_n
        france_in_list = any(
            c.get("organisation") == COUNTRY_CODE for c in standings[:top_n]
        )
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
        self, medals: List[Dict[str, Any]], france_standing: Optional[Dict[str, Any]]
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
                await ctx.send("❌ Impossible de récupérer le tableau des médailles.", ephemeral=True)
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
