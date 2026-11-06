"""fetch_untrusted(): SSRF guard, redirects, body cap; HttpError never carries a credential."""

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import src.core.http as http_module
from src.core.errors import HttpError
from src.core.http import BlockedUrlError, check_untrusted_url, fetch_untrusted, http_client
from src.core.urls import is_public_address, redact_url, redact_urls_in

# ── Address classification ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.17.0.2",  # Docker bridge: the MongoDB container, sibling services
        "192.168.1.10",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # CGNAT
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fc00::1",
        "fe80::1%eth0",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "not-an-ip",
    ],
)
def test_non_public_addresses(address):
    assert not is_public_address(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses(address):
    assert is_public_address(address)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/feed.xml",
        "http:///no-host",
        "http://127.0.0.1:27017/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::ffff:10.0.0.1]/",
    ],
)
def test_check_untrusted_url_refuses(url):
    with pytest.raises(BlockedUrlError):
        check_untrusted_url(url)


def test_check_untrusted_url_leaves_names_to_the_resolver():
    check_untrusted_url("https://example.com/feed.xml")  # no exception, no DNS here


def test_names_resolving_to_loopback_are_refused():
    """``localhost`` (or any name pointing inside) is caught by the resolver."""

    async def main():
        try:
            await fetch_untrusted("http://localhost:9/feed.xml")
        finally:
            await http_client.close()

    with pytest.raises(BlockedUrlError):
        asyncio.run(main())


# ── Against a local server (127.0.0.1 allowed for these tests only) ─────


@pytest.fixture
def loopback_is_public(monkeypatch):
    monkeypatch.setattr(http_module, "is_public_address", lambda a: a == "127.0.0.1")


def run_with_server(routes, scenario):
    async def main():
        app = web.Application()
        for path, handler in routes.items():
            app.router.add_get(path, handler)
        server = TestServer(app, host="127.0.0.1")
        await server.start_server()
        try:
            return await scenario(lambda path: str(server.make_url(path)))
        finally:
            await http_client.close()
            await server.close()

    return asyncio.run(main())


async def feed(_request):
    return web.Response(text="<rss>é</rss>", content_type="application/rss+xml")


def test_fetches_a_public_url(loopback_is_public):
    out = run_with_server({"/feed": feed}, lambda url: fetch_untrusted(url("/feed")))
    assert out == "<rss>é</rss>"


def test_json(loopback_is_public):
    async def data(_request):
        return web.json_response({"ok": True})

    out = run_with_server({"/d": data}, lambda url: fetch_untrusted(url("/d"), "json"))
    assert out == {"ok": True}


def test_follows_public_redirects(loopback_is_public):
    async def hop(_request):
        raise web.HTTPFound("/feed")

    out = run_with_server({"/hop": hop, "/feed": feed}, lambda url: fetch_untrusted(url("/hop")))
    assert out == "<rss>é</rss>"


def test_redirect_into_the_private_network_is_refused(loopback_is_public):
    async def hop(_request):
        raise web.HTTPFound("http://10.0.0.5:27017/")

    with pytest.raises(BlockedUrlError):
        run_with_server({"/hop": hop}, lambda url: fetch_untrusted(url("/hop")))


def test_redirect_loops_stop(loopback_is_public):
    async def loop(_request):
        raise web.HTTPFound("/loop")

    with pytest.raises(HttpError, match="redirections"):
        run_with_server({"/loop": loop}, lambda url: fetch_untrusted(url("/loop")))


def test_body_is_capped(loopback_is_public):
    async def big(_request):
        return web.Response(body=b"x" * 2048)

    async def chunked(request):
        response = web.StreamResponse()
        await response.prepare(request)  # no Content-Length: streamed
        for _ in range(4):
            await response.write(b"x" * 1024)
        return response

    routes = {"/big": big, "/chunked": chunked}
    for path in routes:
        with pytest.raises(HttpError, match="volumineuse"):
            run_with_server(routes, lambda url, p=path: fetch_untrusted(url(p), max_bytes=1000))


def test_http_errors_carry_the_status(loopback_is_public):
    async def missing(_request):
        raise web.HTTPNotFound()

    with pytest.raises(HttpError) as exc:
        run_with_server({"/x": missing}, lambda url: fetch_untrusted(url("/missing")))
    assert exc.value.status == 404


# ── No credential in errors ─────────────────────────────────────────────


def test_http_error_redacts_url_and_message():
    secret = "AIzaSECRET"  # pragma: allowlist secret
    url = f"https://www.googleapis.com/youtube/v3/videos?id=1&key={secret}"
    err = HttpError(f"Error fetching {url}: boom", url=url, status=403)
    assert secret not in str(err)
    assert secret not in err.url
    assert "key=***" in str(err)


def test_redact_helpers():
    assert redact_url("https://user:pw@host/p?token=t&x=1") == "https://***@host/p?token=***&x=1"
    assert (
        redact_urls_in("see http://h/?api_key=k and more") == "see http://h/?api_key=*** and more"
    )
