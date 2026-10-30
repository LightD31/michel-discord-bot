"""Rattachement des matchs VLR.gg à une équipe suivie, par identifiant.

Les listes globales de l'API (``/v2/match?q=live_score``, ``q=upcoming``) ne
portent que des noms d'équipe. Les comparer par sous-chaîne confondait des
équipes voisines (« G2 » attrapait aussi G2 Gozen, « Gentle Mates » attrapait
Gentle Mates GC). On s'appuie donc sur les identifiants : les ``match_id`` du
calendrier de l'équipe, et les ``teams[].id`` de ``/v2/match/details``.
"""

import re
from collections.abc import Iterable
from typing import Any


def extract_match_id_from_url(url: str) -> str | None:
    """Extrait l'ID d'un match depuis une URL VLR.gg.

    Ex: "https://www.vlr.gg/412345/team1-vs-team2" -> "412345"
    """
    if not url:
        return None
    m = re.search(r"vlr\.gg/(\d+)", url)
    return m.group(1) if m else None


def match_id_of(entry: dict[str, Any]) -> str:
    """ID VLR.gg d'une entrée de l'API (champ ``match_id``, sinon depuis l'URL)."""
    mid = entry.get("match_id")
    if mid:
        return str(mid)
    return extract_match_id_from_url(entry.get("match_page") or entry.get("url") or "") or ""


def same_team_name(a: str, b: str) -> bool:
    """Égalité de noms d'équipe, insensible à la casse et aux espaces — pas d'inclusion."""
    return bool(a) and a.strip().casefold() == b.strip().casefold()


def team_side(details: dict[str, Any], team_id: str | None) -> int | None:
    """Côté (0 ou 1) de l'équipe ``team_id`` dans ``details["teams"]``, sinon None."""
    if not team_id:
        return None
    for idx, team in enumerate((details.get("teams") or [])[:2]):
        if str(team.get("id") or "") == str(team_id):
            return idx
    return None


def is_final(details: dict[str, Any]) -> bool:
    """Vrai quand la page du match affiche le résultat définitif."""
    return str(details.get("status") or "").strip().lower() == "final"


def live_candidates(
    live_segments: Iterable[dict[str, Any]], schedule_ids: set[str], vlr_name: str
) -> list[dict[str, Any]]:
    """Matchs en direct qui concernent probablement l'équipe.

    Un match est retenu si son ID figure au calendrier de l'équipe, ou si l'un
    des deux noms est *exactement* le nom officiel VLR de l'équipe. L'appelant
    confirme ensuite chaque candidat avec :func:`team_side` sur ses détails.
    """
    found = []
    for seg in live_segments:
        mid = match_id_of(seg)
        names = (str(seg.get("team1") or ""), str(seg.get("team2") or ""))
        if (mid and mid in schedule_ids) or any(same_team_name(vlr_name, n) for n in names):
            found.append(seg)
    return found


def due_for_reminder(
    upcoming: Iterable[dict[str, Any]], *, now: float, window_seconds: int
) -> list[dict[str, Any]]:
    """Matchs à venir dont le coup d'envoi tombe dans ``(now, now + window]``.

    Seuls les matchs dont l'heure est connue (``kickoff_precise``) comptent :
    un rappel « dans 15 min » sur une date sans heure serait faux.
    """
    if window_seconds <= 0:
        return []
    due = []
    for match in upcoming:
        kickoff = match.get("kickoff")
        if not match.get("kickoff_precise") or not isinstance(kickoff, int | float):
            continue
        if now < kickoff <= now + window_seconds:
            due.append(match)
    return due
