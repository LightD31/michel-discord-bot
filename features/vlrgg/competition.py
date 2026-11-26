"""La compétition que joue une équipe suivie : son classement ou son arbre.

L'API ne rattache pas un match à son événement par identifiant : ni le
calendrier d'une équipe, ni ``/v2/match/details`` ne portent d'``event_id``.
L'URL d'un match, en revanche, reprend le slug de son événement
(``/794456/eintracht-frankfurt-vs-giantx-vct-2027-emea-kickoff-europe-qualifier-playoffs-swiss-stage-r2-1-0``
pour l'événement ``/event/3233/vct-2027-emea-kickoff-europe-qualifier-playoffs``) :
on cherche donc, parmi les événements en cours, ceux dont le slug figure dans
l'URL du match, puis on le confirme par l'ID du match dans
``/v2/events/matches``, qui donne aussi sa phase (``stage``).

Le classement vient de ``/v2/event/{id}?stage=…`` (une table par groupe),
l'arbre des matchs de la phase, rangés par tour (``event_series``) : tableau
supérieur, tableau inférieur, puis le reste (grande finale), chacun dans
l'ordre où l'API liste ses tours.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from features.vlrgg.matching import match_id_of, same_team_name

CompetitionView = Literal["off", "standings", "bracket"]
COMPETITION_VIEWS: tuple[CompetitionView, ...] = ("off", "standings", "bracket")

_EVENT_PATH = re.compile(r"/event/(\d+)/([a-z0-9-]+)")
_MATCH_PATH = re.compile(r"vlr\.gg/\d+/([a-z0-9-]+)")


def competition_view(raw: object) -> CompetitionView:
    """Le réglage ``competitionView`` tel qu'écrit, ``off`` s'il est inconnu."""
    for view in COMPETITION_VIEWS:
        if raw == view:
            return view
    return "off"


def event_slug(url: str) -> tuple[str, str] | None:
    """``(event_id, slug)`` depuis l'URL d'un événement VLR.gg."""
    m = _EVENT_PATH.search(url or "")
    return (m.group(1), m.group(2)) if m else None


def candidate_events(match_url: str, events: Iterable[dict[str, Any]]) -> list[str]:
    """IDs des événements dont le slug figure dans l'URL du match, le plus précis d'abord.

    « vct-2027-emea-kickoff » et « vct-2027-emea-kickoff-europe-qualifier-playoffs »
    figurent tous deux dans l'URL d'un match des playoffs : le plus long l'emporte.
    """
    m = _MATCH_PATH.search(match_url or "")
    if not m:
        return []
    padded = f"-{m.group(1)}-"
    found: dict[str, int] = {}
    for event in events:
        parsed = event_slug(str(event.get("url_path") or ""))
        if parsed is None:
            continue
        event_id, slug = parsed
        if f"-{slug}-" in padded:
            found[event_id] = max(found.get(event_id, 0), len(slug))
    return sorted(found, key=lambda eid: -found[eid])


def stage_of(match_id: str, event_matches: Iterable[dict[str, Any]]) -> str | None:
    """Nom de la phase du match ``match_id`` dans l'événement, None s'il n'y figure pas."""
    for entry in event_matches:
        if match_id and match_id_of(entry) == match_id:
            return str(entry.get("stage") or "")
    return None


def stage_slug(stage: str, stages: Iterable[dict[str, Any]]) -> str:
    """Slug de la phase nommée ``stage`` dans la liste ``stages`` du détail d'événement."""
    for entry in stages:
        if same_team_name(stage, str(entry.get("name") or "")):
            return str(entry.get("slug") or "")
    return ""


def is_active_stage(stage: str, stages: Iterable[dict[str, Any]]) -> bool:
    """Vrai quand ``stage`` est la phase que l'API sert par défaut."""
    return any(
        same_team_name(stage, str(entry.get("name") or "")) and entry.get("active")
        for entry in stages
    )


# ── Classement ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StandingRow:
    rank: int
    team: str
    wins: str
    losses: str
    maps: str = ""
    rounds_diff: str = ""


@dataclass(frozen=True)
class StandingTable:
    title: str  # le groupe, ou la phase quand il n'y en a qu'un
    rows: list[StandingRow]


