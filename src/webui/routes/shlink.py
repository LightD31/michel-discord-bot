"""Short-link management endpoints (Shlink).

Developer-only CRUD over the configured Shlink instance so the links the bot
posts — and any link created by hand — can be listed, retargeted, retagged or
deleted from the dashboard instead of Shlink's own admin UI.

Shlink is the source of truth: nothing is mirrored into MongoDB. Every call
hits the API through :mod:`src.integrations.shlink`, which reads the global
``shlink`` config section on each request, so a credential change in the
dashboard applies immediately.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.core import logging as logutil
from src.integrations.shlink import ShlinkError, get_settings, shlink_client
from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.routes.shlink")

MAX_ITEMS_PER_PAGE = 100


# ── Request models ──────────────────────────────────────────────────


class ShortUrlCreate(BaseModel):
    long_url: str = Field(min_length=1, max_length=4096)
    title: str | None = Field(default=None, max_length=512)
    custom_slug: str | None = Field(default=None, max_length=128)
    tags: list[str] = Field(default_factory=list)
    find_if_exists: bool = True


class ShortUrlUpdate(BaseModel):
    long_url: str | None = Field(default=None, min_length=1, max_length=4096)
    title: str | None = Field(default=None, max_length=512)
    tags: list[str] | None = None


# ── Helpers ─────────────────────────────────────────────────────────


def _clean_tags(tags: list[str]) -> list[str]:
    return [t.strip() for t in tags if t and t.strip()]


def _validate_long_url(url: str) -> str:
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="L'URL doit commencer par http:// ou https://")
    return url


def _short_url_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    """Project a Shlink short-url object onto the fields the dashboard shows."""
    return {
        "short_code": item.get("shortCode", ""),
        "short_url": item.get("shortUrl", ""),
        "long_url": item.get("longUrl", ""),
        "title": item.get("title") or "",
        "tags": item.get("tags") or [],
        "domain": item.get("domain") or "",
        "date_created": item.get("dateCreated") or "",
        "visits": item.get("visitsSummary", {}).get("total", item.get("visitsCount", 0)),
    }


def _shlink_error(e: ShlinkError) -> HTTPException:
    """Map an integration failure onto a status the dashboard can display."""
    status = 400 if e.status and 400 <= e.status < 500 else 502
    return HTTPException(status_code=status, detail=str(e))


# ── Router factory ──────────────────────────────────────────────────


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    @router.get("/api/shlink/status")
    async def api_shlink_status(request: Request):
        """Report whether Shlink is reachable and whether auto-shortening is on."""
        ctx.require_developer(request)
        settings = get_settings()
        return JSONResponse(
            {
                "configured": settings.configured,
                "enabled": settings.enabled,
                "base_url": settings.base_url,
                "domain": settings.domain,
                "default_tags": settings.tags,
                "excluded_domains": settings.excluded_domains,
            }
        )

    @router.get("/api/shlink/short-urls")
    async def api_list_short_urls(
        request: Request,
        page: int = 1,
        items_per_page: int = 25,
        search: str = "",
        tags: str = "",
    ):
        ctx.require_developer(request)
        page = max(1, page)
        items_per_page = max(1, min(items_per_page, MAX_ITEMS_PER_PAGE))
        tag_list = _clean_tags(tags.split(",")) if tags else None
        try:
            result = await shlink_client.list_short_urls(
                page=page,
                items_per_page=items_per_page,
                search_term=search.strip() or None,
                tags=tag_list,
            )
        except ShlinkError as e:
            raise _shlink_error(e) from e
        return JSONResponse(
            {
                "short_urls": [_short_url_to_dict(item) for item in result.get("data", [])],
                "pagination": result.get("pagination", {}),
            }
        )

    @router.post("/api/shlink/short-urls")
    async def api_create_short_url(request: Request, payload: ShortUrlCreate):
        session = ctx.require_developer(request)
        long_url = _validate_long_url(payload.long_url)
        try:
            created = await shlink_client.create_short_url(
                long_url,
                title=payload.title,
                tags=_clean_tags(payload.tags),
                custom_slug=(payload.custom_slug or "").strip() or None,
                find_if_exists=payload.find_if_exists,
            )
        except ShlinkError as e:
            raise _shlink_error(e) from e
        logger.info(
            "Short URL %s -> %s created by %s (%s)",
            created.get("shortUrl"),
            long_url,
            session.username,
            session.user_id,
        )
        return JSONResponse(_short_url_to_dict(created))

    @router.patch("/api/shlink/short-urls/{short_code}")
    async def api_update_short_url(request: Request, short_code: str, payload: ShortUrlUpdate):
        session = ctx.require_developer(request)
        long_url = _validate_long_url(payload.long_url) if payload.long_url else None
        try:
            updated = await shlink_client.update_short_url(
                short_code,
                long_url=long_url,
                title=payload.title,
                tags=_clean_tags(payload.tags) if payload.tags is not None else None,
            )
        except ShlinkError as e:
            raise _shlink_error(e) from e
        logger.info(
            "Short URL %s updated by %s (%s)", short_code, session.username, session.user_id
        )
        return JSONResponse(_short_url_to_dict(updated))

    @router.delete("/api/shlink/short-urls/{short_code}")
    async def api_delete_short_url(request: Request, short_code: str):
        session = ctx.require_developer(request)
        try:
            await shlink_client.delete_short_url(short_code)
        except ShlinkError as e:
            raise _shlink_error(e) from e
        logger.info(
            "Short URL %s deleted by %s (%s)", short_code, session.username, session.user_id
        )
        return JSONResponse({"success": True})

    return router
