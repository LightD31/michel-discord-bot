"""Answers members when a command, button or modal fails.

Replaces interactions.py's default error listeners (English messages, and
silence on an unexpected exception) with an ephemeral French reply. An
unexpected error is logged with its traceback and a short reference that the
member is shown, so a report can be matched to the log line. See
``src/discord_ext/errors.py`` for the messages.
"""

from __future__ import annotations

from typing import Any

from interactions import Client, Extension, listen
from interactions.api.events import CommandError, ComponentError, ModalError

from src.core import logging as logutil
from src.discord_ext.errors import is_expected, new_reference, user_message

logger = logutil.init_logger("extensions.errors")


class ErrorsExtension(Extension):
    """Global handler for interaction errors."""

    MODULE_KEYS = ()

    def __init__(self, bot: Client) -> None:
        self.bot = bot

    async def _answer(self, ctx: Any, error: BaseException, source: str) -> None:
        reference = new_reference()
        if not is_expected(error):
            logger.error("%s failed [ref %s]: %s", source, reference, error, exc_info=error)
        try:
            await ctx.send(user_message(error, reference), ephemeral=True)
        except Exception as e:  # noqa: BLE001 — the interaction may have expired
            logger.debug("Could not tell the member about [ref %s]: %s", reference, e)

    @listen(CommandError, disable_default_listeners=True)
    async def on_command_error(self, event: CommandError) -> None:
        await self._answer(event.ctx, event.error, f"/{event.ctx.invoke_target}")

    @listen(ComponentError, disable_default_listeners=True)
    async def on_component_error(self, event: ComponentError) -> None:
        await self._answer(event.ctx, event.error, f"component {event.ctx.custom_id}")

    @listen(ModalError, disable_default_listeners=True)
    async def on_modal_error(self, event: ModalError) -> None:
        await self._answer(event.ctx, event.error, f"modal {event.ctx.custom_id}")


def setup(bot: Client) -> None:
    ErrorsExtension(bot)
