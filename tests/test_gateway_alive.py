"""The heartbeat file needs a gateway that still heartbeats, not only a running loop (O04)."""

from types import SimpleNamespace

from src.discord_ext.lifecycle import gateway_alive


def client(last, interval=41.25):
    gateway = SimpleNamespace(heartbeat_interval=interval, _last_heartbeat=last)
    return SimpleNamespace(_connection_state=SimpleNamespace(gateway=gateway))


def test_a_recent_heartbeat_is_alive():
    assert gateway_alive(client(last=1000.0), now=1040.0)


def test_a_gateway_stuck_reconnecting_is_not():
    assert not gateway_alive(client(last=1000.0), now=1000.0 + 41.25 * 3)


def test_unknown_internals_count_as_alive():
    assert gateway_alive(SimpleNamespace())  # no connection state yet
    assert gateway_alive(client(last=0))  # never heartbeated yet: startup
    assert gateway_alive(client(last=1000.0, interval=None), now=99999.0)