def standings_tables(standings: Iterable[dict[str, Any]], team: str = "") -> list[StandingTable]:
    """Tables du classement ; seulement celle(s) de ``team`` quand il en a une.

    Les colonnes sont celles de VLR.gg (``Team``, ``W``, ``L``, ``MAP``, ``Δ``…),
    les lignes déjà dans l'ordre du classement.
    """
    tables = []
    for table in standings:
        rows = [
            StandingRow(
                rank=i,
                team=str(row.get("Team") or "?"),
                wins=str(row.get("W") or "0"),
                losses=str(row.get("L") or "0"),
                maps=str(row.get("MAP") or ""),
                rounds_diff=str(row.get("Δ") or ""),
            )
            for i, row in enumerate(table.get("rows") or [], start=1)
            if isinstance(row, dict)
        ]
        if rows:
            title = str(table.get("group") or "").strip() or str(table.get("stage") or "")
            tables.append(StandingTable(title=title, rows=rows))
    if team:
        own = [t for t in tables if any(same_team_name(team, r.team) for r in t.rows)]
        if own:
            return own
    return tables


# ── Arbre ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BracketMatch:
    team1: str
    team2: str
    score1: str
    score2: str
    winner: int | None  # 0 ou 1, None tant que le match n'est pas joué
    status: str  # "Completed", "LIVE", "Upcoming"
    url: str = ""

    @property
    def live(self) -> bool:
        return self.status.strip().lower() == "live"

    @property
    def played(self) -> bool:
        return self.winner is not None or self.status.strip().lower() == "completed"


@dataclass(frozen=True)
class BracketRound:
    name: str
    matches: list[BracketMatch] = field(default_factory=list)


_ROUND_WORDS = {
    "quarterfinals": "Quarts de finale",
    "quarterfinal": "Quart de finale",
    "semifinals": "Demi-finales",
    "semifinal": "Demi-finale",
    "final": "Finale",
    "finals": "Finale",
    "grand final": "Grande Finale",
    "grand finals": "Grande Finale",
}


def round_label(series: str) -> str:
    """« Upper Quarterfinals » → « Quarts de finale · Winner Bracket »."""
    words = series.strip()
    side = ""
    for prefix, label in (("upper ", "Winner Bracket"), ("lower ", "Loser Bracket")):
        if words.casefold().startswith(prefix):
            words, side = words[len(prefix) :], label
            break
    base = _ROUND_WORDS.get(words.casefold(), words)
    return f"{base} · {side}" if side else base


def _bracket_side(series: str) -> int:
    """0 tableau supérieur (ou tours sans tableau), 1 inférieur, 2 le reste (finales)."""
    lowered = series.casefold()
    if lowered.startswith("lower"):
        return 1
    if lowered.startswith("upper") or "final" not in lowered:
        return 0
    return 2


def _side(entry: dict[str, Any], key: str) -> dict[str, Any]:
    side = entry.get(key)
    return side if isinstance(side, dict) else {}


def _bracket_match(entry: dict[str, Any]) -> BracketMatch:
    t1 = _side(entry, "team1")
    t2 = _side(entry, "team2")
    winner = 0 if t1.get("is_winner") else 1 if t2.get("is_winner") else None
    return BracketMatch(
        team1=str(t1.get("name") or "TBD"),
        team2=str(t2.get("name") or "TBD"),
        score1=str(t1.get("score") or ""),
        score2=str(t2.get("score") or ""),
        winner=winner,
        status=str(entry.get("status") or ""),
        url=str(entry.get("url") or ""),
    )


def bracket_rounds(event_matches: Iterable[dict[str, Any]], stage: str) -> list[BracketRound]:
    """Les tours de la phase ``stage`` : tableau supérieur, inférieur, puis finales."""
    rounds: dict[str, BracketRound] = {}
    for entry in event_matches:
        if not same_team_name(stage, str(entry.get("stage") or "")):
            continue
        series = str(entry.get("event_series") or "").strip() or stage
        rounds.setdefault(series, BracketRound(series)).matches.append(_bracket_match(entry))
    ordered = list(rounds.values())  # sorted() est stable : l'ordre de l'API par tableau
    return sorted(ordered, key=lambda r: _bracket_side(r.name))


# ── Ensemble ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Competition:
    event_id: str
    name: str
    stage: str
    url: str = ""
    logo: str = ""
    view: CompetitionView = "bracket"
    tables: list[StandingTable] = field(default_factory=list)
    rounds: list[BracketRound] = field(default_factory=list)


def reference_urls(vlr_data: dict[str, Any]) -> list[tuple[str, str]]:
    """``(match_id, url)`` des matchs qui situent l'équipe : en direct, prochain, dernier."""
    refs: list[tuple[str, str]] = []
    picks = [
        *(vlr_data.get("live") or []),
        *(vlr_data.get("upcoming") or [])[:1],
        *(vlr_data.get("results") or [])[:1],
    ]
    for match in picks:
        url = str(match.get("match_page") or match.get("url") or "")
        mid = match_id_of(match)
        if mid and url and (mid, url) not in refs:
            refs.append((mid, url))
    return refs
