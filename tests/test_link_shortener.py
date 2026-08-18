"""Tests for the Shlink link-shortening layer.

Covers the pure URL rules (``features.links.shortener``), the async wrappers
that must never lose a post (``features.links``), and the client's cache /
settings plumbing (``src.integrations.shlink``).
"""

import pytest

import features.links as links
from features.links.shortener import (
    clean_url,
    extract_urls,
    is_media_url,
    replace_urls,
    should_shorten,
)
from src.integrations.shlink import ShlinkClient, ShlinkError, ShlinkSettings, _error_detail

SHORT_HOSTS = {"exemple.link"}


def _settings(**overrides) -> ShlinkSettings:
    raw = {
        "enabled": True,
        "shlinkBaseUrl": "https://exemple.link",
        "shlinkApiKey": "test-key",  # pragma: allowlist secret
    }
    raw.update(overrides)
    return ShlinkSettings(raw)


# ── Pure rules ──────────────────────────────────────────────────────


def test_clean_url_strips_trailing_punctuation():
    assert clean_url("https://example.com/article.") == "https://example.com/article"
    assert clean_url("https://example.com/a,") == "https://example.com/a"


def test_clean_url_keeps_balanced_parentheses():
    url = "https://fr.wikipedia.org/wiki/Python_(langage)"
    assert clean_url(url) == url
    assert clean_url("https://example.com/a)") == "https://example.com/a"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article-tres-long?utm_source=rss",
        "http://news.example.org/2026/08/some-story",
    ],
)
def test_should_shorten_accepts_external_links(url):
    assert should_shorten(url, short_hosts=SHORT_HOSTS)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/file",  # not http(s)
        "https://exemple.link/abc123",  # already one of our short URLs
        "https://discord.com/channels/1/2/3",  # Discord navigation
        "https://cdn.discordapp.com/attachments/1/2/img.png",
        "https://example.com/image.png",  # embed media
        "https://example.com/clip.mp4",
        "https://twitch.tv",  # bare domain, nothing to gain
    ],
)
def test_should_shorten_rejects_non_candidates(url):
    assert not should_shorten(url, short_hosts=SHORT_HOSTS)


def test_should_shorten_honors_excluded_domains():
    assert not should_shorten(
        "https://www.twitch.tv/someone", short_hosts=SHORT_HOSTS, excluded_domains=["twitch.tv"]
    )
    assert should_shorten(
        "https://example.com/x", short_hosts=SHORT_HOSTS, excluded_domains=["twitch.tv"]
    )


def test_is_media_url_ignores_query_string():
    assert is_media_url("https://example.com/photo.jpg")
    assert not is_media_url("https://example.com/photo")


def test_extract_urls_dedupes_and_filters():
    text = (
        "Voir [l'article](https://example.com/a) et https://example.com/a encore, "
        "plus une image https://example.com/img.png et <https://example.com/b>."
    )
    assert extract_urls(text, short_hosts=SHORT_HOSTS) == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_replace_urls_preserves_markdown_and_punctuation():
    text = "Lire [ici](https://example.com/a), ou (https://example.com/b)."
    out = replace_urls(
        text,
        {
            "https://example.com/a": "https://exemple.link/aa",
            "https://example.com/b": "https://exemple.link/bb",
        },
    )
    assert out == "Lire [ici](https://exemple.link/aa), ou (https://exemple.link/bb)."


def test_replace_urls_leaves_unmapped_urls_alone():
    text = "https://example.com/a et https://example.com/b"
    out = replace_urls(text, {"https://example.com/a": "https://exemple.link/aa"})
    assert out == "https://exemple.link/aa et https://example.com/b"


# ── Async wrappers ──────────────────────────────────────────────────


