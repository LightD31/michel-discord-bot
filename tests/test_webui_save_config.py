"""Tests for WebUIContext.save_config failure handling.

Dashboard saves must surface the underlying write error (e.g. a config/
bind mount the container uid cannot write to) and must refuse to persist
an empty config skeleton produced by an unreadable config file.
"""

import pytest
from fastapi import HTTPException

from src.webui.auth import DiscordOAuth
from src.webui.context import WebUIContext


@pytest.fixture
def ctx():
    oauth = DiscordOAuth(client_id="", client_secret="", redirect_uri="")
    return WebUIContext(bot=None, bot_loop=None, oauth=oauth)


def test_save_refuses_empty_config(ctx):
    with pytest.raises(HTTPException) as exc:
        ctx.save_config({})
    assert exc.value.status_code == 503
    assert "refusée" in exc.value.detail


def test_save_surfaces_permission_error(ctx, monkeypatch):
    from src.core.config import config_store

    def boom(_data):
        raise PermissionError(13, "Permission denied", "config/.config-x.json.tmp")

    monkeypatch.setattr(config_store, "save_full", boom)
    with pytest.raises(HTTPException) as exc:
        ctx.save_config({"config": {"x": 1}, "servers": {}})
    assert exc.value.status_code == 500
    assert "Permission denied" in exc.value.detail
    assert "chown -R 1000:1000" in exc.value.detail


def test_save_passes_through_on_success(ctx, monkeypatch):
    from src.core.config import config_store

    saved = {}
    monkeypatch.setattr(config_store, "save_full", saved.update)
    ctx.save_config({"config": {"x": 1}, "servers": {}})
    assert saved == {"config": {"x": 1}, "servers": {}}
