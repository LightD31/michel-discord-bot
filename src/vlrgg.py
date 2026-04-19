"""Client pour l'API non-officielle VLR.gg (V2).

Source unique pour les données de matchs Valorant.
API: https://vlr.drndvs.fr (déploiement custom)

Endpoints V2 utilisés:
- /v2/team/matches?id=X — historique des matchs d'une équipe (results + upcoming)
- /v2/match?q=live_score — matchs en direct (filtrage par nom)
- /v2/match/details?match_id=X — détails complets d'un match
- /v2/team?id=X — profil d'équipe

Note: Un cache avec TTL court est utilisé (API self-hosted sur vlr.drndvs.fr).
"""

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.core import logging as logutil
from src.discord_ext.embeds import format_discord_timestamp
from src.core.http import fetch

logger = logutil.init_logger(__name__)

VLRGG_API_URL = "https://vlr.drndvs.fr"

# Limite de requêtes concurrentes vers l'API (éviter les 502 sur le serveur self-hosted)
_api_semaphore = asyncio.Semaphore(2)

# Cache TTL en secondes par type d'endpoint (valeurs basses, API self-hosted)
CACHE_TTL = {
    "live": 10,
    "upcoming": 30,
    "results": 60,
    "details": 30,
    "team": 120,
    "default": 30,
}

# Cache interne : { "endpoint:params_hash" -> (timestamp, data) }
_cache: dict[str, tuple] = {}


def _cache_key(endpoint: str, params: dict[str, str] | None) -> str:
    """Génère une clé de cache unique pour un endpoint + params."""
    params_str = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    return f"{endpoint}?{params_str}"


def _get_ttl(endpoint: str, params: dict[str, str] | None = None) -> int:
    """Détermine le TTL de cache approprié pour un endpoint."""
    if "match/details" in endpoint:
        return CACHE_TTL["details"]
    if "team/matches" in endpoint or "team" in endpoint:
        return CACHE_TTL["team"]
    q = (params or {}).get("q", "")
    if q == "live_score":
        return CACHE_TTL["live"]
    if q == "upcoming":
        return CACHE_TTL["upcoming"]
    if q == "results":
        return CACHE_TTL["results"]
    return CACHE_TTL["default"]


