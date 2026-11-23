"""Paginator and reaction-poll formatter.

- :class:`CustomPaginator` — drop-in replacement for ``interactions.ext.paginators.Paginator``
  with a ``callback`` button, select-dropdown jumps, and keep-alive pings.
- :func:`format_poll` — renders a reaction poll's vote counts per option and the
  voters behind each one into a copy of its embed.
"""

from __future__ import annotations

from typing import Any

from interactions import ComponentContext, Embed, Message, MessageFlags
from interactions.ext import paginators

from src.discord_ext.look import embeds_to_v2

# ---------------------------------------------------------------------------
# Custom paginator
# ---------------------------------------------------------------------------


class CustomPaginator(paginators.Paginator):
    """Custom paginator with overridden button handling.

    ``v2 = True`` (P26) renders each page as a Components V2 card, its buttons
    under it: use :meth:`payload` to send or edit, and the buttons do the same.
    """

    v2: bool = False

    def payload(self) -> dict[str, Any]:
        """What to send or edit the message with: :meth:`to_dict`, or its V2 card."""
        data = self.to_dict()
        if not self.v2:
            return data
        rows = list(data.get("components") or [])
        buttons = sum(1 + len(row.get("components", [])) for row in rows)
        pages = [Embed.from_dict(e) for e in data.get("embeds") or []]
        return {
            "components": [*embeds_to_v2(pages, extra=buttons), *rows],
            "flags": MessageFlags.IS_COMPONENTS_V2,
        }

    def set_pages(self, pages: list, *, page_index: int | None = None) -> None:
        """Swap in freshly rendered pages, keeping this paginator's identity.

        Building a new ``Paginator`` mints a new ``_uuid``, which every button
        ``custom_id`` is derived from — so a refresher that rebuilt its
        paginator each cycle handed the message different components every
        time, and no change check could ever find the render unchanged. Worse,
        each construction registers another component callback under that uuid
        and ``Client.add_component_callback`` raises on duplicates, so the old
        ones can only accumulate.

        Reusing one instance per persistent message keeps the custom_ids
        stable, keeps a single live callback, and leaves the buttons wired to
        the pages currently on screen.
        """
        self.pages = list(pages)
        if page_index is not None:
            self.page_index = page_index
        self.page_index = max(0, min(self.page_index, len(self.pages) - 1))

    async def _on_button(self, ctx: ComponentContext, *args, **kwargs) -> Message | None:
        if self._timeout_task:
            self._timeout_task.ping.set()
        match ctx.custom_id.split("|")[1]:
            case "first":
                self.page_index = 0
            case "last":
                self.page_index = len(self.pages) - 1
            case "next":
                if (self.page_index + 1) < len(self.pages):
                    self.page_index += 1
            case "back":
                if self.page_index >= 1:
                    self.page_index -= 1
            case "select":
                self.page_index = int(ctx.values[0])
            case "callback":
                if self.callback is not None:
                    return await self.callback(ctx)

        await ctx.edit_origin(**self.payload())
        return None


__all__ = ["CustomPaginator"]
