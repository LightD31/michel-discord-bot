"""The monitors of a server's own Kuma, for its alerts' picker (P42)."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from webui_fakes import FakeWebUI

import src.webui.routes.uptime as uptime_routes
from src.integrations.uptime_kuma import MonitorState, MonitorStatus, UptimeKumaError

KUMA = {"uptimeKumaUrl": "https://kuma.ici", "uptimeKumaApiKey": "k"}  # pragma: allowlist secret


def monitors(ctx, guild="1"):
    router = uptime_routes.create_router(ctx)
    (route,) = [r for r in router.routes if r.path.endswith("/uptime/monitors")]
    return asyncio.run(route.endpoint(None, guild))


def test_the_servers_own_kuma_lists_its_monitors_by_name(monkeypatch):
    async def states(settings):
        assert (settings.base_url, settings.api_key) == ("https://kuma.ici", "k")
        return {
            2: MonitorState(2, "Proxy", None, MonitorStatus.UP),
            1: MonitorState(1, "NAS", None, MonitorStatus.DOWN),
        }

    monkeypatch.setattr(uptime_routes, "fetch_monitor_states", states)
    # Read even while the module is off: alerts are set up before switching it on.
    ctx = FakeWebUI.with_server("1", {"moduleUptime": {"enabled": False, **KUMA}})
    body = json.loads(monitors(ctx).body)
    assert body == {
        "monitors": [
            {"id": "1", "name": "NAS", "status": 0},
            {"id": "2", "name": "Proxy", "status": 1},
        ]
    }


def test_no_kuma_or_a_kuma_down_says_so(monkeypatch):
    with pytest.raises(HTTPException) as unset:
        monitors(FakeWebUI.with_server("1", {"moduleUptime": {}}))
    assert unset.value.status_code == 422

    async def down(settings):
        raise UptimeKumaError("401")

    monkeypatch.setattr(uptime_routes, "fetch_monitor_states", down)
    with pytest.raises(HTTPException) as refused:
        monitors(FakeWebUI.with_server("1", {"moduleUptime": KUMA}))
    assert refused.value.status_code == 502


def test_only_the_servers_admins_read_its_monitors():
    ctx = FakeWebUI.with_server("1", {"moduleUptime": KUMA}, manages=["2"])
    with pytest.raises(HTTPException) as refused:
        monitors(ctx)
    assert refused.value.status_code == 403
