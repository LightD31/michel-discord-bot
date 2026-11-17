"""A caught failure is logged with its traceback unless it is an outage (T05).

``log_failure`` decides per exception; the scan below keeps new code from
logging an unexpected error with ``logger.error`` inside an ``except Exception``,
which would drop the traceback and hide the bug from error reporting.
"""

import ast
import logging
from types import SimpleNamespace

import pytest
from interactions.client.errors import HTTPException
from source_scan import BOT_SOURCES, keyword, python_files, rel

from src.core import errors as core_errors
from src.core.errors import DatabaseError, HttpError, is_expected_failure
from src.core.logging import log_failure
from src.discord_ext import errors as discord_errors

BROAD = {"Exception", "BaseException"}


def discord_error(status):
    return HTTPException(SimpleNamespace(status=status, reason="x"), response_data={})


@pytest.fixture
def records(caplog):
    caplog.set_level(logging.ERROR, logger="test.failures")
    return caplog


@pytest.fixture(autouse=True)
def _discord_classifier(monkeypatch):
    # As main.py does, on a copy of the registry: a check added for good would
    # make every later test see Discord's 403/404 as expected (T03).
    monkeypatch.setattr(core_errors, "_expected_checks", list(core_errors._expected_checks))
    discord_errors.install()


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
    return handler.type is None or any(
        isinstance(n, ast.Name | ast.Attribute)
        and (n.id if isinstance(n, ast.Name) else n.attr) in BROAD
        for n in names
    )


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


def keeps_traceback(call: ast.Call) -> bool:
    """``exc_info=`` anything but a literal ``False`` / ``None`` / ``0``."""
    passed = keyword(call, "exc_info")
    return passed is not None and not (
        isinstance(passed.value, ast.Constant) and not passed.value.value
    )


def bare_error_lines(source: str) -> list[int]:
    """``<anything>.error(...)`` / ``.critical(...)`` in a broad except, without a traceback.

    Any receiver counts (``self._log``, ``LOG``, ``logging``): nothing in the bot
    has an ``error`` method that isn't a logger's.
    """
    lines = []
    for handler in ast.walk(ast.parse(source)):
        if not (isinstance(handler, ast.ExceptHandler) and broad(handler)):
            continue
        for call in calls_directly_in(handler):
            func = call.func
            if not (isinstance(func, ast.Attribute) and func.attr in ("error", "critical")):
                continue
            if not keeps_traceback(call):
                lines.append(call.lineno)
    return sorted(lines)


def test_no_bare_error_line_in_a_broad_except():
    offenders = [
        f"{rel(file)}:{line}"
        for file in python_files(*BOT_SOURCES)
        for line in bare_error_lines(file.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        "logger.error in an `except Exception` drops the traceback: use "
        "log_failure(logger, ...) (src/core/logging.py) or logger.exception(...)"
    )


@pytest.mark.parametrize(
    "call",
    [
        "logger.error('x')",
        "self._l.error('x')",  # the receiver needn't say "log"
        "logger.critical('x')",
        "logger.error('x', exc_info=False)",
        "logger.error('x', exc_info=None)",
    ],
)
def test_the_scan_catches(call):
    for clause in ("except Exception:", "except (ValueError, Exception):", "except:"):
        assert bare_error_lines(f"try:\n    pass\n{clause}\n    {call}\n") == [4]


@pytest.mark.parametrize(
    "call", ["logger.exception('x')", "logger.error('x', exc_info=True)", "logger.warning('x')"]
)
def test_the_scan_lets_a_traceback_or_a_lower_level_through(call):
    assert bare_error_lines(f"try:\n    pass\nexcept Exception as e:\n    {call}\n") == []


def test_a_narrow_except_may_log_one_line():
    assert bare_error_lines("try:\n    pass\nexcept KeyError:\n    logger.error('x')\n") == []
