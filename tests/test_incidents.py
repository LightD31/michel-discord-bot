"""The server's incidents (P21): what is kept, for whom, and what never is."""

import ast
import asyncio
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from source_scan import parse, python_files, rel
from webui_fakes import FakeWebUI

from src.core import incidents, log_context
from src.core.errors import DatabaseError
from src.core.incidents import PER_GUILD, Incident, IncidentHandler, reason_of
from src.core.logging import log_failure
from src.webui.routes import incidents as routes


class Forbidden(Exception):  # named like Discord's
    pass


@pytest.fixture
def handler():
    found = IncidentHandler()
    logger = logging.getLogger("test.incidents")
    logger.addHandler(found)
    logger.propagate = False
    yield found, logger
    logger.removeHandler(found)


def test_reasons_are_short_french_and_never_the_error_text():
    assert reason_of(DatabaseError("mongo:27017 refused")) == "La base de données ne répondait pas."
    assert (
        reason_of(Forbidden("Missing Access"))
        == "Discord a refusé : il manque une permission à Michel."
    )
    assert reason_of(TimeoutError()) == "Un service a mis trop de temps à répondre."
    assert "KeyError" not in reason_of(KeyError("secret"))
    assert reason_of(None) == "Échec signalé, sans plus de détail."


def test_a_failed_interaction_is_kept_for_its_guild_only(handler):
    found, logger = handler
    with log_context.scope(guild_id="1", command="/agenda", correlation_id="ab12cd"):
        try:
            raise KeyError("token=hunter2")
        except KeyError:
            logger.exception("boom with the member 123456789012345678 and token=hunter2")
    logger.error("no guild in context: not an incident")
    (incident,) = found.of("1")
    assert incident.reference == "ab12cd" and incident.where == "/agenda"
    assert incident.reason.startswith("Erreur inattendue")
    assert "hunter2" not in json.dumps(incident.to_dict())
    assert "123456789012345678" not in json.dumps(incident.to_dict())
    assert found.of("2") == []


def test_one_failure_logged_twice_is_one_incident_with_its_reason(handler):
    found, logger = handler
    with log_context.scope(guild_id="1", command="/succes", correlation_id="cd34ef"):
        logger.error("first line, no exception")
        try:
            raise DatabaseError("down")
        except DatabaseError:
            log_failure(logger, "second line: %s", "the expected failure")
    (incident,) = found.of("1")
    assert incident.reason == "La base de données ne répondait pas."


def test_warnings_are_not_incidents_and_the_buffer_is_bounded(handler):
    found, logger = handler
    with log_context.scope(guild_id="1", task="AgendaExtension.refresh_all"):
        logger.warning("just a warning")
        for i in range(PER_GUILD + 5):
            with log_context.scope(correlation_id=f"r{i:04}"):
                logger.error("failed")
    kept = found.of("1")
    assert len(kept) == PER_GUILD and kept[0].reference == f"r{PER_GUILD + 4:04}"  # newest first


def test_few_guilds_are_kept_at_most(monkeypatch):
    monkeypatch.setattr(incidents, "MAX_GUILDS", 2)
    found = IncidentHandler()
    for gid in ("1", "2", "3"):
        found.add(gid, Incident(0.0, "x", "/a", "r", True))
    assert found.of("1") == [] and len(found.of("3")) == 1


def test_buttons_and_tasks_are_named_without_their_ids():
    assert routes.where_label("/agenda") == "/agenda"
    assert routes.where_label("component:giveaway:join:123456789012345678") == "Bouton (giveaway)"
    assert routes.where_label("modal:123456789012345678") == "Formulaire"
    assert routes.where_label("AgendaExtension.refresh_all") == "Message de l'agenda"
    assert routes.where_label("Unknown.task") == "Tâche de fond"


def fake_ctx(admin_of=("1",)) -> FakeWebUI:
    return FakeWebUI(session=SimpleNamespace(user_id="42"), manages=admin_of)


def endpoint(ctx):
    router = routes.create_router(ctx)
    return next(r.endpoint for r in router.routes if r.path.endswith("/incidents"))


def test_the_route_is_for_the_guilds_admins_and_finds_a_reference(monkeypatch):
    kept = IncidentHandler()
    kept.add("1", Incident(10.0, "ab12cd", "/agenda", "Discord a renvoyé une erreur.", True))
    kept.add(
        "1", Incident(20.0, "ef56ab", "component:rolemenu:1", "Un service externe a échoué.", True)
    )
    monkeypatch.setattr(incidents, "_handler", kept)
    out = json.loads(asyncio.run(endpoint(fake_ctx())(None, "1", ref="AB12")).body)
    assert out == {
        "incidents": [
            {
                "at": 10.0,
                "reference": "ab12cd",
                "where": "/agenda",
                "reason": "Discord a renvoyé une erreur.",
            }
        ]
    }
    assert (
        len(json.loads(asyncio.run(endpoint(fake_ctx())(None, "1", ref="")).body)["incidents"]) == 2
    )
    with pytest.raises(HTTPException):
        asyncio.run(endpoint(fake_ctx(admin_of=()))(None, "1", ref=""))


# ── Every task looping over guilds says which guild it is on (P24) ────────

GUILD_LOOP = re.compile(
    r"([A-Z_]+\.(items|guild_ids)\(\)|\bguilds\b|_guilds\(\)|SERVERS\.values\(\)|_servers\.items\(\))"
)
# Loops whose items aren't one guild's: a streamer or a vote watched by several
# guilds, guilds being purged after the bot left, paginators being dropped.
NOT_PER_GUILD = {
    "extensions/datapurge.py",
    "extensions/twitch/emotes.py",
    "extensions/twitch/schedule.py",
    "extensions/compareai/voting.py",
}


def _task_loops(path: Path):
    tree = parse(path)[0]
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        if not any(ast.unparse(d).startswith("Task.create") for d in fn.decorator_list):
            continue
        for loop in ast.walk(fn):
            if isinstance(loop, ast.For | ast.AsyncFor) and GUILD_LOOP.search(
                ast.unparse(loop.iter)
            ):
                yield loop


def _binds_guild(loop: ast.For | ast.AsyncFor) -> bool:
    """A statement of the loop's own body is ``with log_context.scope(guild_id=…)``."""
    return any(
        isinstance(statement, ast.With | ast.AsyncWith)
        and any(
            "log_context.scope(guild_id=" in ast.unparse(item.context_expr)
            for item in statement.items
        )
        for statement in loop.body
    )


def test_a_task_looping_over_guilds_binds_each_guild():
    offenders = []
    for path in python_files("extensions"):
        where = rel(path)
        if where in NOT_PER_GUILD:
            continue
        for loop in _task_loops(path):
            if not loop.body or not _binds_guild(loop):
                # The paginators of guilds switched off: nothing to report there.
                if "not in enabled" in ast.unparse(loop.iter):
                    continue
                offenders.append(f"{where}:{loop.lineno}")
    assert offenders == [], (
        "wrap each guild's turn in `with log_context.scope(guild_id=…):` so its failures "
        "show in that server's incidents (src/core/incidents.py)"
    )
