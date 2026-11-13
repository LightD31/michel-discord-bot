"""Every ``Task.start()`` is guarded by ``if not <task>.running`` (B15).

``on_startup`` / ``on_ready`` run again after a dashboard reload, and ``Ready``
fires again on every gateway reconnect: an unguarded ``start()`` runs a second
loop of the same task (interactions.py doesn't refuse it).
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _unguarded_starts(tree: ast.AST) -> list[tuple[int, str]]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    found = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "start"
            and not node.args
            and not node.keywords
        ):
            continue
        target = ast.unparse(node.func.value)
        guard = f"not {target}.running"
        ancestor = parents.get(node)
        guarded = False
        while ancestor is not None:
            if isinstance(ancestor, ast.If) and guard in ast.unparse(ancestor.test):
                guarded = True
                break
            ancestor = parents.get(ancestor)
        if not guarded:
            found.append((node.lineno, target))
    return found


def test_task_starts_are_guarded():
    problems = []
    for path in sorted((ROOT / "extensions").rglob("*.py")):
        top = path.relative_to(ROOT / "extensions").parts[0]
        if top.startswith("_") and top != "__init__.py":  # parked work, not loaded
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, target in _unguarded_starts(tree):
            problems.append(f"{path.relative_to(ROOT)}:{line} {target}.start()")
    assert not problems, "Task.start() without `if not <task>.running`:\n" + "\n".join(problems)


def test_the_scan_sees_a_guard():
    good = ast.parse("if not self.loop.running:\n    self.loop.start()\n")
    bad = ast.parse("self.loop.start()\n")
    assert _unguarded_starts(good) == []
    assert _unguarded_starts(bad) == [(1, "self.loop")]
