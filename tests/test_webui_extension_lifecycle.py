"""Dashboard load / reload / unload keep an extension's lifecycle whole.

Runs a real (unconnected) ``interactions.Client`` against a throwaway package
extension shaped like the bot's own — config read in ``_common`` at import, a
command on a mixin, a task started from ``on_startup`` — because the bugs live
in the library's reload itself:

- the package's submodules stayed cached, so the config never refreshed and
  the mixin's command stayed bound to the unloaded instance;
- the old instance's tasks kept running;
- Startup never fires again, so the new instance's tasks never started.
"""

import asyncio
import sys
import textwrap

import interactions
import pytest

from src.webui import botops

PKG = "lifecycle_probe_ext"


class _Ctx:
    """Stands in for WebUIContext: the test already runs on the "bot loop"."""

    def __init__(self, bot) -> None:
        self.bot = bot

    async def run_on_bot_loop(self, func, *args, timeout, **kwargs):
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            result = await result
        return result


@pytest.fixture
def ext_package(tmp_path, monkeypatch):
    """Write the probe package + a config file its ``_common`` reads at import."""
    config_file = tmp_path / "value.txt"
    config_file.write_text("v1")
    events_mod = tmp_path / "lifecycle_probe_events.py"
    events_mod.write_text("EVENTS = []\n")

    pkg = tmp_path / PKG
    pkg.mkdir()
    (pkg / "_common.py").write_text(
        "import pathlib\n"
        f"VALUE = pathlib.Path({str(config_file)!r}).read_text()\n"
        "if VALUE == 'broken':\n"
        "    raise RuntimeError('bad config')\n"
    )
    (pkg / "mixin.py").write_text(
        textwrap.dedent(
            """
            from interactions import SlashContext, slash_command

            class Mixin:
                @slash_command(name="probe", description="probe", scopes=[123])
                async def probe(self, ctx: SlashContext):
                    pass
            """
        )
    )
    (pkg / "__init__.py").write_text(
        textwrap.dedent(
            """
            from interactions import Extension, IntervalTrigger, Task, listen

            from lifecycle_probe_events import EVENTS

            from ._common import VALUE
            from .mixin import Mixin

            class ProbeExtension(Extension, Mixin):
                def __init__(self, bot):
                    self.value = VALUE

                @listen()
                async def on_startup(self):
                    EVENTS.append(("startup", self.value))
                    self.tick.start()

                @listen()
                async def on_ready(self, event):
                    EVENTS.append(("ready", self.value))

                @Task.create(IntervalTrigger(hours=1))
                async def tick(self):
                    pass

                async def async_drop(self):
                    EVENTS.append(("drop", self.value))
            """
        )
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    yield config_file
    for name in [m for m in sys.modules if m == PKG or m.startswith(f"{PKG}.")]:
        del sys.modules[name]
    sys.modules.pop("lifecycle_probe_events", None)


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


def _events():
    return sys.modules["lifecycle_probe_events"].EVENTS


async def test_reload_refreshes_config_rebinds_commands_and_restarts_tasks(ext_package):
    bot = interactions.Client()
    ctx = _Ctx(bot)

    await botops.run_extension_op(ctx, "load", PKG)
    await _settle()
    assert _events() == []  # not ready yet: the gateway's own Startup will run it

    bot._ready.set()
    first = bot.ext["ProbeExtension"]
    first_tick = type(first).tick

    ext_package.write_text("v2")
    await botops.run_extension_op(ctx, "reload", PKG)
    await _settle()

    second = bot.ext["ProbeExtension"]
    assert second is not first
    assert second.value == "v2"  # _common was re-imported
    command = bot.interactions_by_scope[123]["probe"]
    assert command.callback.args[0] is second  # the mixin command follows
    assert not first_tick.running  # old loop stopped
    assert type(second).tick.running  # new loop started by on_startup
    assert _events() == [("drop", "v1"), ("startup", "v2"), ("ready", "v2")]

    await botops.run_extension_op(ctx, "unload", PKG)
    await _settle()
    assert not type(second).tick.running
    assert _events()[-1] == ("drop", "v2")
    assert not any(m.startswith(f"{PKG}.") for m in sys.modules)


async def test_failed_reload_restarts_the_restored_extension(ext_package):
    bot = interactions.Client()
    ctx = _Ctx(bot)
    await botops.run_extension_op(ctx, "load", PKG)
    bot._ready.set()

    ext_package.write_text("broken")
    await botops.run_extension_op(ctx, "reload", PKG)  # the library reverts
    await _settle()

    restored = bot.ext["ProbeExtension"]
    assert restored.value == "v1"
    assert type(restored).tick.running  # not left loaded with nothing running
    assert _events() == [("drop", "v1"), ("startup", "v1"), ("ready", "v1")]
    type(restored).tick.stop()


def test_purge_only_touches_the_extension_submodules(monkeypatch):
    for name in ("pkg_a", "pkg_a.sub", "pkg_a.sub.deep", "pkg_ab", "pkg_ab.sub"):
        monkeypatch.setitem(sys.modules, name, object())
    assert sorted(botops.purge_submodules("pkg_a")) == ["pkg_a.sub", "pkg_a.sub.deep"]
    assert "pkg_a" in sys.modules and "pkg_ab.sub" in sys.modules