class _FakeClient:
    """Stand-in for ``shlink_client`` recording the calls it received."""

    def __init__(self, result="https://exemple.link/xyz", error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[str] = []
        self.cache: dict[str, str] = {}

    async def shorten(self, long_url, *, title=None, tags=None, use_cache=True):
        self.calls.append(long_url)
        if self.error:
            raise self.error
        return self.result

    def cache_get(self, long_url, *, allow_stale=False):
        return self.cache.get(long_url)


def _patch(monkeypatch, client, settings):
    monkeypatch.setattr(links, "shlink_client", client)
    monkeypatch.setattr(links, "get_settings", lambda: settings)


async def test_shorten_url_passthrough_when_disabled(monkeypatch):
    client = _FakeClient()
    _patch(monkeypatch, client, _settings(enabled=False))
    assert await links.shorten_url("https://example.com/a") == "https://example.com/a"
    assert client.calls == []


async def test_shorten_url_passthrough_when_unconfigured(monkeypatch):
    client = _FakeClient()
    _patch(monkeypatch, client, _settings(shlinkApiKey=""))
    assert await links.shorten_url("https://example.com/a") == "https://example.com/a"
    assert client.calls == []


async def test_shorten_url_shortens_external_link(monkeypatch):
    client = _FakeClient()
    _patch(monkeypatch, client, _settings())
    assert await links.shorten_url("https://example.com/a") == "https://exemple.link/xyz"
    assert client.calls == ["https://example.com/a"]


async def test_shorten_url_skips_excluded_link(monkeypatch):
    client = _FakeClient()
    _patch(monkeypatch, client, _settings(shlinkExcludedDomains=["example.com"]))
    assert await links.shorten_url("https://example.com/a") == "https://example.com/a"
    assert client.calls == []


async def test_shorten_url_falls_back_on_api_error(monkeypatch):
    client = _FakeClient(error=ShlinkError("boom", status=500))
    _patch(monkeypatch, client, _settings())
    assert await links.shorten_url("https://example.com/a") == "https://example.com/a"


async def test_shorten_url_falls_back_on_unexpected_error(monkeypatch):
    client = _FakeClient(error=RuntimeError("kaboom"))
    _patch(monkeypatch, client, _settings())
    assert await links.shorten_url("https://example.com/a") == "https://example.com/a"


async def test_shorten_url_reuses_stale_short_url_on_failure(monkeypatch):
    """A refreshed embed must keep its short URL through a Shlink outage."""
    client = _FakeClient(error=ShlinkError("down"))
    client.cache["https://example.com/a"] = "https://exemple.link/old"
    _patch(monkeypatch, client, _settings())
    assert await links.shorten_url("https://example.com/a") == "https://exemple.link/old"


async def test_shorten_text_rewrites_only_candidates(monkeypatch):
    client = _FakeClient()
    _patch(monkeypatch, client, _settings())
    text = "Nouvelle vidéo https://www.youtube.com/watch?v=abc — aperçu https://example.com/i.png"
    out = await links.shorten_text(text)
    assert out == "Nouvelle vidéo https://exemple.link/xyz — aperçu https://example.com/i.png"
    assert client.calls == ["https://www.youtube.com/watch?v=abc"]


async def test_shorten_text_noop_when_disabled(monkeypatch):
    client = _FakeClient()
    _patch(monkeypatch, client, _settings(enabled=False))
    text = "https://example.com/a"
    assert await links.shorten_text(text) == text


# ── Client plumbing ─────────────────────────────────────────────────


def test_settings_short_hosts_cover_base_url_and_domain():
    settings = _settings(shlinkDomain="court.example")
    assert settings.short_hosts == {"exemple.link", "court.example"}
    assert settings.configured


def test_settings_unconfigured_without_base_url():
    assert not _settings(shlinkBaseUrl="").configured


def test_client_cache_roundtrip_and_clear():
    client = ShlinkClient()
    client.cache_put("https://example.com/a", "https://exemple.link/aa")
    assert client.cache_get("https://example.com/a") == "https://exemple.link/aa"
    client.cache_clear()
    assert client.cache_get("https://example.com/a") is None


def test_client_cache_expires(monkeypatch):
    import src.integrations.shlink as shlink_module

    client = ShlinkClient()
    client.cache_put("https://example.com/a", "https://exemple.link/aa")
    monkeypatch.setattr(shlink_module, "_CACHE_TTL_SECONDS", -1)
    assert client.cache_get("https://example.com/a") is None


async def test_client_request_requires_configuration(monkeypatch):
    import src.integrations.shlink as shlink_module

    monkeypatch.setattr(shlink_module, "get_settings", lambda: _settings(shlinkApiKey=""))
    with pytest.raises(ShlinkError):
        await ShlinkClient().list_short_urls()


def test_error_detail_uses_api_message():
    assert "Slug déjà pris" in _error_detail({"detail": "Slug déjà pris"}, 400)
    assert "HTTP 500" in _error_detail(None, 500)


def test_encode_params_expands_lists_and_drops_none():
    from src.integrations.shlink import _encode_params

    assert _encode_params({"page": 2, "tags[]": ["rss", "news"], "searchTerm": None}) == [
        ("page", "2"),
        ("tags[]", "rss"),
        ("tags[]", "news"),
    ]
    assert _encode_params(None) == []


def test_quote_keeps_short_code_in_one_path_segment():
    from src.integrations.shlink import _quote

    assert _quote("../../admin") == "..%2F..%2Fadmin"
    assert _quote("abc123") == "abc123"
