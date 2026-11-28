"""The leaderboard's paginator as a Components V2 card (P26)."""

from types import SimpleNamespace

from interactions import MessageFlags
from interactions.models.discord.components import process_components

from src.discord_ext.look import card
from src.discord_ext.paginator import CustomPaginator


def paginator(v2: bool) -> CustomPaginator:
    pages = [card("moduleXp", title=f"Classement (page {i})") for i in (1, 2)]
    found = CustomPaginator.create_from_embeds(
        SimpleNamespace(add_component_callback=lambda cb: None), *pages
    )
    found.v2 = v2
    return found


def test_an_embed_paginator_keeps_its_embeds():
    data = paginator(False).payload()
    assert "embeds" in data and "flags" not in data


def test_a_v2_paginator_shows_its_page_as_a_card_with_its_buttons_under_it():
    data = paginator(True).payload()
    assert data["flags"] == MessageFlags.IS_COMPONENTS_V2 and "embeds" not in data
    parts = process_components(data["components"])
    assert parts[0]["type"] == 17  # the page's card
    assert "Classement (page 1)" in parts[0]["components"][0]["content"]
    assert all(part["type"] == 1 for part in parts[1:]) and parts[1:]  # rows of buttons


async def test_a_button_on_a_v2_paginator_edits_with_what_edit_origin_takes():
    """``edit_origin`` has no ``flags``: passing the payload whole raised (MICHEL-DISCORD-BOT-3)."""
    import inspect

    from interactions import ComponentContext

    accepted = inspect.signature(ComponentContext.edit_origin)
    edits = []

    class Ctx:
        custom_id = "uuid|next"

        async def edit_origin(self, **kwargs):
            accepted.bind(None, **kwargs)  # a TypeError, as in production, if a kwarg is unknown
            edits.append(kwargs)

    found = paginator(True)
    await found._on_button(Ctx())

    assert found.page_index == 1
    parts = process_components(edits[0]["components"])
    assert "Classement (page 2)" in parts[0]["components"][0]["content"]
