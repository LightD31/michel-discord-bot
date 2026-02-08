"""Client pour l'API non-officielle VLR.gg.

Utilisé comme source alternative/failover pour les données de matchs Valorant
lorsque l'API Liquipedia est indisponible ou a des données moins récentes.

API: https://vlrggapi.vercel.app (https://github.com/axsddlr/vlrggapi)

Note: L'API VLR.gg a un rate limit plus bas. Un cache avec TTL est utilisé
pour minimiser le nombre de requêtes.
"""

import re
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from src.utils import fetch
from src import logutil

logger = logutil.init_logger(__name__)

VLRGG_API_URL = "https://vlrggapi.vercel.app"

# Cache TTL en secondes — une seule requête par endpoint dans cette fenêtre
CACHE_TTL_SECONDS = 120  # 2 minutes

# Cache interne : { "endpoint:params_hash" -> (timestamp, data) }
_cache: Dict[str, tuple] = {}


def _cache_key(endpoint: str, params: Optional[Dict[str, str]]) -> str:
    """Génère une clé de cache unique pour un endpoint + params."""
    params_str = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    return f"{endpoint}?{params_str}"


async def vlrgg_request(
    endpoint: str, params: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Effectue une requête vers l'API VLR.gg avec cache.

    Les réponses sont mises en cache pendant CACHE_TTL_SECONDS pour
    respecter le rate limit de l'API.

    Args:
        endpoint: Point d'entrée API (ex: "match").
        params: Paramètres de requête.

    Returns:
        Données JSON de la réponse, ou dict vide en cas d'erreur.
    """
    key = _cache_key(endpoint, params)

    # Vérifier le cache
    if key in _cache:
        cached_time, cached_data = _cache[key]
        age = time.monotonic() - cached_time
        if age < CACHE_TTL_SECONDS:
            logger.debug(f"VLR.gg cache hit pour {key} (âge: {age:.0f}s)")
            return cached_data

    url = f"{VLRGG_API_URL}/{endpoint}"
    try:
        data = await fetch(url, params=params, return_type="json")
        _cache[key] = (time.monotonic(), data)
        return data
    except Exception as e:
        logger.error(f"Erreur API VLR.gg pour {endpoint}: {e}")
        # Renvoyer le cache expiré plutôt que rien si on en a un
        if key in _cache:
            logger.warning(f"Utilisation du cache expiré pour {key}")
            return _cache[key][1]
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

    Les requêtes sont effectuées séquentiellement pour respecter le rate limit
    de l'API VLR.gg. Le cache intégré évite les requêtes redondantes.

    Args:
        team: Nom de l'équipe.

    Returns:
        Dictionnaire avec les clés 'results', 'upcoming', et 'live'.
    """
    result: Dict[str, Any] = {"results": [], "upcoming": [], "live": []}

    try:
        result["results"] = await fetch_team_results(team)
    except Exception as e:
        logger.warning(f"VLR.gg fetch_team_results échoué: {e}")

    try:
        result["upcoming"] = await fetch_team_upcoming(team)
    except Exception as e:
        logger.warning(f"VLR.gg fetch_team_upcoming échoué: {e}")

    try:
        result["live"] = await fetch_team_live(team)
    except Exception as e:
        logger.warning(f"VLR.gg fetch_team_live échoué: {e}")

    return result


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
