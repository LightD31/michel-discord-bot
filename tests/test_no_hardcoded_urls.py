"""No URL in the code: links, icons and third-party endpoints come from config.

docs/conventions/config.md, *No URLs in the code*: a user-facing link or an API the bot calls is
a ``ui(..., "url")`` field read at use time, never a literal, so changing a
host never takes a release. What stays in the code (T02):

- canonical URLs *derived* from data: a Discord message jump link, a Twitch
  channel, a YouTube video, a Spotify track, a VLR.gg team page;
- Discord's own OAuth endpoints (the dashboard login), the XML namespace an
  Atom feed is parsed with, and the Chart.js build the CSP pins by exact URL;
- examples in the help text of a schema field (``ui(...)``, ``secret_field(...)``);
- a bare scheme (``"https://"``) or a host built at run time (``http://%s``).
"""

import ast
import re
from urllib.parse import urlsplit

import pytest
from source_scan import Imports, parse, python_files, rel

DERIVED_HOSTS = {
    "discord.com",  # message jump links, the OAuth endpoints
    "twitch.tv",
    "www.twitch.tv",
    "youtu.be",
    "www.youtube.com",
    "open.spotify.com",
    "www.vlr.gg",
    "www.w3.org",  # an XML namespace, never fetched
    "cdn.jsdelivr.net",  # Chart.js, pinned with its SRI digest (security_headers.py)
}
HELP_TEXT_CALLS = {"ui", "secret_field", "Field", "enabled_field"}
URL = re.compile(r"""https?://([^\s/)\]'"`<>]*)""", re.IGNORECASE)


def _help_text_constants(tree: ast.AST, imports: Imports) -> set[int]:
    """The ids of every constant passed to a schema field helper."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (imports.resolve(node.func) or "").rpartition(".")[2]
            if name in HELP_TEXT_CALLS:
                found |= {id(n) for n in ast.walk(node) if isinstance(n, ast.Constant)}
    return found


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
        ):
            ids.add(id(node.body[0].value))
    return ids


def hardcoded_urls(tree: ast.AST, imports: Imports) -> list[tuple[int, str]]:
    skipped = _docstrings(tree) | _help_text_constants(tree, imports)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in skipped:
            continue
        for match in URL.finditer(node.value):
            host = match.group(1)
            if not re.search(r"[a-z0-9]", host, re.IGNORECASE) or "%" in host or "{" in host:
                continue  # a bare scheme ("https://…" too), or a host filled in at run time
            if urlsplit(match.group(0)).hostname in DERIVED_HOSTS:
                continue
            found.append((node.lineno, match.group(0)))
    return found


def test_no_url_is_written_in_the_code():
    offenders = []
    for path in python_files("main.py", "src", "features", "extensions"):
        tree, imports, _ = parse(path)
        offenders += [f"{rel(path)}:{line}: {url}" for line, url in hardcoded_urls(tree, imports)]
    assert not offenders, (
        "declare the URL as a ui(..., 'url') field and read it at use time "
        "(docs/conventions/config.md, No URLs in the code):\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "source",
    [
        "API = 'https://api.example.com/v1'",
        "ICON = f'https://cdn.example.com/{name}.png'",
        "def f(url='https://example.com'): ...",
    ],
)
def test_the_scan_catches(source):
    tree = ast.parse(source)
    assert hardcoded_urls(tree, Imports(tree))


@pytest.mark.parametrize(
    "source",
    [
        "LINK = f'https://discord.com/channels/{g}/{c}/{m}'",
        "STREAM = f'https://twitch.tv/{login}'",
        "url: str | None = ui('API', 'url', description='ex. https://api.example.com')",
        "PREFIXES = ('http://', 'https://')",
        "placeholder = 'https://… (un document, un fil Discord…)'",
        "logger.info('listening on http://%s:%s/metrics', host, port)",
        "def f():\n    '''See https://example.com.'''",
    ],
)
def test_the_scan_lets_derived_and_documented_urls_through(source):
    tree = ast.parse(source)
    assert hardcoded_urls(tree, Imports(tree)) == []
