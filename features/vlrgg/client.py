"""Client pour l'API non-officielle VLR.gg (V2).

Source unique pour les données de matchs Valorant.
L'URL de l'API (déploiement self-hosted) est configurée dans la section
« VLR.gg » de la config globale — aucune URL n'est codée en dur ici.

Endpoints V2 utilisés:
- /v2/team?id=X&q=matches — historique des matchs terminés d'une équipe
- /v2/team?id=X&q=schedule — calendrier d'une équipe (mêlé aux derniers résultats)
- /v2/team?id=X&q=stats — bilan par map d'une équipe
- /v2/team?id=X — profil d'équipe (nom officiel, logo, classement, roster)
- /v2/match?q=upcoming — matchs à venir de la page d'accueil (heure UTC)
- /v2/match?q=live_score — matchs en direct
- /v2/match/details?match_id=X&light=true — détails d'un match (sans les
  onglets performance/economy, inutilisés ici)
- /v2/rankings?region=X — classement régional
- /v2/search?q=X — recherche d'équipes

Note: Un cache avec TTL court est utilisé (API self-hosted). L'API limite
/team et /match/details à 20 requêtes par minute et par client, d'où des TTL
plus longs pour les données qui bougent peu, et la mémorisation sans
expiration des détails d'un match terminé.
"""

import asyncio
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

from features.vlrgg.matching import (
    extract_match_id_from_url,
    is_final,
    live_candidates,
    match_id_of,
    team_side,
)
from features.vlrgg.normalize import (
    enrich_match_from_details,
    normalize_team_match,
    upcoming_from_schedule,
)
from src.core import logging as logutil
from src.core.config import config_store
from src.core.errors import HttpError
from src.core.http import fetch

logger = logutil.init_logger(__name__)


def get_api_url() -> str:
    """Base URL of the VLR.gg API deployment, from the global config.

    Read on each call so a dashboard edit applies without a restart. Empty
    when unconfigured — callers then skip the request instead of hitting a
    hardcoded host.
    """
    section = config_store.get().get("config", {}).get("vlrgg") or {}
    return str(section.get("vlrggApiUrl") or "").strip().rstrip("/")


# Limite de requêtes concurrentes vers l'API (éviter les 502 sur le serveur self-hosted)
_api_semaphore = asyncio.Semaphore(2)


