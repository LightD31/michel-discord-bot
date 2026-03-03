"""Client pour l'API non-officielle VLR.gg (V2).

Source unique pour les données de matchs Valorant.
API: https://vlrggapi.vercel.app (https://github.com/axsddlr/vlrggapi)

Endpoints V2 utilisés:
- /v2/match?q=results|upcoming|live_score — listes de matchs
- /v2/match/details?match_id=X — détails complets d'un match
- /v2/team?id=X — profil d'équipe
- /v2/team/matches?id=X — historique des matchs d'une équipe (avec match_id)

Note: Un cache avec TTL variable est utilisé pour minimiser le nombre de requêtes.
"""

import re
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from src.utils import fetch
from src import logutil

logger = logutil.init_logger(__name__)

VLRGG_API_URL = "https://vlrggapi.vercel.app"

# Cache TTL en secondes par type d'endpoint
CACHE_TTL = {
    "live": 30,
    "upcoming": 120,
    "results": 300,
    "details": 120,
    "team": 600,
    "default": 120,
}

# Cache interne : { "endpoint:params_hash" -> (timestamp, data) }
_cache: Dict[str, tuple] = {}


def _cache_key(endpoint: str, params: Optional[Dict[str, str]]) -> str:
    """Génère une clé de cache unique pour un endpoint + params."""
    params_str = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    return f"{endpoint}?{params_str}"


def _get_ttl(endpoint: str, params: Optional[Dict[str, str]] = None) -> int:
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


async def vlrgg_request(
    endpoint: str, params: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
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
        data: Dict[str, Any] = await fetch(url, params=params, return_type="json")  # type: ignore[assignment]
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
    """Obtient le timestamp du résultat le plus récent."""
    if not results:
        return None
    time_str = results[0].get("time_completed", "")
    return parse_time_ago(time_str)


def extract_match_id_from_url(url: str) -> Optional[str]:
    """Extrait l'ID d'un match depuis une URL VLR.gg.

    Ex: "https://www.vlr.gg/412345/team1-vs-team2" -> "412345"
    """
    if not url:
        return None
    m = re.search(r"vlr\.gg/(\d+)", url)
    return m.group(1) if m else None


def normalize_team_match(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Convertit une entrée /v2/team/matches en format compatible avec les résultats globaux.

    Les endpoints globaux (/v2/match?q=results) utilisent des champs plats
    (team1, team2, score1, score2) tandis que /v2/team/matches utilise des
    objets imbriqués ({"name": ..., "tag": ...}) et un score combiné ("2:3").

    Args:
        entry: Entrée brute depuis /v2/team/matches.

    Returns:
        Dictionnaire normalisé avec les mêmes clés que les résultats globaux.
    """
    t1 = entry.get("team1", {})
    t2 = entry.get("team2", {})
    team1_name = t1.get("name", "???") if isinstance(t1, dict) else str(t1)
    team2_name = t2.get("name", "???") if isinstance(t2, dict) else str(t2)

    score_str = entry.get("score", "0:0")
    parts = score_str.split(":")
    score1 = parts[0].strip() if len(parts) >= 2 else "?"
    score2 = parts[1].strip() if len(parts) >= 2 else "?"

    return {
        "team1": team1_name,
        "team2": team2_name,
        "score1": score1,
        "score2": score2,
        "match_page": entry.get("url", ""),
        "tournament_name": entry.get("event", ""),
        "time_completed": entry.get("date", ""),
        "match_id": entry.get("match_id", ""),
        "_source": "team_matches",
    }


# ── Endpoints V2 ─────────────────────────────────────────────────


async def fetch_match_details(match_id: str) -> Dict[str, Any]:
    """Récupère les détails complets d'un match par son ID.

    Inclut: scores par map, stats joueurs, rounds, economy, head-to-head.

    Args:
        match_id: ID VLR.gg du match.

    Returns:
        Données complètes du match, ou dict vide en cas d'erreur.
    """
    data = await vlrgg_request("v2/match/details", {"match_id": match_id})
    return data.get("data", data)


async def fetch_team_info(team_id: str) -> Dict[str, Any]:
    """Récupère le profil d'une équipe par son ID VLR.gg."""
    data = await vlrgg_request("v2/team", {"id": team_id})
    return data.get("data", data)


async def fetch_team_matches_by_id(
    team_id: str, page: int = 1
) -> List[Dict[str, Any]]:
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


async def fetch_team_results(
    team: str, team_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Récupère les résultats récents pour une équipe."""
    data = await vlrgg_request("v2/match", {"q": "results"})
    segments = data.get("data", {}).get("segments", [])
    return filter_team_matches(team, segments)


async def fetch_team_upcoming(
    team: str, team_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Récupère les matchs à venir pour une équipe."""
    data = await vlrgg_request("v2/match", {"q": "upcoming"})
    segments = data.get("data", {}).get("segments", [])
    return filter_team_matches(team, segments)


async def fetch_team_live(
    team: str, team_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Récupère les matchs en direct pour une équipe."""
    data = await vlrgg_request("v2/match", {"q": "live_score"})
    segments = data.get("data", {}).get("segments", [])
    return filter_team_matches(team, segments)


async def fetch_all_team_data(
    team: str, team_id: Optional[str] = None
) -> Dict[str, Any]:
    """Récupère toutes les données de matchs pour une équipe.

    Si team_id est fourni, récupère aussi l'historique via /team/matches
    (qui inclut les match_id pour les détails).

    Args:
        team: Nom de l'équipe.
        team_id: ID VLR.gg de l'équipe (optionnel mais recommandé).

    Returns:
        Dictionnaire avec 'results', 'upcoming', 'live', et optionnellement 'team_matches'.
    """
    result: Dict[str, Any] = {"results": [], "upcoming": [], "live": []}

    if team_id:
        try:
            team_matches = await fetch_team_matches_by_id(team_id)
            result["team_matches"] = team_matches
            logger.debug(
                f"VLR.gg /team/matches: {len(team_matches)} matchs pour ID {team_id}"
            )
        except Exception as e:
            logger.warning(f"VLR.gg fetch_team_matches_by_id échoué: {e}")

    try:
        result["results"] = await fetch_team_results(team, team_id)
    except Exception as e:
        logger.warning(f"VLR.gg fetch_team_results échoué: {e}")

    try:
        result["upcoming"] = await fetch_team_upcoming(team, team_id)
    except Exception as e:
        logger.warning(f"VLR.gg fetch_team_upcoming échoué: {e}")

    try:
        result["live"] = await fetch_team_live(team, team_id)
    except Exception as e:
        logger.warning(f"VLR.gg fetch_team_live échoué: {e}")

    # Fallback: si les résultats globaux sont vides, utiliser team_matches
    if not result["results"] and result.get("team_matches"):
        logger.info(
            f"VLR.gg: résultats globaux vides pour {team}, "
            f"utilisation de /team/matches ({len(result['team_matches'])} entrées)"
        )
        result["results"] = [
            normalize_team_match(m) for m in result["team_matches"]
        ]

    return result


async def check_api_health() -> bool:
    """Vérifie la santé de l'API VLR.gg."""
    try:
        data = await vlrgg_request("v2/health")
        return data.get("status") == "success" or bool(data)
    except Exception:
        return False
