"""Which per-guild public dashboards exist, and where they live.

Framework-free on purpose: ``routes/public.py`` uses it to gate the
unauthenticated endpoints, and extensions use it to link to the pages from
Discord embeds without importing FastAPI.

A public dashboard is opt-in per guild: the module must be enabled *and* its
``publicDashboard`` flag set from the module's Web UI page. The pages
themselves are served by the SPA at ``/public/{guild_id}/{kind}``.
"""

from typing import Any

# Dashboard kind (URL segment) -> module config key that owns it.
PUBLIC_DASHBOARDS: dict[str, str] = {
    "xp": "moduleXp",
    "spotify": "moduleSpotify",
}

PUBLIC_FLAG = "publicDashboard"


def public_dashboard_enabled(server_config: dict[str, Any], kind: str) -> bool:
    """True if *kind*'s public page is switched on in this guild's config."""
    module_key = PUBLIC_DASHBOARDS.get(kind)
    if module_key is None:
        return False
    module = server_config.get(module_key) or {}
    return bool(module.get("enabled")) and bool(module.get(PUBLIC_FLAG))


def enabled_public_dashboards(server_config: dict[str, Any]) -> list[str]:
    """The public dashboard kinds this guild exposes, in display order."""
    return [kind for kind in PUBLIC_DASHBOARDS if public_dashboard_enabled(server_config, kind)]


def public_dashboard_url(global_config: dict[str, Any], guild_id: str | int, kind: str) -> str:
    """Absolute URL of a guild's public *kind* page, or ``""`` when unreachable.

    Empty when the Web UI is off or ``webui.baseUrl`` is unset — callers treat
    that as "no link", like any other unset URL. Does not check the per-guild
    opt-in; pair it with :func:`public_dashboard_enabled`.
    """
    webui = global_config.get("webui") or {}
    base = str(webui.get("baseUrl") or "").strip().rstrip("/")
    if not webui.get("enabled") or not base:
        return ""
    return f"{base}/public/{guild_id}/{kind}"
