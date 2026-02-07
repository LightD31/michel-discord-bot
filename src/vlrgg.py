"""Client pour l'API non-officielle VLR.gg.

Utilisé comme source alternative/failover pour les données de matchs Valorant
lorsque l'API Liquipedia est indisponible ou a des données moins récentes.

API: https://vlrggapi.vercel.app (https://github.com/axsddlr/vlrggapi)
"""

import asyncio
import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from src.utils import fetch
from src import logutil

logger = logutil.init_logger(__name__)

VLRGG_API_URL = "https://vlrggapi.vercel.app"


async def vlrgg_request(
    endpoint: str, params: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Effectue une requête vers l'API VLR.gg.

    Args:
        endpoint: Point d'entrée API (ex: "match").
        params: Paramètres de requête.

    Returns:
        Données JSON de la réponse, ou dict vide en cas d'erreur.
    """
    url = f"{VLRGG_API_URL}/{endpoint}"
    try:
        data = await fetch(url, params=params, return_type="json")
        return data
    except Exception as e:
        logger.error(f"Erreur API VLR.gg pour {endpoint}: {e}")
        return {}


def filter_team_matches(team: str, matches: List[Dict]) -> List[Dict]:
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
        if team_lower in m.get("team1", "").lower()
        or team_lower in m.get("team2", "").lower()
    ]


def parse_time_ago(time_str: str) -> Optional[datetime]:
    """Parse un temps relatif comme '2h 44m ago' en datetime.

    Args:
        time_str: Chaîne de temps relatif (ex: "2h 44m ago", "30m ago").

    Returns:
        datetime correspondant, ou None si le parsing échoue.
    """
    if not time_str:
        return None

    try:
        total_minutes = 0
        # Extraire heures
        hours_match = re.search(r"(\d+)\s*h", time_str)
        if hours_match:
            total_minutes += int(hours_match.group(1)) * 60
        # Extraire minutes
        mins_match = re.search(r"(\d+)\s*m", time_str)
        if mins_match:
            total_minutes += int(mins_match.group(1))
        # Extraire jours
        days_match = re.search(r"(\d+)\s*d", time_str)
        if days_match:
            total_minutes += int(days_match.group(1)) * 1440

        if total_minutes > 0:
            return datetime.now() - timedelta(minutes=total_minutes)
    except (ValueError, AttributeError):
        pass

    return None


def parse_vlrgg_timestamp(timestamp_str: str) -> Optional[datetime]:
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


def get_most_recent_result_time(results: List[Dict]) -> Optional[datetime]:
    """Obtient le timestamp du résultat le plus récent.

    Args:
        results: Liste des résultats VLR.gg.

    Returns:
        datetime du résultat le plus récent, ou None.
    """
    if not results:
        return None

    # Le premier résultat est le plus récent
    time_str = results[0].get("time_completed", "")
    return parse_time_ago(time_str)


async def fetch_team_results(team: str) -> List[Dict[str, Any]]:
    """Récupère les résultats récents pour une équipe.

    Args:
        team: Nom de l'équipe.

    Returns:
        Liste des résultats correspondants.
    """
    data = await vlrgg_request("match", {"q": "results"})
    segments = data.get("data", {}).get("segments", [])
    return filter_team_matches(team, segments)


async def fetch_team_upcoming(team: str) -> List[Dict[str, Any]]:
    """Récupère les matchs à venir pour une équipe.

    Args:
        team: Nom de l'équipe.

    Returns:
        Liste des matchs à venir correspondants.
    """
    data = await vlrgg_request("match", {"q": "upcoming"})
    segments = data.get("data", {}).get("segments", [])
    return filter_team_matches(team, segments)


async def fetch_team_live(team: str) -> List[Dict[str, Any]]:
    """Récupère les matchs en direct pour une équipe.

    Args:
        team: Nom de l'équipe.

    Returns:
        Liste des matchs en direct correspondants.
    """
    data = await vlrgg_request("match", {"q": "live_score"})
    segments = data.get("data", {}).get("segments", [])
    return filter_team_matches(team, segments)


async def fetch_all_team_data(team: str) -> Dict[str, Any]:
    """Récupère toutes les données de matchs pour une équipe.

    Effectue les 3 requêtes (résultats, à venir, en direct) en parallèle.

    Args:
        team: Nom de l'équipe.

    Returns:
        Dictionnaire avec les clés 'results', 'upcoming', et 'live'.
    """
    results_data, upcoming_data, live_data = await asyncio.gather(
        fetch_team_results(team),
        fetch_team_upcoming(team),
        fetch_team_live(team),
        return_exceptions=True,
    )

    return {
        "results": results_data if isinstance(results_data, list) else [],
        "upcoming": upcoming_data if isinstance(upcoming_data, list) else [],
        "live": live_data if isinstance(live_data, list) else [],
    }


async def check_api_health() -> bool:
    """Vérifie la santé de l'API VLR.gg.

    Returns:
        True si l'API est accessible, False sinon.
    """
    try:
        data = await vlrgg_request("health")
        api_status = data.get(VLRGG_API_URL, {}).get("status", "")
        return api_status == "Healthy"
    except Exception:
        return False
