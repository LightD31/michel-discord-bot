"""Dashboard actions that mutate the running bot client, run on the bot loop.

The Web UI serves requests from a daemon thread with its own uvicorn event
loop, so calling ``bot.reload_extension(...)`` straight from a route body runs
it on the wrong loop. interactions.py reacts to a load *or* an unload by firing
``asyncio.create_task(self.synchronise_interactions())``, which binds the task
to whatever loop is currently running — the uvicorn one — while the client's
aiohttp session belongs to the bot loop. The task then dies with::

    RuntimeError: Timeout context manager should be used inside a task

and, since nobody awaits it, the only trace is a stray "Task exception was
never retrieved" traceback while the commands are silently never synced.
Everything else an extension does at load time (``Task.start()``,
``add_interaction``) lands on the wrong loop the same way.

So every route that loads, unloads, or reloads an extension goes through the
helpers here: the operation runs on the bot loop, the library's implicit
fire-and-forget sync is suppressed, and the caller decides whether to follow up
with a single awaited :func:`sync_commands`.

The library's reload is also incomplete for this bot's extensions, so
:func:`run_extension_op` finishes the job around it:

- ``unload_extension`` drops only the extension's own module from
  ``sys.modules``. A package's submodules (``_common``, the mixins) stay
  cached, so the reload re-imports none of them: the config snapshot taken in
  ``_common`` at import goes stale, and every command, component and task
  defined on a cached mixin keeps its callback bound to the *unloaded*
  instance (``wrap_partial`` skips anything already wrapped). The submodules
  are purged first so the reload re-imports the whole package.
- ``Extension.drop()`` doesn't stop ``Task`` loops: the old instance's tasks
  keep firing, with the old config, next to the new ones. They are stopped,
  and an extension holding other live resources (a websocket, a socket.io
  client) releases them in an optional ``async def async_drop(self)``.
- ``Startup`` and ``Ready`` fire once per process, so an extension loaded or
  reloaded afterwards never runs the ``on_startup`` / ``on_ready`` listeners
  that start its tasks. They are run for the freshly loaded instance, the way
  the gateway would have at boot.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from typing import TYPE_CHECKING, Any, Literal

from interactions import Task, events

from src.core import logging as logutil
from src.core.tasks import spawn

if TYPE_CHECKING:
    from src.webui.context import WebUIContext

logger = logutil.init_logger("webui.botops")

ExtensionAction = Literal["load", "unload", "reload"]

# Loading an extension runs its module import and ``setup()`` on the bot loop;
# generous, but bounded so a wedged loop doesn't hang the dashboard request.
EXTENSION_OP_TIMEOUT_SECONDS = 60.0
# A global sync walks every scope the bot has commands in.
GLOBAL_SYNC_TIMEOUT_SECONDS = 120.0
# A guild-scoped sync is a couple of Discord API calls.
GUILD_SYNC_TIMEOUT_SECONDS = 30.0
# Budget for one extension's ``async_drop()`` (closing a websocket…).
ASYNC_DROP_TIMEOUT_SECONDS = 15.0

# Listeners that start an extension's background work, in the order the
# gateway fires them at boot.
_BOOT_EVENTS: tuple[tuple[str, type[events.BaseEvent]], ...] = (
    ("startup", events.Startup),
    ("ready", events.Ready),
)


def _belongs_to(module_name: str, ext_path: str) -> bool:
    return module_name == ext_path or module_name.startswith(f"{ext_path}.")


def extensions_of(bot: Any, ext_path: str) -> list[Any]:
    """Extension instances loaded from ``ext_path`` (the module or a submodule)."""
    return [
        ext
        for ext in list(getattr(bot, "ext", {}).values())
        if _belongs_to(getattr(ext, "extension_name", ""), ext_path)
    ]


def purge_submodules(ext_path: str) -> list[str]:
    """Drop ``ext_path``'s submodules from ``sys.modules`` so a reload re-imports them."""
    stale = [name for name in sys.modules if name.startswith(f"{ext_path}.")]
    for name in stale:
        del sys.modules[name]
    return stale


def stop_tasks(ext: Any) -> list[str]:
    """Stop every ``Task`` defined on ``ext`` (its class and mixins)."""
    stopped = []
    for name, task in inspect.getmembers(type(ext), lambda v: isinstance(v, Task)):
        if task.running:
            stopped.append(name)
        task.stop()
    return stopped


