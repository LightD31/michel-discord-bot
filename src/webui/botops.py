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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from src.core import logging as logutil

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


async def run_extension_op(ctx: WebUIContext, action: ExtensionAction, ext_path: str) -> None:
    """Run ``bot.<action>_extension(ext_path)`` on the bot loop.

    The client's implicit per-call command sync is disabled for the duration:
    it would schedule an unawaited task whose failures nobody sees, and a
    reload-everything pass would queue one per extension. Callers that need
    Discord to learn about the new command set call :func:`sync_commands` once,
    afterwards.

    Propagates whatever the operation raises; callers translate it for the UI.
    """
    bot = ctx.bot

    def _op() -> None:
        previous_sync_ext = bot.sync_ext
        bot.sync_ext = False
        try:
            getattr(bot, f"{action}_extension")(ext_path)
        finally:
            bot.sync_ext = previous_sync_ext

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
