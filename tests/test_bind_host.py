"""Where the dashboard and /metrics listen when the config doesn't say (S12)."""

from src.core.config import bind_host


def test_bind_host_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("MICHEL_BIND_HOST", raising=False)
    assert bind_host(None) == "127.0.0.1"
    assert bind_host("  ") == "127.0.0.1"


def test_bind_host_prefers_the_config_then_the_environment(monkeypatch):
    monkeypatch.setenv("MICHEL_BIND_HOST", "0.0.0.0")  # the Docker image
    assert bind_host("") == "0.0.0.0"
    assert bind_host("192.168.1.10") == "192.168.1.10"
