"""Michel says *tu* (P6, B8, ``docs/VOIX.md``): no *vous* of politeness in what it writes.

The scan reads every string literal of the bot (f-strings included), skipping
docstrings and log lines, and fails on a *vous* pronoun or a *vous*
imperative. A message to a whole channel still says *tu*: it speaks to the
one reading it.
"""

import ast
import re

import pytest
from source_scan import BOT_SOURCES, python_files, rel

POLITE = re.compile(
    r"\b(vous|votre|vos|veuillez|utilisez|cliquez|essayez|réessayez|vérifiez|choisissez|"
    r"indiquez|tapez|renseignez|sélectionnez|entrez|saisissez|patientez|contactez|activez|"
    r"désactivez|configurez|ajoutez|pensez|affinez|associez|créez|consultez|retrouvez|"
    r"regardez|votez|mettez|laissez|limitez|restez|tirez|attendez|recommencez)\b",
    re.IGNORECASE,
)
# Phrases that aren't Michel talking to someone: the bot's joke statuses.
NOT_ADDRESSED = {"Vous êtes moches", "Mangez des pommes"}
LOGGERS = {"debug", "info", "warning", "error", "exception", "critical", "log_failure"}


def _docstrings(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            value = body[0].value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found.add(id(value))
    return found


def _logged(tree: ast.AST) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else None
            name = name or (node.func.id if isinstance(node.func, ast.Name) else None)
            if name in LOGGERS:
                found.update(id(n) for arg in node.args for n in ast.walk(arg))
    return found


def polite_lines(source: str) -> list[int]:
    tree = ast.parse(source)
    skipped = _docstrings(tree) | _logged(tree)
    lines = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in skipped
            and node.value not in NOT_ADDRESSED
            and POLITE.search(node.value)
        ):
            lines.append(node.lineno)
    return sorted(set(lines))


def test_michel_says_tu():
    offenders = [
        f"{rel(file)}:{line}"
        for file in python_files(*BOT_SOURCES)
        for line in polite_lines(file.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "Michel tutoie (docs/VOIX.md)"


@pytest.mark.parametrize(
    "source",
    [
        'send("Vous ne pouvez pas faire ça.")',
        'x = f"Votre demande {n} est partie"',
        'send_error(ctx, "Réessayez plus tard.")',
        'description="Cliquez sur un bouton pour voter"',
    ],
)
def test_the_scan_catches(source):
    assert polite_lines(source) != []


@pytest.mark.parametrize(
    "source",
    [
        'def f():\n    """Vous lisez une docstring."""',
        'logger.warning("Vous: %s", e)',
        'send("Tu ne peux pas faire ça. Passe chez le staff, assez vite.")',
    ],
)
def test_the_scan_lets_through(source):
    assert polite_lines(source) == []