async def teardown(bot: Any, ext_path: str) -> None:
    """Release what ``Extension.drop()`` leaves behind: task loops and live connections."""
    for ext in extensions_of(bot, ext_path):
        stopped = stop_tasks(ext)
        if stopped:
            logger.info("Stopped %s tasks before unloading: %s", ext_path, ", ".join(stopped))
        async_drop = getattr(ext, "async_drop", None)
        if async_drop is None:
            continue
        try:
            await asyncio.wait_for(async_drop(), timeout=ASYNC_DROP_TIMEOUT_SECONDS)
        except Exception as e:  # noqa: BLE001 — never block the unload on cleanup
            logger.error("async_drop() failed for %s: %s", ext_path, e)


async def _run_listener(listener: Any, event_cls: type[events.BaseEvent], bot: Any) -> None:
    if listener.pass_event_object:
        event = event_cls()
        event.bot = bot
        await listener(event)
    else:
        await listener()


async def _boot_sequence(bot: Any, ext: Any, ext_path: str) -> None:
    for event_name, event_cls in _BOOT_EVENTS:
        for listener in list(getattr(ext, "_listeners", [])):
            if listener.event != event_name:
                continue
            try:
                await _run_listener(listener, event_cls, bot)
            except Exception as e:  # noqa: BLE001 — same as a failing boot listener
                logger.error("%s listener of %s failed after load: %s", event_name, ext_path, e)


def boot(bot: Any, ext_path: str) -> None:
    """Run the Startup/Ready listeners of freshly loaded ``ext_path`` instances.

    Only when the client is already running: at boot the gateway fires those
    events itself. Scheduled in the background, as at boot — a listener that
    hydrates state for a minute must not hold up the dashboard request.
    """
    if not getattr(bot, "is_ready", False):
        return
    for ext in extensions_of(bot, ext_path):
        spawn(_boot_sequence(bot, ext, ext_path), name=f"boot:{ext_path}", log=logger)


async def run_extension_op(ctx: WebUIContext, action: ExtensionAction, ext_path: str) -> None:
    """Run ``bot.<action>_extension(ext_path)`` on the bot loop, lifecycle included.

    Around the library call (see the module docstring for why each is needed):
    an unload or reload first stops the outgoing instance's tasks, awaits its
    ``async_drop()`` and purges the package's cached submodules; a load or
    reload then runs the new instance's Startup/Ready listeners. That last step
    also runs when a reload failed and the library restored the previous
    module, so the extension is never left loaded with nothing running.

    The client's implicit per-call command sync is disabled for the duration:
    it would schedule an unawaited task whose failures nobody sees, and a
    reload-everything pass would queue one per extension. Callers that need
    Discord to learn about the new command set call :func:`sync_commands` once,
    afterwards.

    Propagates whatever the operation raises; callers translate it for the UI.
    """
    bot = ctx.bot

    async def _op() -> None:
        if action in ("unload", "reload"):
            await teardown(bot, ext_path)
            purge_submodules(ext_path)
        previous_sync_ext = bot.sync_ext
        bot.sync_ext = False
        try:
            getattr(bot, f"{action}_extension")(ext_path)
        finally:
            bot.sync_ext = previous_sync_ext
            if action in ("load", "reload"):
                boot(bot, ext_path)

    await ctx.run_on_bot_loop(_op, timeout=EXTENSION_OP_TIMEOUT_SECONDS)


async def sync_commands(
    ctx: WebUIContext,
    *,
    scopes: list[int] | None = None,
    timeout: float | None = None,
) -> None:
    """Push the client's current commands to Discord, on the bot loop.

    *scopes* limits the sync to those guild ids; ``None`` syncs every scope.
    """
    bot = ctx.bot
    if timeout is None:
        timeout = GUILD_SYNC_TIMEOUT_SECONDS if scopes else GLOBAL_SYNC_TIMEOUT_SECONDS

    def _sync():
        if scopes is None:
            return bot.synchronise_interactions()
        return bot.synchronise_interactions(scopes=scopes)

    await ctx.run_on_bot_loop(_sync, timeout=timeout)
