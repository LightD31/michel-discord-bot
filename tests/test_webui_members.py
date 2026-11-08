"""The dashboard's member search: one page at a time, never the whole guild.

The route is mounted on a bare FastAPI app with a stub context, like
tests/test_webui_bot_messages.py; ``all_members`` itself is checked against
MongoDB in tests/test_mongo_userinfo.py.
"""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import src.webui.members as members_mod
import src.webui.routes.members as routes
from src.core.errors import DatabaseError
from src.webui.members import Member, MemberDirectory, fold, load_members, pick, search

ALICE = Member("1", "alice", "Élodie")
BOB = Member("2", "bob_42", "Bob")
CHARLIE = Member("3", "charlie", "Charles-Élie")
ROSTER = [BOB, CHARLIE, ALICE]


def test_folding_ignores_case_and_accents():
    assert fold("Élodie") == "elodie"
    assert fold("ÇA") == "ca"


def test_an_empty_query_pages_through_everyone():
    page, total = search(ROSTER, "  ", limit=2, offset=1)
    assert (page, total) == ([CHARLIE, ALICE], 3)


def test_the_query_matches_names_usernames_and_ids_without_accents():
    assert search(ROSTER, "elo")[0] == [ALICE]
    assert search(ROSTER, "BOB_")[0] == [BOB]
    assert search(ROSTER, "3")[0] == [CHARLIE]


def test_names_starting_with_the_query_come_first():
    page, total = search(ROSTER, "el")
    assert page == [ALICE, CHARLIE] and total == 2  # Élodie starts with it, Charles-Élie doesn't


def test_the_guild_first_names_are_searched_too():
    assert search(ROSTER, "jean", names={"2": "Jean"})[0] == [BOB]
    assert search(ROSTER, "jean", names={"2": 7})[0] == []


def test_the_total_counts_every_match_beyond_the_page():
    many = [Member(str(n), f"user{n}", f"User {n}") for n in range(120)]
    page, total = search(many, "user", limit=50)
    assert len(page) == 50 and total == 120


def test_picking_keeps_the_order_asked_and_skips_unknown_ids():
    assert pick(ROSTER, ["3", "9", "1", "3"]) == [CHARLIE, ALICE]


async def test_the_directory_keeps_a_guild_list_for_its_ttl():
    loads: list[str] = []
    now = [0.0]

    async def load(guild_id):
        loads.append(guild_id)
        return [ALICE]

    directory = MemberDirectory(load, ttl=60, clock=lambda: now[0])
    assert await directory.members("1") == [ALICE]
    now[0] = 59
    await directory.members("1")
    await directory.members("2")
    assert loads == ["1", "2"]

    now[0] = 61
    await directory.members("1")
    assert loads == ["1", "2", "1"]


# ── Loading on the bot loop ──────────────────────────────────────────────


class _Repo:
    rows: list[dict] = []
    failing = False

    def __init__(self, guild_id):
        self.guild_id = guild_id

    async def all_members(self):
        if _Repo.failing:
            raise DatabaseError("mongo down")
        return list(_Repo.rows)


class _Bot:
    def __init__(self, members) -> None:
        self.members = members

    async def fetch_guild(self, guild_id):
        if self.members is None:
            raise LookupError("Unknown Guild")
        return SimpleNamespace(members=self.members)


@pytest.fixture
def repo(monkeypatch):
    _Repo.rows = [{"id": "1", "username": "alice", "display_name": "Élodie"}]
    _Repo.failing = False
    monkeypatch.setattr(members_mod, "UserInfoRepository", _Repo)
    return _Repo


async def test_the_stored_members_are_completed_by_the_gateway_cache(repo):
    cached = [
        SimpleNamespace(id=1, username="alice", display_name="ignored", bot=False),
        SimpleNamespace(id=2, username="bob_42", display_name="Bob", bot=False),
        SimpleNamespace(id=3, username="robot", display_name="Robot", bot=True),
    ]

    members = await load_members(_Bot(cached), "1")

    assert members == [Member("2", "bob_42", "Bob"), ALICE]  # sorted, bots left out


async def test_either_source_can_fail_alone(repo):
    repo.failing = True
    assert await load_members(_Bot([SimpleNamespace(id=2, username="bob_42")]), "1") == [
        Member("2", "bob_42", "bob_42")
    ]
    repo.failing = False
    assert await load_members(_Bot(None), "1") == [ALICE]


# ── Route ────────────────────────────────────────────────────────────────


@dataclass
class _Session:
    username: str = "admin"
    user_id: str = "42"


class _Ctx:
    """Guild admin of guild 1 only; the bot calls run inline."""

    bot = object()

    def __init__(self) -> None:
        self.loads = 0

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002 — parity
        if str(guild_id) != "1":
            raise HTTPException(status_code=403, detail="Accès réservé")
        return _Session()

    def require_bot_loop(self):
        return None

    async def call_bot(self, func, *args, what, **kwargs):
        self.loads += 1
        return [Member(str(n), f"user{n}", f"Membre {n:03d}") for n in range(120)]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(routes, "load_discord2name", lambda gid: {"7": "Jean"})
    ctx = _Ctx()
    app = FastAPI()
    app.include_router(routes.create_router(ctx))  # type: ignore[arg-type]
    return TestClient(app), ctx


def test_the_route_answers_one_page_and_the_total(client):
    http, ctx = client

    data = http.get("/api/servers/1/members", params={"limit": 10, "offset": 20}).json()

    assert [m["id"] for m in data["members"]] == [str(n) for n in range(20, 30)]
    assert (data["total"], data["offset"], data["limit"]) == (120, 20, 10)
    http.get("/api/servers/1/members", params={"q": "membre 11"})
    assert ctx.loads == 1  # the second request was served from the directory


def test_the_route_searches_the_first_names_too(client):
    http, _ = client

    data = http.get("/api/servers/1/members", params={"q": "jean"}).json()

    assert [m["id"] for m in data["members"]] == ["7"] and data["total"] == 1


def test_the_route_resolves_ids_and_ignores_garbage(client):
    http, _ = client

    data = http.get("/api/servers/1/members", params={"ids": "5,abc,,999,3"}).json()

    assert [m["id"] for m in data["members"]] == ["5", "3"]
    assert "total" not in data


def test_the_route_caps_the_page_size(client):
    http, _ = client
    assert http.get("/api/servers/1/members", params={"limit": 101}).status_code == 422
    assert http.get("/api/servers/1/members", params={"offset": -1}).status_code == 422


def test_another_guild_is_refused(client):
    http, ctx = client
    assert http.get("/api/servers/2/members").status_code == 403
    assert ctx.loads == 0