class SlidingWindowLimiter:
    """Au plus ``limit`` passages par fenêtre glissante de ``window`` secondes.

    Les appelants en trop attendent leur tour (dans l'ordre d'arrivée) au lieu
    de recevoir un 429 de l'API. :meth:`hold` suspend tous les passages pendant
    un délai : l'API peut compter dans le même budget d'autres clients (derrière
    un reverse proxy, tous partagent son adresse), qu'aucun compteur local ne voit.
    """

    def __init__(
        self,
        limit: int,
        window: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._resume_at = 0.0
        self._lock = asyncio.Lock()

    def hold(self, seconds: float) -> None:
        """Ne laisse plus rien passer pendant ``seconds`` (après un 429)."""
        self._resume_at = max(self._resume_at, self._clock() + seconds)

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                if now < self._resume_at:
                    await self._sleep(self._resume_at - now)
                    continue
                while self._stamps and now - self._stamps[0] >= self._window:
                    self._stamps.popleft()
                if len(self._stamps) < self._limit:
                    self._stamps.append(now)
                    return
                await self._sleep(self._window - (now - self._stamps[0]))


# L'API accepte 20 requêtes/min par client sur /team et /match/details (budget
# commun) : au démarrage, chaque équipe en demande ~9 (profil, historique,
# calendrier, détails des derniers matchs). On garde une marge sous ce plafond.
_rate_limited = SlidingWindowLimiter(limit=18, window=60.0)
# Pause imposée à ce budget quand l'API répond 429 malgré tout
_RATE_LIMIT_BACKOFF_SECONDS = 20.0


def _is_rate_limited(endpoint: str) -> bool:
    return endpoint.startswith(("v2/team", "v2/match/details"))


# Cache TTL en secondes par type d'endpoint (valeurs basses, API self-hosted)
CACHE_TTL = {
    "live": 10,
    "upcoming": 30,
    "results": 60,
    "details": 30,
    "team": 120,
    "team_profile": 1800,
    "team_stats": 1800,
    "rankings": 3600,
    "search": 300,
    "default": 30,
}

# Cache interne : { "endpoint:params_hash" -> (timestamp, data) }
_cache: dict[str, tuple] = {}
# Au-delà, on oublie la moitié la plus ancienne (la recherche crée une clé par saisie)
_CACHE_MAX_ENTRIES = 512

# Détails des matchs terminés : figés, donc gardés sans expiration (LRU borné)
_final_details: OrderedDict[str, dict[str, Any]] = OrderedDict()
_FINAL_DETAILS_MAX = 128


def _cache_key(endpoint: str, params: dict[str, str] | None) -> str:
    """Génère une clé de cache unique pour un endpoint + params."""
    params_str = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    return f"{endpoint}?{params_str}"


def _get_ttl(endpoint: str, params: dict[str, str] | None = None) -> int:
    """Détermine le TTL de cache approprié pour un endpoint."""
    q = (params or {}).get("q", "")
    if "match/details" in endpoint:
        return CACHE_TTL["details"]
    if endpoint.endswith("team"):
        if q in ("", "profile"):
            return CACHE_TTL["team_profile"]
        if q == "stats":
            return CACHE_TTL["team_stats"]
        return CACHE_TTL["team"]
    if endpoint.endswith("rankings"):
        return CACHE_TTL["rankings"]
    if endpoint.endswith("search"):
        return CACHE_TTL["search"]
    if q == "live_score":
        return CACHE_TTL["live"]
    if q == "upcoming":
        return CACHE_TTL["upcoming"]
    if q == "results":
        return CACHE_TTL["results"]
    return CACHE_TTL["default"]


def _cache_store(key: str, data: dict[str, Any]) -> None:
    _cache[key] = (time.monotonic(), data)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        oldest = sorted(_cache, key=lambda k: _cache[k][0])
        for stale in oldest[: len(_cache) - _CACHE_MAX_ENTRIES // 2]:
            del _cache[stale]


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

    api_url = get_api_url()
    if not api_url:
        logger.warning(
            "VLR.gg: aucune URL d'API configurée (config globale → VLR.gg) — requête ignorée."
        )
        return {}

    url = f"{api_url}/{endpoint}"
    try:
        if _is_rate_limited(endpoint):
            await _rate_limited.acquire()
        async with _api_semaphore:
            data: dict[str, Any] = await fetch(url, params=params, return_type="json")
        _cache_store(key, data)
        return data
    except Exception as e:
        logger.error(f"Erreur API VLR.gg pour {endpoint}: {e}")
        if isinstance(e, HttpError) and e.status == 429 and _is_rate_limited(endpoint):
            _rate_limited.hold(_RATE_LIMIT_BACKOFF_SECONDS)
        # Renvoyer le cache expiré plutôt que rien si on en a un
        if key in _cache:
            logger.warning(f"Utilisation du cache expiré pour {key}")
            return _cache[key][1]
        return {}


def _segments(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Liste ``data.segments`` d'une réponse V2 (vide si absente ou d'un autre type)."""
    inner = data.get("data", {})
    if isinstance(inner, dict):
        segments = inner.get("segments", [])
        return segments if isinstance(segments, list) else []
    return inner if isinstance(inner, list) else []


def _first_segment(data: dict[str, Any]) -> dict[str, Any]:
    """Premier segment d'une réponse V2 à objet unique (profil, détails)."""
    inner = data.get("data", data)
    if isinstance(inner, dict):
        segments = inner.get("segments", [])
        if segments and isinstance(segments, list):
            return segments[0]
        return inner
    return {}


async def _enrich_matches(
    matches: list[dict[str, Any]], team_id: str | None = None, max_count: int = 6
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
        if isinstance(details, BaseException):
            logger.warning(f"Échec enrichissement match {_match_id}: {details}")
            continue
        if isinstance(details, dict) and details:
            matches[idx] = enrich_match_from_details(matches[idx], details, team_id)

    return matches


# ── Endpoints V2 ─────────────────────────────────────────────────


async def fetch_match_details(match_id: str) -> dict[str, Any]:
    """Récupère les détails d'un match par son ID.

    Inclut: scores par map, picks, veto, stats joueurs, rounds. ``light``
    évite à l'API de scraper les onglets performance/economy de chaque map
    (une requête vers vlr.gg au lieu de 1 + 2 par map) : le bot ne les lit pas.
    Une fois le match terminé, le résultat est mémorisé sans expiration.

    Args:
        match_id: ID VLR.gg du match.

    Returns:
        Données du match, ou dict vide en cas d'erreur.
    """
    if match_id in _final_details:
        _final_details.move_to_end(match_id)
        return _final_details[match_id]

    data = await vlrgg_request("v2/match/details", {"match_id": match_id, "light": "true"})
    details = _first_segment(data)
    if isinstance(details, dict) and is_final(details):
        _final_details[match_id] = details
        while len(_final_details) > _FINAL_DETAILS_MAX:
            _final_details.popitem(last=False)
    return details


async def fetch_team_profile(team_id: str) -> dict[str, Any]:
    """Profil d'une équipe : nom officiel, tag, logo, classement, roster, gains."""
    return _first_segment(await vlrgg_request("v2/team", {"id": team_id}))


async def fetch_team_matches_by_id(team_id: str, page: int = 1) -> list[dict[str, Any]]:
    """Récupère l'historique des matchs d'une équipe par son ID.

    L'ancien endpoint /v2/team/matches a été fusionné dans /v2/team (q=matches).
    Chaque match inclut un match_id utilisable avec fetch_match_details().

    Args:
        team_id: ID VLR.gg de l'équipe.
        page: Numéro de page (1-based).

    Returns:
        Liste des matchs de l'équipe.
    """
    data = await vlrgg_request("v2/team", {"id": team_id, "q": "matches", "page": str(page)})
    return _segments(data)


async def fetch_team_schedule(team_id: str) -> list[dict[str, Any]]:
    """Cartes de match de la page d'équipe : matchs à venir *et* derniers résultats.

    Voir :func:`features.vlrgg.normalize.upcoming_from_schedule` pour le tri.
    """
    return _segments(await vlrgg_request("v2/team", {"id": team_id, "q": "schedule"}))


async def fetch_team_map_stats(team_id: str, since: date | None = None) -> list[dict[str, Any]]:
    """Bilan par map d'une équipe, éventuellement limité aux matchs depuis ``since``."""
    params = {"id": team_id, "q": "stats"}
    if since is not None:
        params["date_start"] = since.isoformat()
    return _segments(await vlrgg_request("v2/team", params))


async def fetch_rankings(region: str) -> list[dict[str, Any]]:
    """Classement VLR.gg d'une région (codes de /v2/rankings: eu, na, ap...)."""
    return _segments(await vlrgg_request("v2/rankings", {"region": region}))


async def search_teams(query: str) -> list[dict[str, Any]]:
    """Équipes dont le nom correspond à ``query`` ({id, name, img, ...})."""
    data = await vlrgg_request("v2/search", {"q": query})
    inner = data.get("data", {})
    segments = inner.get("segments", {}) if isinstance(inner, dict) else {}
    results = segments.get("results", {}) if isinstance(segments, dict) else {}
    teams = results.get("teams", []) if isinstance(results, dict) else []
    return teams if isinstance(teams, list) else []


async def fetch_live_matches() -> list[dict[str, Any]]:
    """Tous les matchs en direct (/v2/match?q=live_score), sans filtrage."""
    return _segments(await vlrgg_request("v2/match", {"q": "live_score"}))


async def fetch_upcoming_matches() -> list[dict[str, Any]]:
    """Matchs à venir de la page d'accueil (/v2/match?q=upcoming), sans filtrage.

    La liste est courte (quelques matchs) : elle sert surtout à obtenir l'heure
    UTC et le nom de série des matchs proches du calendrier d'une équipe.
    """
    return _segments(await vlrgg_request("v2/match", {"q": "upcoming"}))


async def _gathered(label: str, coro: Any, default: Any) -> Any:
    try:
        return await coro
    except Exception as e:
        logger.warning(f"VLR.gg {label} échoué: {e}")
        return default


async def fetch_all_team_data(team_id: str, team: str = "") -> dict[str, Any]:
    """Récupère toutes les données de matchs pour une équipe, par son ID.

    - Results: /v2/team?q=matches, enrichis par /v2/match/details
    - Upcoming: /v2/team?q=schedule, débarrassé des matchs terminés, avec l'heure
      UTC de /v2/match?q=upcoming quand le match y figure
    - Live: /v2/match?q=live_score, rattaché par ID de match ou nom officiel,
      puis confirmé par les IDs d'équipe des détails du match

    Args:
        team_id: ID VLR.gg de l'équipe.
        team: Nom configuré, utilisé seulement si le profil est indisponible.

    Returns:
        Dictionnaire avec 'profile', 'results', 'upcoming', 'live'.
    """
    profile, team_matches, schedule, homepage, live_segments = await asyncio.gather(
        _gathered("profil", fetch_team_profile(team_id), {}),
        _gathered("historique", fetch_team_matches_by_id(team_id), []),
        _gathered("calendrier", fetch_team_schedule(team_id), []),
        _gathered("page d'accueil", fetch_upcoming_matches(), []),
        _gathered("live", fetch_live_matches(), []),
    )
    vlr_name = str(profile.get("name") or team)

    results = [normalize_team_match(m) for m in team_matches]
    logger.debug(f"VLR.gg /team?q=matches: {len(results)} résultats pour ID {team_id}")
    try:
        # Enrichir avec /match/details pour tournoi, rounds et scores par map
        results = await _enrich_matches(results, team_id, max_count=6)
    except Exception as e:
        logger.warning(f"VLR.gg enrichissement des résultats échoué: {e}")

    schedule_ids = {match_id_of(f) for f in schedule} - {""}
    live = []
    for seg in live_candidates(live_segments, schedule_ids, vlr_name):
        mid = match_id_of(seg)
        details = await fetch_match_details(mid) if mid else {}
        if details.get("teams") and team_side(details, team_id) is None:
            continue  # Même nom ou calendrier obsolète, mais pas cette équipe
        live.append(seg)

    exclude = {str(r.get("match_id") or "") for r in results} | {match_id_of(m) for m in live}
    upcoming = upcoming_from_schedule(
        schedule,
        exclude_ids=exclude - {""},
        homepage=homepage,
        vlr_name=vlr_name,
        now=datetime.now(UTC),
    )
    logger.debug(f"VLR.gg: {len(upcoming)} à venir, {len(live)} en direct pour {vlr_name}")

    return {"profile": profile, "results": results, "upcoming": upcoming, "live": live}


async def check_api_health() -> bool:
    """Vérifie la santé de l'API VLR.gg."""
    try:
        data = await vlrgg_request("v2/health")
        return data.get("status") == "success" or bool(data)
    except Exception:
        return False
