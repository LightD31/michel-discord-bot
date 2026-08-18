"""Tests for reusing one paginator across refreshes.

A ``Paginator`` derives every button ``custom_id`` from a per-instance uuid, so
a refresher that rebuilt its paginator each cycle handed the message different
components every time — no change check could ever see the render as unchanged.
Rebuilding also registers another component callback, and the client rejects
duplicate listeners, so the old ones can only pile up.
"""

import pytest
from interactions import Embed

from src.discord_ext.paginator import CustomPaginator


class FakeClient:
    """Just enough client for ``Paginator.__attrs_post_init__``."""

    def __init__(self) -> None:
        self.callbacks: list[str] = []

    def add_component_callback(self, command) -> None:
        for listener in command.listeners:
            if listener in self.callbacks:
                raise ValueError(
                    f"Duplicate Component! Multiple component callbacks for `{listener}`"
                )
            self.callbacks.append(listener)


def _pages(n: int, *, label: str = "p") -> list[Embed]:
    return [Embed(title=f"{label}{i}") for i in range(n)]


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


def test_rebuilding_changes_every_custom_id(client):
    """The behaviour being fixed — kept as the baseline the reuse path avoids."""
    first = CustomPaginator.create_from_embeds(client, *_pages(3))
    second = CustomPaginator.create_from_embeds(client, *_pages(3))

    ids_first = {c["custom_id"] for row in first.to_dict()["components"] for c in row["components"]}
    ids_second = {
        c["custom_id"] for row in second.to_dict()["components"] for c in row["components"]
    }
    assert ids_first.isdisjoint(ids_second)


def test_rebuilding_leaks_a_callback_per_cycle(client):
    for _ in range(3):
        CustomPaginator.create_from_embeds(client, *_pages(2))
    # Six listeners per paginator, none ever removed.
    assert len(client.callbacks) == 18


def test_set_pages_keeps_components_identical(client):
    paginator = CustomPaginator.create_from_embeds(client, *_pages(3))
    before = paginator.to_dict()["components"]

    paginator.set_pages(_pages(3, label="refreshed"), page_index=0)
    after = paginator.to_dict()["components"]

    assert before == after
    assert len(client.callbacks) == 6  # still one registration


def test_set_pages_swaps_the_rendered_content(client):
    paginator = CustomPaginator.create_from_embeds(client, *_pages(2))
    paginator.set_pages(_pages(2, label="refreshed"), page_index=0)

    assert paginator.to_dict()["embeds"][0]["title"] == "refreshed0"


def test_set_pages_clamps_a_now_out_of_range_index(client):
    paginator = CustomPaginator.create_from_embeds(client, *_pages(5))
    paginator.page_index = 4

    paginator.set_pages(_pages(2))

    assert paginator.page_index == 1
    assert paginator.to_dict()["embeds"][0]["title"] == "p1"


def test_set_pages_preserves_the_index_when_not_given(client):
    paginator = CustomPaginator.create_from_embeds(client, *_pages(5))
    paginator.page_index = 3

    paginator.set_pages(_pages(5, label="refreshed"))

    assert paginator.page_index == 3


def test_set_pages_honours_an_explicit_index(client):
    paginator = CustomPaginator.create_from_embeds(client, *_pages(5))
    paginator.page_index = 3

    paginator.set_pages(_pages(5), page_index=0)

    assert paginator.page_index == 0
