"""A caught failure is logged with its traceback unless it is an outage (T05).

``log_failure`` decides per exception; the scan below keeps new code from
logging an unexpected error with ``logger.error`` inside an ``except Exception``,
which would drop the traceback and hide the bug from error reporting.
"""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from interactions.client.errors import HTTPException

from src.core.errors import DatabaseError, HttpError, is_expected_failure
from src.core.logging import log_failure
from src.discord_ext import errors as discord_errors

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ["extensions", "features", "src", "main.py"]
BROAD = {"Exception", "BaseException"}


def discord_error(status):
    return HTTPException(SimpleNamespace(status=status, reason="x"), response_data={})


@pytest.fixture
def records(caplog):
    caplog.set_level(logging.ERROR, logger="test.failures")
    return caplog


@pytest.fixture(autouse=True)
def _discord_classifier():
    discord_errors.install()  # as main.py does


def failing(error):
    try:
        raise error
    except Exception:
        log_failure(logging.getLogger("test.failures"), "Could not do it: %s", "detail")


def test_a_bug_is_logged_with_its_traceback(records):
    failing(ValueError("bad value"))
    (record,) = records.records
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None and record.exc_info[0] is ValueError
    assert record.getMessage() == "Could not do it: detail"


@pytest.mark.parametrize(
    "error",
    [
        DatabaseError("down"),
        HttpError("503", url="https://api.example", status=503),
        TimeoutError(),
        ConnectionResetError(),
    ],
)
def test_an_outage_is_one_line(records, error):
    failing(error)
    (record,) = records.records
    assert record.exc_info is None


@pytest.mark.parametrize(
    ("status", "expected"), [(403, True), (404, True), (503, True), (400, False)]
)
def test_discord_refusals_and_outages_are_expected_but_not_a_bad_request(status, expected):
    assert is_expected_failure(discord_error(status)) is expected


def test_an_exception_can_be_passed_outside_an_except(records):
    log_failure(logging.getLogger("test.failures"), "gathered", error=KeyError("k"))
    (record,) = records.records
    assert record.exc_info[0] is KeyError


def test_the_record_points_at_the_caller(records):
    failing(ValueError())
    (record,) = records.records
    assert record.funcName == "failing"


def broad(handler):
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return handler.type is None or any(isinstance(n, ast.Name) and n.id in BROAD for n in names)


def calls_directly_in(handler):
    stack = list(ast.iter_child_nodes(handler))
    while stack:
        node = stack.pop()
        if isinstance(
            node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ExceptHandler
        ):
            continue
        if isinstance(node, ast.Call):
            yield node
        stack.extend(ast.iter_child_nodes(node))


def test_no_bare_error_line_in_a_broad_except():
    offenders = []
    for name in SOURCES:
        path = ROOT / name
        for file in [path] if path.is_file() else sorted(path.rglob("*.py")):
            source = file.read_text(encoding="utf-8")
            for handler in ast.walk(ast.parse(source)):
                if not (isinstance(handler, ast.ExceptHandler) and broad(handler)):
                    continue
                for call in calls_directly_in(handler):
                    func = call.func
                    if not (isinstance(func, ast.Attribute) and func.attr == "error"):
                        continue
                    if "log" not in (ast.get_source_segment(source, func.value) or "").lower():
                        continue
                    if any(keyword.arg == "exc_info" for keyword in call.keywords):
                        continue
                    offenders.append(f"{file.relative_to(ROOT)}:{call.lineno}")
    assert offenders == [], (
        "logger.error in an `except Exception` drops the traceback: use "
        "log_failure(logger, ...) (src/core/logging.py) or logger.exception(...)"
    )
