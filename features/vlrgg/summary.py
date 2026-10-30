"""Résumés d'un match ou d'une équipe VLR.gg pour l'affichage (pur, sans réseau).

Les fonctions lisent les payloads de ``/v2/match/details`` et
``/v2/team?q=stats`` sans les modifier : les détails d'un match terminé sont
gardés en mémoire et partagés entre les appelants.
"""

import re
from dataclasses import dataclass
from typing import Any

from features.vlrgg.matching import same_team_name


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None


# ── Maps ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MapScore:
    """Score d'une map, du point de vue de l'ordre demandé."""

    name: str
    score1: str
    score2: str
    picked_by: str = ""
    duration: str = ""

    @property
    def winner(self) -> int | None:
        """0 ou 1 selon le côté gagnant, None si égalité ou score illisible."""
        s1, s2 = _as_int(self.score1), _as_int(self.score2)
        if s1 is None or s2 is None or s1 == s2:
            return None
        return 0 if s1 > s2 else 1


def map_scores(details: dict[str, Any], *, swap: bool = False) -> list[MapScore]:
    """Maps jouées (ou en cours) d'un match, dans l'ordre de la série.

    ``swap`` inverse les deux côtés, pour afficher l'équipe suivie en premier.
    Les maps pas encore commencées (score vide) sont ignorées.
    """
    result = []
    for m in details.get("maps") or []:
        name = str(m.get("map_name") or m.get("map") or "").strip()
        if not name or name.lower() in ("tbd", "n/a"):
            continue
        score = m.get("score") or {}
        if isinstance(score, dict):
            s1, s2 = score.get("team1", ""), score.get("team2", "")
        else:
            s1, s2 = m.get("team1_score", ""), m.get("team2_score", "")
        if str(s1).strip() == "" and str(s2).strip() == "":
            continue
        if swap:
            s1, s2 = s2, s1
        duration = str(m.get("duration") or "").strip()
        result.append(
            MapScore(
                name=name,
                score1=str(s1),
                score2=str(s2),
                picked_by=str(m.get("picked_by") or ""),
                # La map en cours affiche "-" en guise de durée
                duration=duration if duration.strip("-– ") else "",
            )
        )
    return result


def live_round_score(segment: dict[str, Any]) -> tuple[int, int] | None:
    """Rounds gagnés par chaque équipe sur la map en cours (/v2/match?q=live_score).

    L'API donne les rounds par côté et met "N/A" sur le côté pas encore joué
    (avant la mi-temps, une équipe n'a que CT, l'autre que T) : on additionne
    ce qui est connu.
    """
    totals = []
    for team in ("team1", "team2"):
        known = [
            v
            for v in (
                _as_int(segment.get(f"{team}_round_ct")),
                _as_int(segment.get(f"{team}_round_t")),
            )
            if v is not None
        ]
        if not known:
            return None
        totals.append(sum(known))
    return totals[0], totals[1]


# ── Joueurs ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlayerLine:
    """Statistiques cumulées d'un joueur sur les maps d'un match."""

    name: str
    agents: tuple[str, ...]
    rating: float
    acs: float
    kills: int
    deaths: int
    assists: int


def top_players(details: dict[str, Any], n: int = 3) -> list[PlayerLine]:
    """Les ``n`` meilleurs joueurs du match, classés par rating moyen.

    Rating et ACS sont moyennés sur les maps jouées ; kills, morts et assists
    sont cumulés.
    """
    stats: dict[str, dict[str, Any]] = {}
    for map_info in details.get("maps") or []:
        players = map_info.get("players") or {}
        if not isinstance(players, dict):
            continue
        for team_players in players.values():
            if not isinstance(team_players, list):
                continue
            for p in team_players:
                name = str(p.get("name") or p.get("player") or "").strip()
                if not name:
                    continue
                entry = stats.setdefault(
                    name,
                    {"ratings": [], "acs": [], "agents": [], "k": 0, "d": 0, "a": 0},
                )
                rating = _as_float(p.get("rating"))
                if rating is not None:
                    entry["ratings"].append(rating)
                acs = _as_float(p.get("acs", p.get("average_combat_score")))
                if acs is not None:
                    entry["acs"].append(acs)
                agent = str(p.get("agent") or "").strip()
                if agent and agent not in entry["agents"]:
                    entry["agents"].append(agent)
                entry["k"] += _as_int(p.get("kills")) or 0
                entry["d"] += _as_int(p.get("deaths")) or 0
                entry["a"] += _as_int(p.get("assists")) or 0

    lines = [
        PlayerLine(
            name=name,
            agents=tuple(s["agents"]),
            rating=sum(s["ratings"]) / len(s["ratings"]) if s["ratings"] else 0.0,
            acs=sum(s["acs"]) / len(s["acs"]) if s["acs"] else 0.0,
            kills=s["k"],
            deaths=s["d"],
            assists=s["a"],
        )
        for name, s in stats.items()
    ]
    lines.sort(key=lambda p: (p.rating, p.acs), reverse=True)
    return lines[:n]


