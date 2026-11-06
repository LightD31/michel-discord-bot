"""URL helpers shared by the HTTP client and the error types.

- :func:`redact_url` / :func:`redact_urls_in` strip credentials (userinfo and
  credential-carrying query parameters) before a URL reaches a log line or an
  exception message.
- :func:`is_public_address` decides whether an address may be reached on behalf
  of a URL someone else chose (see :func:`src.core.http.fetch_untrusted`).
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

SENSITIVE_QUERY_KEYS = ("token", "access_token", "key", "api_key", "apikey", "password", "secret")

_URL_IN_TEXT = re.compile(r"""https?://[^\s'"<>]+""")


def redact_url(url: str) -> str:
    """Strip userinfo and known credential-carrying query params from *url*.

    Used before logging so credentials passed in the URL never end up on disk.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "<unparseable url>"
    netloc = parts.hostname or ""
    if port:
        netloc = f"{netloc}:{port}"
    if parts.username:
        netloc = f"***@{netloc}"
    query = parts.query
    if query:
        kept = []
        for pair in query.split("&"):
            key, sep, _ = pair.partition("=")
            if key.lower() in SENSITIVE_QUERY_KEYS:
                kept.append(f"{key}{sep}***" if sep else key)
            else:
                kept.append(pair)
        query = "&".join(kept)
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def redact_urls_in(text: str) -> str:
    """Apply :func:`redact_url` to every http(s) URL inside *text*."""
    return _URL_IN_TEXT.sub(lambda m: redact_url(m.group(0)), text)


def is_public_address(address: str) -> bool:
    """True if *address* (an IP literal) is globally routable.

    Loopback, private (RFC 1918, ULA), link-local (cloud metadata endpoints
    included), CGNAT, multicast, unspecified and reserved ranges are not; an
    IPv4-mapped IPv6 address is judged by the IPv4 address it carries. Anything
    unparseable is not public either.
    """
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop an IPv6 zone id
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def host_is_ip_literal(host: str) -> bool:
    """True if *host* (as in a URL's netloc, brackets allowed) is an IP address, not a name."""
    try:
        ipaddress.ip_address(host.strip("[]").split("%", 1)[0])
    except ValueError:
        return False
    return True


__all__ = [
    "SENSITIVE_QUERY_KEYS",
    "host_is_ip_literal",
    "is_public_address",
    "redact_url",
    "redact_urls_in",
]
