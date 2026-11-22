"""The Satisfactory dedicated server's API, pinned and bounded (no Discord).

Shared by the status extension and the dashboard's "Vérifier la connexion"
(P16): ``pyfactorybridge`` is synchronous, so every call goes through
:func:`call` (a thread, a timeout), and the API token is only ever sent to a
server whose certificate has the configured fingerprint.
"""

from __future__ import annotations

import asyncio
import re

import requests
from pyfactorybridge import API
from requests.adapters import HTTPAdapter

# pyfactorybridge's requests carry no timeout: a server that accepts the
# connection and then never answers held a worker thread for good, every later
# refresh was skipped (the task was "still running") and the process couldn't
# stop (the default executor is joined at exit). (connect, read) seconds:
REQUEST_TIMEOUT = (5.0, 10.0)
# Ceiling for one client call, which may make a few requests (the constructor
# checks the server's health, then authenticates).
CALL_TIMEOUT_SECONDS = 30.0


_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


def normalize_fingerprint(value: object) -> str | None:
    """A SHA-256 fingerprint as 64 hex digits (``AB:CD:…`` accepted), else ``None``."""
    text = str(value or "").strip().lower()
    for prefix in ("sha256 fingerprint=", "sha256="):
        text = text.removeprefix(prefix)
    text = text.replace(":", "").replace(" ", "")
    return text if _FINGERPRINT_RE.match(text) else None


class _PinnedAdapter(HTTPAdapter):
    """Accepts the server only if its certificate has the configured SHA-256 fingerprint.

    pyfactorybridge sends every request with ``verify=False`` (a dedicated
    server's certificate is self-signed), so the API token went to whoever
    answered on the address. urllib3 checks ``assert_fingerprint`` whatever
    ``verify`` says, before any byte of the request is sent.
    """

    def __init__(self, fingerprint: str) -> None:
        self._fingerprint = fingerprint
        super().__init__()

    def init_poolmanager(self, *args, **kwargs):
        kwargs["assert_fingerprint"] = self._fingerprint
        return super().init_poolmanager(*args, **kwargs)


class _TimeoutSession(requests.Session):
    """A ``requests`` session whose requests give up after :data:`REQUEST_TIMEOUT`."""

    def request(self, method, url, *args, **kwargs):
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        return super().request(method, url, *args, **kwargs)


def build_api(address: str, token: str, fingerprint: str) -> API:
    """``API(address=..., token=...)`` on a pinned session with timeouts (blocking).

    The constructor makes its requests before a session could be swapped in,
    so this does its three steps itself: the endpoint URL, a health check
    (which fails on an unreachable server, or one with another certificate)
    and the token.
    """
    api = API.__new__(API)
    api.URL = f"https://{address}/api/v1/"
    api.auth = None
    api.session = _TimeoutSession()
    api.session.mount("https://", _PinnedAdapter(fingerprint))
    api.get_server_health()
    api.renew_auth(method="token", value=token)
    return api


async def call(fn, *args):
    """Run a blocking pyfactorybridge call off the loop, within :data:`CALL_TIMEOUT_SECONDS`."""
    return await asyncio.wait_for(asyncio.to_thread(fn, *args), CALL_TIMEOUT_SECONDS)


def connection_of(settings: dict) -> tuple[str, str, str | None] | None:
    """``(address, token, fingerprint)`` of the guild's server, None until address and token are set."""
    ip = str(settings.get("satisfactoryServerIp") or "").strip()
    token = str(settings.get("satisfactoryServerToken") or "").strip()
    if not ip or not token:
        return None
    port = str(settings.get("satisfactoryServerPort") or "7777").strip()
    fingerprint = normalize_fingerprint(settings.get("satisfactoryServerCertificateSha256"))
    return f"{ip}:{port}", token, fingerprint


__all__ = [
    "CALL_TIMEOUT_SECONDS",
    "REQUEST_TIMEOUT",
    "build_api",
    "call",
    "connection_of",
    "normalize_fingerprint",
]
