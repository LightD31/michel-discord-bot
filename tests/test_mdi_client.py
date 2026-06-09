"""Unit tests for ``features.mdi.client``.

Payload shapes mirror https://raider.io/api/events/mdi-midnight-season-1/brackets
(and ``…/brackets/<slug>``) as observed live in June 2026.
"""

from features.mdi.client import GameSnapshot, MatchSnapshot, TeamRef, _build_match


def _team_payload(team_id: int, slug: str, name: str, seed: int) -> dict:
    return {
        "id": team_id,
        "seed": seed,
        "name": name,
        "slug": slug,
        "region": {"name": "Europe", "slug": "eu", "short_name": "EU"},
        "icon_logo_url": "https://example.com/logo.png",
        "teamEventProfileUrl": "/event-teams/mdi/" + slug,
    }


def _match_payload(team_id: int, winner: bool) -> dict:
    return {
        "id": 10783,
        "round": 1,
        "match": 1,
        "position": "upper",
        "status": "complete" if winner else "unstarted",
        "winnerTeamId": team_id if winner else None,
        "gracePeriodEndsAt": "2026-06-07T15:47:26Z",
        "firstTeam": _team_payload(team_id, "mandatory", "Mandatory", 5),
        "secondTeam": _team_payload(8000, "interrupt", "Interrupt!", 4),
        "games": [
            {
                "id": 49726,
                "gameOrder": 1,
                "status": "complete" if winner else "unstarted",
                "winnerTeamId": team_id if winner else None,
                "details": {
                    "mythicLevel": 15,
                    "firstTeamDeaths": 0,
                    "secondTeamDeaths": 7,
                    "firstTeamSplit1": "5:16",
                    "secondTeamSplit1": "6:09",
                    "firstTeamSplit2": "4:43",
                    "secondTeamSplit2": None,
                },
                "dungeon": {
                    "name": "Pit of Saron",
                    "short_name": "POS",
                    "keystone_timer_ms": 1800999,
                },
                "videoId": None,
                "videoType": None,
            }
        ],
    }


def test_build_match_parses_live_payload_shape() -> None:
    match = _build_match(_match_payload(7798, winner=True), "season-finals", "Season Finals")
    assert isinstance(match, MatchSnapshot)
    assert match.id == 10783
    assert match.status == "complete"
    assert match.winner_team_id == 7798
    # Matches no longer carry startsAt/scheduledAt — must degrade to None.
    assert match.starts_at is None
    assert match.first_team is not None and match.first_team.name == "Mandatory"
    assert match.second_team is not None and match.second_team.slug == "interrupt"

    game = match.games[0]
    assert isinstance(game, GameSnapshot)
    assert game.mythic_level == 15
    assert game.dungeon_short_name == "POS"
    assert game.first_team_splits == (316, 283)
    # Second team's split2 is null: collection stops at the first missing split.
    assert game.second_team_splits == (369,)
    assert game.keystone_timer_seconds == 1800


def test_team_by_slug_returns_per_bracket_ref() -> None:
    # Raider.IO registers the same roster under a different team id per bracket
    # (Mandatory was 7681 in group-a and 7798 in season-finals). Winner checks
    # must use the id carried by the match itself.
    group_a = _build_match(_match_payload(7681, winner=True), "group-a", "Group A")
    finals = _build_match(_match_payload(7798, winner=True), "season-finals", "Season Finals")
    assert group_a is not None and finals is not None

    ref_a = group_a.team_by_slug("mandatory")
    ref_f = finals.team_by_slug("MANDATORY")  # lookup is case-insensitive
    assert isinstance(ref_a, TeamRef) and ref_a.id == 7681
    assert isinstance(ref_f, TeamRef) and ref_f.id == 7798

    # The stale cross-bracket id misclassifies the win; the per-match ref doesn't.
    assert finals.winner_team_id != ref_a.id
    assert finals.winner_team_id == ref_f.id
    assert finals.games_won_by(ref_f.id) == 1
    assert finals.opponent_of(ref_f.id) is not None
    assert finals.team_by_slug("unknown-team") is None
