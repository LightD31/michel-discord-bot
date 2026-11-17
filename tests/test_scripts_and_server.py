"""``scripts/check_config.py`` and the dashboard's launcher (T01, T05)."""

import json
import socket
import time
import urllib.error
import urllib.request

import pytest

from scripts import check_config


@pytest.fixture
def no_extension_import(monkeypatch):
    """The schemas registered so far are enough; importing every extension isn't."""
    monkeypatch.setattr(check_config, "_import_extensions", lambda: ["extensions.broken"])


def test_a_valid_config_passes(tmp_path, capsys, no_extension_import):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"config": {}, "servers": {}}), encoding="utf-8")
    check_config.main([str(path)])
    out = capsys.readouterr()
    assert f"{path} : OK" in out.out
    assert "could not import extensions.broken" in out.err


def test_an_invalid_config_is_reported_and_fails(tmp_path, capsys, no_extension_import):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"config": {"webui": {"notAField": True}}, "servers": {}}), encoding="utf-8"
    )
    with pytest.raises(SystemExit, match="problème"):
        check_config.main([str(path)])
    assert "notAField" in capsys.readouterr().out


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_dashboard_starts_on_its_own_thread(store_config):
    from src.webui.server import start_webui

    store_config(
        {
            "config": {
                "discord": {"botToken": "x"},
                "webui": {"clientId": "1", "clientSecret": "x"},  # pragma: allowlist secret
            },
            "servers": {},
        }
    )
    port = free_port()
    server, thread = start_webui(bot=None, host="127.0.0.1", port=port)
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started and thread.daemon and thread.name == "webui-server"
        try:
            status = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).status
        except urllib.error.HTTPError as e:
            status = e.code
        assert status < 500
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert not thread.is_alive()
