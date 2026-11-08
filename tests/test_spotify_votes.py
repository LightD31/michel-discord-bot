"""The Spotify polls' rules (features/spotify/votes.py), and the daily draw that used to spin."""

import importlib
import random
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from features.spotify.votes import (
    DailyTally,
    add_vote_deadline,
    add_vote_passes,
    daily_outcome,
    pick_vote_track,
    tally_add_votes,
    tally_daily_votes,
)

NAMES = {"1": "Jean"}


def test_the_daily_tally_counts_each_choice_and_names_voters():
    tally = tally_daily_votes({"1": "conserver", "2": "supprimer", "3": "menfou"}, NAMES)
    assert tally == DailyTally(conserver=1, supprimer=1, menfou=1, voters=["Jean", "2", "3"])
    assert tally.total == 3


@pytest.mark.parametrize(
    ("votes", "outcome"),
    [
        ({"1": "conserver", "2": "conserver"}, "extend"),  # 2 < MIN_VOTES
        ({"1": "supprimer", "2": "supprimer", "3": "conserver"}, "remove"),
        ({"1": "conserver", "2": "supprimer", "3": "menfou"}, "keep"),  # a tie keeps it
        ({"1": "menfou", "2": "menfou", "3": "menfou"}, "remove"),  # nobody took a side
        ({"1": "menfou", "2": "menfou", "3": "conserver"}, "keep"),
    ],
)
def test_the_daily_outcome(votes, outcome):
    assert daily_outcome(tally_daily_votes(votes, {})) == outcome


def test_the_draw_only_picks_tracks_never_voted_on():
    rng = random.Random(0)
    picks = {pick_vote_track(["a", "b", "c"], ["a", "c"], rng) for _ in range(20)}
    assert picks == {"b"}


def test_the_draw_gives_up_once_every_track_was_voted_on():
    assert pick_vote_track(["a", "b"], ["a", "b", "z"]) is None
    assert pick_vote_track([], []) is None


def test_the_add_poll_tally_mentions_unnamed_voters():
    assert tally_add_votes({"1": "yes", "2": "no", "3": "yes"}, NAMES) == (
        2,
        1,
        ["Jean", "<@2>", "<@3>"],
    )


def test_the_add_poll_needs_a_strict_majority():
    assert add_vote_passes(2, 1) and not add_vote_passes(1, 1)


@pytest.mark.parametrize(
    ("now", "deadline"),
    [
        (datetime(2026, 1, 1, 14, 0, 0), datetime(2026, 1, 2, 14, 0)),
        (datetime(2026, 1, 1, 14, 0, 1), datetime(2026, 1, 2, 15, 0)),
        (datetime(2026, 1, 1, 23, 30), datetime(2026, 1, 3, 0, 0)),
    ],
)
def test_the_add_poll_closes_on_the_first_full_hour_a_day_later(now, deadline):
    assert add_vote_deadline(now) == deadline


@pytest.fixture
def votes_mod(store_config, tmp_path):
    """The extension's votes module, imported against a minimal config."""
    store_config({"config": {"misc": {"dataFolder": str(tmp_path)}}, "servers": {}})
    for name in [m for m in sys.modules if m.startswith("extensions.spotify")]:
        sys.modules.pop(name)
    yield importlib.import_module("extensions.spotify.votes")
    for name in [m for m in sys.modules if m.startswith("extensions.spotify")]:
        sys.modules.pop(name)


async def test_no_poll_opens_once_every_track_was_voted_on(votes_mod):
    """It used to redraw forever: the bot's loop hung on the daily vote."""

    class Repo:
        async def playlist_track_ids(self):
            return ["a", "b"]

        async def voted_track_ids(self):
            return ["a", "b"]

    saved: list[dict] = []

    async def save_voteinfos(server):
        saved.append(dict(server.vote_infos))

    harness = SimpleNamespace(save_voteinfos=save_voteinfos)
    server = SimpleNamespace(
        guild_id="1", repo=Repo(), vote_infos={"message_id": "9", "track_id": "a"}
    )

    await votes_mod.VotesMixin._start_new_vote(harness, server)

    assert saved == [
        {"message_id": None, "track_id": None, "extensions": 0, "reminder_message_id": None}
    ]


def test_the_extension_no_longer_tallies_on_its_own(votes_mod):
    assert not hasattr(votes_mod, "count_votes")


@pytest.mark.parametrize(("outcome", "state"), [("remove", "supprimée"), ("keep", "conservée")])
async def test_closing_the_daily_vote(votes_mod, monkeypatch, outcome, state):
    calls: list = []

    async def fake_embed_song(**kwargs):
        return kwargs["embedtype"], None

    async def fake_vote_embed(**kwargs):
        return kwargs["color"]

    async def fake_edit(message, **kwargs):
        calls.append(("edit", kwargs["content"]))

    async def remove_from_spotify(playlist_id, ids):
        calls.append(("spotify-remove", ids))

    monkeypatch.setattr(votes_mod, "embed_song", fake_embed_song)
    monkeypatch.setattr(votes_mod, "embed_message_vote", fake_vote_embed)
    monkeypatch.setattr(votes_mod, "edit_message_if_changed", fake_edit)
    monkeypatch.setattr(
        votes_mod,
        "spotify_api",
        SimpleNamespace(playlist_remove_all_occurrences_of_items=remove_from_spotify),
    )

    class Repo:
        async def delete_playlist_item(self, track_id):
            calls.append(("delete", track_id))

        async def set_vote_state(self, track_id, value):
            calls.append(("state", value))

    async def check_playlist(server):
        calls.append(("check",))

    harness = SimpleNamespace(_check_playlist_changes_for_server=check_playlist)
    server = SimpleNamespace(guild_id="1", playlist_id="p", repo=Repo())
    tally = tally_daily_votes({"1": "supprimer"}, {})

    await votes_mod.VotesMixin._close_daily_vote(
        harness, server, object(), "t", {}, {}, tally, outcome
    )

    assert ("state", state) in calls
    removed = [c for c in calls if c[0] in ("spotify-remove", "delete", "check")]
    assert bool(removed) == (outcome == "remove")
