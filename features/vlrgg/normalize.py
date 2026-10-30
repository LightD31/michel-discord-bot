"""Normalisation des données VLR.gg (pur, sans réseau).

Convertit les entrées brutes de l'API (historique, calendrier, page
d'accueil, détails) en dictionnaires homogènes pour l'affichage.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from features.vlrgg.matching import match_id_of, same_team_name, team_side
from src.discord_ext.embeds import format_discord_timestamp


def parse_vlrgg_timestamp(timestamp_str: str) -> datetime | None:
    """Parse un timestamp VLR.gg en datetime aware (UTC).

    L'API VLR.gg renvoie ce champ en UTC malgré le format sans suffixe ; on
    attache donc explicitement le fuseau pour éviter une conversion implicite
    en heure locale lors de l'appel ultérieur à ``dt.timestamp()``.

    Args:
        timestamp_str: Chaîne de timestamp (ex: "2024-04-24 21:00:00").

    Returns:
        datetime aware (UTC) correspondant, ou None si le parsing échoue.
    """
    if not timestamp_str:
        return None
    try:
        return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


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

    Ex: "GF" -> "Grande Finale", "UB SF" -> "Demi-finale Winner Bracket",
    "LR2" -> "Round 2 Loser Bracket", "W3" -> "Semaine 3"
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

    # Pattern "UB R1", "LB R2", "UR1", "LR2", etc.
    bracket_match = re.match(r"^(ub|lb|u|l)r?(\d+)$", compact)
    if bracket_match:
        bracket = "Winner" if bracket_match.group(1).startswith("u") else "Loser"
        return f"Round {bracket_match.group(2)} {bracket} Bracket"

    # Semaines de ligue ("W3") et rounds simples ("R2")
    week_match = re.match(r"^w(\d+)$", compact)
    if week_match:
        return f"Semaine {week_match.group(1)}"
    round_match = re.match(r"^r(\d+)$", compact)
    if round_match:
        return f"Round {round_match.group(1)}"

    return round_str


# Abréviation de round collée en fin de texte d'événement par le scraper
# ("GC 26: ChampionshipUBF") : précédée d'une minuscule, d'un chiffre ou d'un
# séparateur pour ne pas couper un nom en majuscules ("EMEA").
_TRAILING_ROUND = re.compile(
    r"(?:(?<=[a-z0-9\s:)\]])|^)((?:[UL]B?)?(?:GF|SF|QF|F|R\d{1,2})|[UL]R\d{1,2}|W\d{1,2})$"
)


def split_event_round(text: str) -> tuple[str, str]:
    """Sépare « événement » et « round » d'un champ ``event`` du calendrier.

    Ex: "GC 26: ChampionshipUBF" -> ("GC 26: Championship", "Finale Winner Bracket"),
    "GF" -> ("", "Grande Finale").
    """
    cleaned = _clean_vlr_text(text)
    m = _TRAILING_ROUND.search(cleaned)
    if not m:
        return cleaned, ""
    return cleaned[: m.start()].strip(), expand_round_name(m.group(1))


# L'API VLR.gg restitue les dates de /v2/team (q=matches, q=schedule) dans
# l'heure locale de son serveur (Europe/Paris) — ex: "8:00 pm" correspond
# à 20h CEST/CET. ZoneInfo gère le passage à l'heure d'été automatiquement.
_VLR_TIMEZONE = ZoneInfo("Europe/Paris")

# Formats de date courants retournés par l'API VLR.gg, et s'ils portent l'heure
_DATE_FORMATS: list[tuple[str, bool]] = [
    ("%Y/%m/%d%I:%M %p", True),  # 2026/02/1111:00 am (anciennes versions, pas d'espace)
    ("%Y/%m/%d %I:%M %p", True),  # 2026/02/11 11:00 am (date + time recombinés)
    ("%Y-%m-%d %H:%M:%S", True),  # 2024-04-24 21:00:00
    ("%Y-%m-%d %H:%M", True),  # 2024-04-24 21:00
    ("%Y/%m/%d", False),  # 2026/02/11
    ("%b %d, %Y", False),  # Feb 11, 2026
]


def parse_vlr_date(date_str: str) -> tuple[datetime, bool] | None:
    """Parse une date VLR.gg (heure de Paris) en ``(datetime aware, heure connue)``."""
    cleaned = (date_str or "").strip()
    if not cleaned:
        return None

    for fmt, has_time in _DATE_FORMATS:
        try:
            dt = datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
        return dt.replace(tzinfo=_VLR_TIMEZONE), has_time

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
            return dt, False
        except ValueError:
            pass
    return None


def format_vlr_date(date_str: str) -> str:
    """Formate une date VLR.gg en timestamp Discord (affichage local pour chaque utilisateur).

    Les dates de /v2/team?q=matches sont rendues dans l'heure locale du serveur
    de l'API (Europe/Paris) ; ZoneInfo gère CET/CEST automatiquement.
    Retourne un timestamp Discord <t:UNIX:f> (ou <t:UNIX:D> sans heure) si le
    parsing réussit, sinon retourne la chaîne telle quelle.
    """
    if not date_str:
        return "?"
    parsed = parse_vlr_date(date_str)
    if parsed is None:
        return date_str.strip()
    dt, has_time = parsed
    return format_discord_timestamp(dt, "f" if has_time else "D")


def normalize_team_match(entry: dict[str, Any]) -> dict[str, Any]:
    """Convertit une entrée /v2/team?q=matches en format normalisé.

    L'équipe suivie est toujours ``team1`` sur la page d'historique de VLR.gg.

    Args:
        entry: Entrée brute depuis /v2/team?q=matches.

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

    # Le champ "event" de /team?q=matches porte l'abréviation du round ("GF", "LBF", ...),
    # parfois collée au nom de l'événement ("GC 26: ChampionshipUBSF")
    event_name, round_info = split_event_round(entry.get("event", ""))

    # L'API sépare désormais la date ("2026/10/27") de l'heure ("1:00 pm") ;
    # les anciennes versions renvoyaient tout dans "date" et pas de "time".
    date_str = " ".join(p for p in (entry.get("date", ""), entry.get("time", "")) if p)

    return {
        "team1": team1_name,
        "team2": team2_name,
        "score1": score1 if not is_upcoming else "0",
        "score2": score2 if not is_upcoming else "0",
        "match_page": entry.get("url", ""),
        # Affinés par enrich_match_from_details quand les détails sont disponibles
        "tournament_name": event_name,
        "round_info": round_info,
        "time_completed": format_vlr_date(date_str),
        "match_event": event_name,
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


def event_and_series(details: dict[str, Any]) -> tuple[str, str]:
    """Nom du tournoi et série ("Playoffs: Grand Final") depuis /v2/match/details.

    Dans /match/details, ``event`` est un dict {name, series, logo} et ``name``
    contient la série en suffixe : on la retire pour garder le tournoi.
    """
    event_data = details.get("event", {})
    if isinstance(event_data, dict):
        event_name = _clean_vlr_text(event_data.get("name", ""))
        series = _clean_vlr_text(event_data.get("series", ""))
    else:
        event_name = _clean_vlr_text(str(event_data))
        series = ""

    # Ex: name="Challengers 2026: FR Stage 1 Playoffs: Grand Final", series="Playoffs: Grand Final"
    # => tournament = "Challengers 2026: FR Stage 1"
    if event_name and series and series in event_name:
        tournament = event_name[: event_name.index(series)].strip() or event_name
    else:
        tournament = event_name
    return tournament, series


def enrich_match_from_details(
    match: dict[str, Any], details: dict[str, Any], team_id: str | None = None
) -> dict[str, Any]:
    """Enrichit un match normalisé avec les données de /v2/match/details.

    Met à jour le round_info, tournament_name, les scores et les maps à partir
    des données plus fiables de l'endpoint details. L'ordre des équipes peut
    différer entre l'historique (équipe suivie en premier) et les détails
    (ordre de la page du match) : ``team_id`` permet de le savoir sans comparer
    les noms.
    """
    if not details:
        return match

    tournament, series = event_and_series(details)
    if tournament:
        match["tournament_name"] = tournament
        match["match_event"] = tournament

    # Round / série ("Playoffs: Grand Final", etc.)
    if series:
        match["round_info"] = expand_round_name(series)

    teams = details.get("teams", [])
    side = team_side(details, team_id)
    if side is not None:
        swapped = side == 1
    else:
        swapped = len(teams) >= 2 and same_team_name(
            str(teams[1].get("name", "")), str(match.get("team1", ""))
        )

    # Scores depuis teams[] (en respectant l'ordre)
    if len(teams) >= 2 and match.get("status") == "completed":
        first, second = (teams[1], teams[0]) if swapped else (teams[0], teams[1])
        s1, s2 = first.get("score", ""), second.get("score", "")
        if s1 != "" and s2 != "":
            match["score1"] = str(s1)
            match["score2"] = str(s2)

    # Stocker les scores par map pour l'affichage (en respectant l'ordre)
    maps_summary = []
    for m in details.get("maps", []):
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
            raw_s1, raw_s2 = raw_s2, raw_s1
        maps_summary.append({"map": map_name, "score1": raw_s1, "score2": raw_s2})
    if maps_summary:
        match["maps"] = maps_summary

    return match


def _upcoming_from_fixture(
    fixture: dict[str, Any], homepage_entry: dict[str, Any] | None
) -> dict[str, Any]:
    t1 = fixture.get("team1") or {}
    t2 = fixture.get("team2") or {}
    kickoff: datetime | None = None
    precise = False
    event, round_info = "", ""

    if homepage_entry:
        # La page d'accueil donne l'heure en UTC et un nom de série propre
        kickoff = parse_vlrgg_timestamp(homepage_entry.get("unix_timestamp", ""))
        precise = kickoff is not None
        event = _clean_vlr_text(homepage_entry.get("match_event", ""))
        round_info = expand_round_name(_clean_vlr_text(homepage_entry.get("match_series", "")))
    if kickoff is None:
        date_str = " ".join(p for p in (fixture.get("date", ""), fixture.get("time", "")) if p)
        parsed = parse_vlr_date(date_str)
        if parsed is not None:
            kickoff, precise = parsed
    if not event and not round_info:
        event, round_info = split_event_round(fixture.get("event", ""))

    return {
        "team1": t1.get("name", "???") if isinstance(t1, dict) else str(t1),
        "team2": t2.get("name", "???") if isinstance(t2, dict) else str(t2),
        "team1_tag": t1.get("tag", "") if isinstance(t1, dict) else "",
        "team2_tag": t2.get("tag", "") if isinstance(t2, dict) else "",
        "match_id": match_id_of(fixture),
        "match_page": fixture.get("url", ""),
        "kickoff": int(kickoff.timestamp()) if kickoff else None,
        "kickoff_precise": precise,
        "tournament_name": event,
        "match_event": event,
        "round_info": round_info,
        "status": "upcoming",
    }


def _fixture_from_homepage(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_id": match_id_of(entry),
        "url": entry.get("match_page", ""),
        "team1": {"name": entry.get("team1", "")},
        "team2": {"name": entry.get("team2", "")},
    }


def upcoming_from_schedule(
    fixtures: list[dict[str, Any]],
    *,
    exclude_ids: set[str],
    homepage: list[dict[str, Any]],
    vlr_name: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Matchs à venir d'une équipe, triés par heure de début.

    ``fixtures`` vient de /v2/team?q=schedule, qui reprend toutes les cartes
    de match de la page d'équipe : les « Upcoming matches » mais aussi les
    « Recent Results ». On écarte donc les IDs déjà connus comme terminés ou en
    direct (``exclude_ids``) et, au cas où l'historique manquerait, ce qui a
    commencé il y a plus de 3 h. Quand le calendrier est indisponible, on se
    rabat sur la page d'accueil filtrée par nom exact.
    """
    homepage_by_id = {match_id_of(m): m for m in homepage if match_id_of(m)}
    if not fixtures:
        fixtures = [
            _fixture_from_homepage(m)
            for m in homepage
            if same_team_name(vlr_name, str(m.get("team1", "")))
            or same_team_name(vlr_name, str(m.get("team2", "")))
        ]

    cutoff = (now - timedelta(hours=3)).timestamp()
    upcoming = []
    for fixture in fixtures:
        mid = match_id_of(fixture)
        if mid and mid in exclude_ids:
            continue
        entry = _upcoming_from_fixture(fixture, homepage_by_id.get(mid))
        if entry["kickoff"] is not None and entry["kickoff"] < cutoff:
            continue
        upcoming.append(entry)

    upcoming.sort(key=lambda m: (m["kickoff"] is None, m["kickoff"] or 0))
    return upcoming
