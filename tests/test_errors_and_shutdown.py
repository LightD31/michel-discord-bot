"""Members get a French reply when an interaction fails; SIGTERM stops the bot cleanly."""

import asyncio
import logging
from types import SimpleNamespace

import pytest
from interactions import errors

import src.discord_ext.lifecycle as lifecycle
from extensions.errors import ErrorsExtension
from src.discord_ext.errors import is_expected, user_message

# ── Error replies ───────────────────────────────────────────────────────


def bare(cls, **attrs):
    """An instance of an interactions error without its library-specific constructor."""
    error = cls.__new__(cls)
    for name, value in attrs.items():
        setattr(error, name, value)
    return error


def test_expected_errors_get_a_specific_message_and_are_not_failures():
    cooldown = bare(
        errors.CommandOnCooldown, cooldown=SimpleNamespace(get_cooldown_time=lambda: 12.7)
    )
    check = bare(errors.CommandCheckFailure)
    busy = bare(errors.MaxConcurrencyReached)
    assert user_message(cooldown, "ref") == "⏳ Doucement ! Réessaie dans 12 s."
    assert "permission" in user_message(check, "ref")
    assert "déjà en cours" in user_message(busy, "ref")
    assert all(is_expected(e) for e in (cooldown, check, busy))
    english = [user_message(e, "ref", french=False) for e in (cooldown, check, busy)]
    assert english == [
        "⏳ Easy! Try again in 12 s.",
        "🚫 You are not allowed to use this command.",
        "⏳ This command is already running, try again in a moment.",
    ]


def test_an_unexpected_error_shows_its_reference():
    error = RuntimeError("boom")
    assert not is_expected(error)
    message = user_message(error, "a1b2c3")
    assert "`a1b2c3`" in message and "boom" not in message  # no internals leak


class Ctx:
    invoke_target = "bilan"
    custom_id = "poll:1"

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send(self, content, ephemeral=False):
        if self.fail:
            raise RuntimeError("Unknown interaction")
        self.sent.append((content, ephemeral))


def test_an_unexpected_command_error_is_logged_with_the_reference_shown(caplog):
    ext = object.__new__(ErrorsExtension)
    ctx = Ctx()
    event = SimpleNamespace(ctx=ctx, error=RuntimeError("boom"))
    with caplog.at_level(logging.ERROR, logger="extensions.errors"):
        asyncio.run(ErrorsExtension.on_command_error.callback(ext, event))
    ((content, ephemeral),) = ctx.sent
    assert ephemeral
    reference = content.split("`")[1]
    (record,) = [r for r in caplog.records if reference in r.getMessage()]
    assert "/bilan" in record.getMessage() and record.exc_info


def test_an_expired_interaction_does_not_raise():
    ext = object.__new__(ErrorsExtension)
    event = SimpleNamespace(ctx=Ctx(fail=True), error=bare(errors.CommandCheckFailure))
    asyncio.run(ErrorsExtension.on_component_error.callback(ext, event))  # no exception


# ── Graceful shutdown ───────────────────────────────────────────────────


class FakeTaskOwner:
    dropped = False

    async def async_drop(self):
        self.dropped = True


class FakeClient:
    def __init__(self, ext):
        self.ext = {"extensions.x": ext}
        self.stopped = False

    async def stop(self):
        self.stopped = True


def test_shutdown_releases_extensions_closes_resources_then_stops_the_gateway():
    order = []
    ext = FakeTaskOwner()
    client = FakeClient(ext)

    async def close_http():
        order.append("http")

    async def close_db():
        order.append("db")
        raise RuntimeError("already closed")  # a failing closer doesn't stop the rest

    shutdown = lifecycle.GracefulShutdown(client, closers=[close_http, close_db])
    asyncio.run(shutdown.shutdown())
    assert ext.dropped and order == ["http", "db"] and client.stopped


def test_a_second_signal_is_ignored_and_a_hung_shutdown_still_exits(monkeypatch):
    monkeypatch.setattr(lifecycle, "SHUTDOWN_TIMEOUT_SECONDS", 0.05)
    exits = []

    class HungClient(FakeClient):
        async def stop(self):
            await asyncio.sleep(10)

    async def main():
        shutdown = lifecycle.GracefulShutdown(
            HungClient(FakeTaskOwner()), closers=[], exit_process=exits.append
        )
        shutdown.request()
        shutdown.request()  # ignored
        await asyncio.sleep(0.2)
        return shutdown

    shutdown = asyncio.run(main())
    assert shutdown.requested and exits == [0]


@pytest.mark.parametrize("sig", lifecycle.SHUTDOWN_SIGNALS)
def test_install_routes_the_signals_to_request(sig):
    handlers = {}
    loop = SimpleNamespace(add_signal_handler=lambda s, cb, *args: handlers.update({s: (cb, args)}))
    shutdown = lifecycle.GracefulShutdown(FakeClient(FakeTaskOwner()), closers=[])
    shutdown.install(loop)
    callback, args = handlers[sig]
    assert callback == shutdown.request and args == (sig,)