async def vlrgg_request(endpoint: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    """Effectue une requête vers l'API VLR.gg avec cache.

    Args:
        endpoint: Point d'entrée API (ex: "v2/match").
        params: Paramètres de requête.

    Returns:
        Données JSON de la réponse, ou dict vide en cas d'erreur.
    """
    key = _cache_key(endpoint, params)
    ttl = _get_ttl(endpoint, params)

    # Vérifier le cache
    if key in _cache:
        cached_time, cached_data = _cache[key]
        age = time.monotonic() - cached_time
        if age < ttl:
            logger.debug(f"VLR.gg cache hit pour {key} (âge: {age:.0f}s)")
            return cached_data

    url = f"{VLRGG_API_URL}/{endpoint}"
    try:
        async with _api_semaphore:
            data: dict[str, Any] = await fetch(url, params=params, return_type="json")  # type: ignore[assignment]
        _cache[key] = (time.monotonic(), data)
        return data
    except Exception as e:
        logger.error(f"Erreur API VLR.gg pour {endpoint}: {e}")
        # Renvoyer le cache expiré plutôt que rien si on en a un
        if key in _cache:
            logger.warning(f"Utilisation du cache expiré pour {key}")
            return _cache[key][1]
        return {}


def filter_team_matches(team: str, matches: list[dict]) -> list[dict]:
    """Filtre les matchs impliquant une équipe spécifique.

    Args:
        team: Nom de l'équipe (insensible à la casse).
        matches: Liste des matchs depuis VLR.gg.

    Returns:
        Liste filtrée des matchs de l'équipe.
    """
    team_lower = team.lower()
    return [
        m
        for m in matches
        if team_lower in m.get("team1", "").lower() or team_lower in m.get("team2", "").lower()
    ]


def parse_vlrgg_timestamp(timestamp_str: str) -> datetime | None:
    """Parse un timestamp VLR.gg en datetime.

    Args:
        timestamp_str: Chaîne de timestamp (ex: "2024-04-24 21:00:00").

    Returns:
        datetime correspondant, ou None si le parsing échoue.
    """
    if not timestamp_str:
        return None
    try:
        return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def extract_match_id_from_url(url: str) -> str | None:
    """Extrait l'ID d'un match depuis une URL VLR.gg.

    Ex: "https://www.vlr.gg/412345/team1-vs-team2" -> "412345"
    """
    if not url:
        return None
    m = re.search(r"vlr\.gg/(\d+)", url)
    return m.group(1) if m else None


# Abréviations courantes des rounds de tournoi
ROUND_ABBREVIATIONS: dict[str, str] = {
    "gf": "Grande Finale",
    "grand final": "Grande Finale",
    "f": "Finale",
    "final": "Finale",
    "sf": "Demi-finale",
    "semi-final": "Demi-finale",
    "semifinal": "Demi-finale",
    "qf": "Quart de finale",
    "quarter-final": "Quart de finale",
    "quarterfinal": "Quart de finale",
    "ro8": "Huitièmes",
    "ro16": "Seizièmes",
    "ro32": "32èmes",
    "ub": "Winner Bracket",
    "ubf": "Finale Winner Bracket",
    "ubsf": "Demi-finale Winner Bracket",
    "ubqf": "Quart Winner Bracket",
    "ubr1": "Round 1 Winner Bracket",
    "ubr2": "Round 2 Winner Bracket",
    "lb": "Loser Bracket",
    "lbf": "Finale Loser Bracket",
    "lbsf": "Demi-finale Loser Bracket",
    "lbr1": "Round 1 Loser Bracket",
    "lbr2": "Round 2 Loser Bracket",
    "lbr3": "Round 3 Loser Bracket",
    "lbr4": "Round 4 Loser Bracket",
    "gs": "Phase de groupes",
    "group stage": "Phase de groupes",
    "elim": "Éliminatoire",
    "elimination": "Éliminatoire",
    "decider": "Match décisif",
    "winners": "Match des gagnants",
    "losers": "Match des perdants",
}


def expand_round_name(round_str: str) -> str:
    """Développe les abréviations courantes de rounds de tournoi.

    Ex: "GF" -> "Grande Finale", "UB SF" -> "Demi-finale Winner Bracket"
    """
    if not round_str:
        return round_str

    normalized = round_str.strip().lower()

    # Correspondance exacte
    if normalized in ROUND_ABBREVIATIONS:
        return ROUND_ABBREVIATIONS[normalized]

    # Essayer avec tirets/espaces supprimés
    compact = normalized.replace(" ", "").replace("-", "")
    if compact in ROUND_ABBREVIATIONS:
        return ROUND_ABBREVIATIONS[compact]

    # Pattern "UB R1", "LB R2", etc.
    bracket_match = re.match(r"^(ub|lb)\s*r?(\d+)$", compact)
    if bracket_match:
        bracket = "Winner" if bracket_match.group(1) == "ub" else "Loser"
        return f"Round {bracket_match.group(2)} {bracket} Bracket"

    return round_str


# L'API VLR.gg renvoie les heures en EST (UTC-5)
_VLR_TIMEZONE = timezone(timedelta(hours=-5))

# Formats de date courants retournés par l'API VLR.gg
_DATE_FORMATS = [
    "%Y/%m/%d%I:%M %p",  # 2026/02/1111:00 am (pas d'espace)
    "%Y/%m/%d %I:%M %p",  # 2026/02/11 11:00 am
    "%Y-%m-%d %H:%M:%S",  # 2024-04-24 21:00:00
    "%Y-%m-%d %H:%M",  # 2024-04-24 21:00
    "%Y/%m/%d",  # 2026/02/11
    "%b %d, %Y",  # Feb 11, 2026
]


def format_vlr_date(date_str: str) -> str:
    """Formate une date VLR.gg en timestamp Discord (affichage local pour chaque utilisateur).

    Les dates de l'API VLR.gg sont en EST (UTC-5).
    Retourne un timestamp Discord <t:UNIX:f> si le parsing réussit,
    sinon retourne la chaîne telle quelle.
    """
    if not date_str:
        return "?"

    cleaned = date_str.strip()

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(cleaned, fmt)
            # Attacher le fuseau EST pour un timestamp Unix correct
            dt = dt.replace(tzinfo=_VLR_TIMEZONE)
            return format_discord_timestamp(dt, "f")
        except ValueError:
            continue

    # Dernier essai: seulement des chiffres de date sans heure
    date_only = re.match(r"(\d{4})[/-](\d{2})[/-](\d{2})", cleaned)
    if date_only:
        try:
            dt = datetime(
                int(date_only.group(1)),
                int(date_only.group(2)),
                int(date_only.group(3)),
                tzinfo=_VLR_TIMEZONE,
            )
            return format_discord_timestamp(dt, "D")
        except ValueError:
            pass

    return cleaned


def normalize_team_match(entry: dict[str, Any]) -> dict[str, Any]:
    """Convertit une entrée /v2/team/matches en format normalisé.

    Args:
        entry: Entrée brute depuis /v2/team/matches.

    Returns:
        Dictionnaire normalisé avec clés standardisées.
    """
    t1 = entry.get("team1", {})
    t2 = entry.get("team2", {})
    team1_name = t1.get("name", "???") if isinstance(t1, dict) else str(t1)
    team2_name = t2.get("name", "???") if isinstance(t2, dict) else str(t2)

    score_str = entry.get("score", "")
    parts = score_str.split(":") if score_str else []
    score1 = parts[0].strip() if len(parts) >= 2 else "?"
    score2 = parts[1].strip() if len(parts) >= 2 else "?"

    # Déterminer le statut: si les scores sont "--" ou vides, c'est un upcoming
    is_upcoming = score_str in ("", "-", "–", "--") or score1 == "?" or score1 == "-"

    # Le champ "event" de /team/matches est en fait l'abréviation du round ("GF", "LBF", ...)
    round_abbr = entry.get("event", "")

    return {
        "team1": team1_name,
        "team2": team2_name,
        "score1": score1 if not is_upcoming else "0",
        "score2": score2 if not is_upcoming else "0",
        "match_page": entry.get("url", ""),
        "tournament_name": "",  # Sera rempli par enrich_match_from_details
        "round_info": expand_round_name(round_abbr),
        "time_completed": format_vlr_date(entry.get("date", "")),
        "match_event": "",  # Sera rempli par enrich_match_from_details
        "match_id": entry.get("match_id", ""),
        "result": entry.get("result", ""),  # "win" ou "loss" directement depuis l'API
        "status": "upcoming" if is_upcoming else "completed",
    }


def _clean_vlr_text(text: str) -> str:
    """Nettoie le texte renvoyé par VLR.gg (tabs, newlines, espaces multiples)."""
    if not text:
        return ""
    cleaned = re.sub(r"[\t\n\r]+", " ", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def enrich_match_from_details(match: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
    """Enrichit un match normalisé avec les données de /v2/match/details.

    Met à jour le round_info, tournament_name et time_completed
    à partir des données plus fiables de l'endpoint details.

    Note: Dans /match/details, 'event' est un dict {name, series, logo}.
    """
    if not details:
        return match

    # Extraire event (dict dans /match/details)
    event_data = details.get("event", {})
    if isinstance(event_data, dict):
        event_name = _clean_vlr_text(event_data.get("name", ""))
        series = _clean_vlr_text(event_data.get("series", ""))
    else:
        event_name = _clean_vlr_text(str(event_data))
        series = ""

    # Extraire le nom du tournoi en retirant le suffixe "series" du name
    # Ex: name="Challengers 2026: FR Stage 1 Playoffs: Grand Final", series="Playoffs: Grand Final"
    # => tournament = "Challengers 2026: FR Stage 1"
    if event_name and series and series in event_name:
        tournament = event_name[: event_name.index(series)].strip()
        if not tournament:
            tournament = event_name
    elif event_name:
        tournament = event_name
    else:
        tournament = ""

    if tournament:
        match["tournament_name"] = tournament
        match["match_event"] = tournament

    # Round / série ("Playoffs: Grand Final", etc.)
    if series:
        match["round_info"] = expand_round_name(series)

    # Déterminer si l'ordre des équipes est inversé entre /team/matches et /match/details
    teams = details.get("teams", [])
    swapped = False
    if len(teams) >= 2:
        detail_t1 = teams[0].get("name", "").strip().lower()
        detail_t2 = teams[1].get("name", "").strip().lower()
        match_t1 = match.get("team1", "").strip().lower()
        # Si team1 de /team/matches correspond à teams[1] de /match/details, l'ordre est inversé
        if (match_t1 and detail_t2 and match_t1 in detail_t2 or detail_t2 in match_t1) and not (
            match_t1 in detail_t1 or detail_t1 in match_t1
        ):
            swapped = True

    # Scores depuis teams[] (en respectant l'ordre)
    if len(teams) >= 2 and match.get("status") == "completed":
        if swapped:
            s1 = teams[1].get("score", "")
            s2 = teams[0].get("score", "")
        else:
            s1 = teams[0].get("score", "")
            s2 = teams[1].get("score", "")
        if s1 and s2:
            match["score1"] = str(s1)
            match["score2"] = str(s2)

    # Stocker les scores par map pour l'affichage (en respectant l'ordre)
    maps_data = details.get("maps", [])
    if maps_data:
        maps_summary = []
        for m in maps_data:
            map_name = m.get("map_name", m.get("map", ""))
            if not map_name or map_name.lower() in ("tbd", "n/a"):
                continue
            score = m.get("score", {})
            if isinstance(score, dict):
                raw_s1 = score.get("team1", "?")
                raw_s2 = score.get("team2", "?")
            else:
                raw_s1 = m.get("team1_score", "?")
                raw_s2 = m.get("team2_score", "?")
            if swapped:
                maps_summary.append({"map": map_name, "score1": raw_s2, "score2": raw_s1})
            else:
                maps_summary.append({"map": map_name, "score1": raw_s1, "score2": raw_s2})
        if maps_summary:
            match["maps"] = maps_summary

    return match


async def _enrich_matches(
    matches: list[dict[str, Any]], max_count: int = 6
) -> list[dict[str, Any]]:
    """Enrichit une liste de matchs en récupérant les détails en parallèle.

    Seuls les matchs avec un match_id sont enrichis.
    """
    to_enrich = []
    for i, m in enumerate(matches[:max_count]):
        match_id = m.get("match_id") or extract_match_id_from_url(m.get("match_page", ""))
        if match_id:
            to_enrich.append((i, match_id))

    if not to_enrich:
        return matches

    # Récupérer les détails en parallèle
    tasks = [fetch_match_details(mid) for _, mid in to_enrich]
    details_list = await asyncio.gather(*tasks, return_exceptions=True)

    for (idx, _match_id), details in zip(to_enrich, details_list, strict=False):
        if isinstance(details, Exception):
            logger.warning(f"Échec enrichissement match {_match_id}: {details}")
            continue
        if isinstance(details, dict) and details:
            matches[idx] = enrich_match_from_details(matches[idx], details)

    return matches


# ── Endpoints V2 ─────────────────────────────────────────────────


async def fetch_match_details(match_id: str) -> dict[str, Any]:
    """Récupère les détails complets d'un match par son ID.

    Inclut: scores par map, stats joueurs, rounds, economy, head-to-head.

    Args:
        match_id: ID VLR.gg du match.

    Returns:
        Données complètes du match, ou dict vide en cas d'erreur.
    """
    data = await vlrgg_request("v2/match/details", {"match_id": match_id})
    inner = data.get("data", data)
    # L'API renvoie {status, segments: [match_data]} — extraire le premier segment
    if isinstance(inner, dict):
        segments = inner.get("segments", [])
        if segments and isinstance(segments, list):
            return segments[0]
    return inner


async def fetch_team_info(team_id: str) -> dict[str, Any]:
    """Récupère le profil d'une équipe par son ID VLR.gg."""
    data = await vlrgg_request("v2/team", {"id": team_id})
    inner = data.get("data", data)
    if isinstance(inner, dict):
        segments = inner.get("segments", [])
        if segments and isinstance(segments, list):
            return segments[0]
    return inner


async def fetch_team_matches_by_id(team_id: str, page: int = 1) -> list[dict[str, Any]]:
    """Récupère l'historique des matchs d'une équipe par son ID.

    Chaque match inclut un match_id utilisable avec fetch_match_details().

    Args:
        team_id: ID VLR.gg de l'équipe.
        page: Numéro de page (1-based).

    Returns:
        Liste des matchs de l'équipe.
    """
    data = await vlrgg_request("v2/team/matches", {"id": team_id, "page": str(page)})
    inner = data.get("data", {})
    if isinstance(inner, dict):
        return inner.get("matches", inner.get("segments", []))
    return inner if isinstance(inner, list) else []


async def fetch_live_matches(team: str) -> list[dict[str, Any]]:
    """Récupère les matchs en direct pour une équipe.

    Utilise l'endpoint global /v2/match?q=live_score car il n'existe pas
    d'endpoint /team/live dédié.

    Args:
        team: Nom de l'équipe (filtrage par nom, insensible à la casse).

    Returns:
        Liste des matchs en direct correspondants.
    """
    data = await vlrgg_request("v2/match", {"q": "live_score"})
    segments = data.get("data", {}).get("segments", [])
    return filter_team_matches(team, segments)


async def fetch_upcoming_matches(team: str) -> list[dict[str, Any]]:
    """Récupère les matchs à venir pour une équipe.

    Utilise l'endpoint global /v2/match?q=upcoming car /v2/team/matches
    ne retourne que les matchs terminés.

    Args:
        team: Nom de l'équipe (filtrage par nom, insensible à la casse).

    Returns:
        Liste des matchs à venir, avec champs normalisés pour l'affichage.
    """
    data = await vlrgg_request("v2/match", {"q": "upcoming"})
    segments = data.get("data", {}).get("segments", [])
    matches = filter_team_matches(team, segments)
    # Normaliser les champs pour correspondre au format attendu par le tracker
    for m in matches:
        m["round_info"] = expand_round_name(m.get("match_series", ""))
        m["status"] = "upcoming"
        m["match_id"] = extract_match_id_from_url(m.get("match_page", ""))
    return matches


async def fetch_all_team_data(team_id: str, team: str) -> dict[str, Any]:
    """Récupère toutes les données de matchs pour une équipe.

    - Results: via /v2/team/matches (par team_id, fiable)
    - Upcoming: via /v2/match?q=upcoming (global, filtré par nom)
    - Live: via /v2/match?q=live_score (global, filtré par nom)

    Args:
        team_id: ID VLR.gg de l'équipe.
        team: Nom de l'équipe (pour le filtrage des matchs upcoming/live).

    Returns:
        Dictionnaire avec 'results', 'upcoming', 'live'.
    """
    result: dict[str, Any] = {"results": [], "upcoming": [], "live": []}

    # Résultats via /team/matches (ne retourne que les matchs terminés)
    try:
        team_matches = await fetch_team_matches_by_id(team_id)
        result["results"] = [normalize_team_match(m) for m in team_matches]
        logger.debug(f"VLR.gg /team/matches: {len(result['results'])} résultats pour ID {team_id}")

        # Enrichir avec /match/details pour dates & rounds plus précis
        result["results"] = await _enrich_matches(result["results"], max_count=6)
    except Exception as e:
        logger.warning(f"VLR.gg fetch_team_matches_by_id échoué: {e}")

    # Upcoming via endpoint global (filtré par nom d'équipe)
    try:
        result["upcoming"] = await fetch_upcoming_matches(team)
        logger.debug(f"VLR.gg /match?q=upcoming: {len(result['upcoming'])} à venir pour {team}")
    except Exception as e:
        logger.warning(f"VLR.gg fetch_upcoming_matches échoué: {e}")

    # Live via endpoint global (filtré par nom d'équipe)
    try:
        result["live"] = await fetch_live_matches(team)
    except Exception as e:
        logger.warning(f"VLR.gg fetch_live_matches échoué: {e}")

    return result


async def check_api_health() -> bool:
    """Vérifie la santé de l'API VLR.gg."""
    try:
        data = await vlrgg_request("v2/health")
        return data.get("status") == "success" or bool(data)
    except Exception:
        return False