# ── Veto ──────────────────────────────────────────────────────────────────────

_VETO_STEP = re.compile(r"^(?P<team>.+?)\s+(?P<action>ban|pick)\s+(?P<map>.+)$", re.IGNORECASE)
_VETO_DECIDER = re.compile(r"^(?P<map>.+?)\s+remains$", re.IGNORECASE)


@dataclass(frozen=True)
class VetoStep:
    action: str  # "ban", "pick" ou "decider"
    map: str
    team: str = ""


def veto_steps(details: dict[str, Any]) -> list[VetoStep]:
    """Étapes du veto ("SEN ban Breeze; C9 pick Bind; Haven remains").

    Le champ ``map_vetos`` reprend la note d'en-tête de la page du match : sans
    veto publié, il contient autre chose (patch...) et aucune étape n'est lue.
    """
    steps = []
    for part in str(details.get("map_vetos") or "").split(";"):
        part = part.strip()
        if m := _VETO_STEP.match(part):
            steps.append(VetoStep(m["action"].lower(), m["map"].strip(), m["team"].strip()))
        elif m := _VETO_DECIDER.match(part):
            steps.append(VetoStep("decider", m["map"].strip()))
    return steps


def veto_line(details: dict[str, Any]) -> str:
    """Veto sur une ligne : « 🚫 Split (MIBR.GC) · ✅ Ascent (MIBR.GC) · 🎯 Abyss »."""
    icons = {"ban": "🚫", "pick": "✅", "decider": "🎯"}
    parts = []
    for step in veto_steps(details):
        suffix = f" ({step.team})" if step.team else ""
        parts.append(f"{icons[step.action]} {step.map}{suffix}")
    return " · ".join(parts)


# ── Map pool ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MapRecord:
    """Bilan d'une équipe sur une map (/v2/team?q=stats)."""

    name: str
    games: int
    wins: int
    losses: int

    @property
    def win_pct(self) -> int:
        return round(self.wins * 100 / self.games) if self.games else 0


def map_pool(stats_segments: list[dict[str, Any]], min_games: int = 1) -> list[MapRecord]:
    """Maps jouées au moins ``min_games`` fois, les plus jouées d'abord."""
    records = []
    for seg in stats_segments or []:
        games = _as_int(seg.get("games")) or 0
        if games < max(min_games, 1):
            continue
        records.append(
            MapRecord(
                name=str(seg.get("map") or "?").strip(),
                games=games,
                wins=_as_int(seg.get("wins")) or 0,
                losses=_as_int(seg.get("losses")) or 0,
            )
        )
    records.sort(key=lambda r: (r.games, r.win_pct), reverse=True)
    return records


def compare_map_pools(
    pool_a: list[MapRecord], pool_b: list[MapRecord]
) -> list[tuple[str, MapRecord | None, MapRecord | None]]:
    """Maps jouées par au moins une des deux équipes, les plus jouées d'abord."""
    by_a = {r.name.casefold(): r for r in pool_a}
    by_b = {r.name.casefold(): r for r in pool_b}
    names = {r.name.casefold(): r.name for r in [*pool_a, *pool_b]}
    rows = [(names[key], by_a.get(key), by_b.get(key)) for key in names]
    rows.sort(
        key=lambda row: (row[1].games if row[1] else 0) + (row[2].games if row[2] else 0),
        reverse=True,
    )
    return rows


# ── Face-à-face ───────────────────────────────────────────────────────────────


def head_to_head(results: list[dict[str, Any]], opponent: str, n: int = 5) -> list[dict[str, Any]]:
    """Derniers matchs de l'historique de l'équipe contre ``opponent``.

    Le champ ``head_to_head`` de /v2/match/details n'est pas fiable (il liste
    les derniers matchs d'une seule des deux équipes), on reconstitue donc le
    face-à-face depuis l'historique normalisé de l'équipe suivie (``team2`` est
    toujours l'adversaire).
    """
    return [m for m in results if same_team_name(opponent, str(m.get("team2", "")))][:n]
