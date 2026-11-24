"""``/menu-roles creer``: pick the roles in Discord, see the preview, publish."""

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, FakeRole, bare_extension, run
from interactions import Permissions

from extensions.reactionroles import ReactionRolesExtension
from extensions.reactionroles.commands import DRAFTS

STAFF = Permissions.MANAGE_ROLES | Permissions.SEND_MESSAGES | Permissions.VIEW_CHANNEL


class Menus:
    def __init__(self):
        self.menus = {}

    async def add(self, menu):
        menu_id = f"{len(self.menus) + 1:024x}"
        self.menus[menu_id] = menu.model_copy(update={"id": menu_id})
        return menu_id

    async def get(self, menu_id):
        return self.menus.get(menu_id)

    async def update(self, menu_id, **fields):
        self.menus[menu_id] = self.menus[menu_id].model_copy(update=fields)

    async def delete(self, menu_id):
        self.menus.pop(menu_id, None)


@pytest.fixture
def world(store_config):
    store_config({"servers": {"1": {"moduleReactionRoles": {"enabled": True}}}})
    guild = FakeGuild(1)
    red = guild.add_role(FakeRole(301, "Rouge", position=2))
    blue = guild.add_role(FakeRole(302, "Bleu", position=3))
    admin = guild.add_role(
        FakeRole(303, "Admins", position=4, permissions=Permissions.ADMINISTRATOR)
    )
    author = guild.add_member(
        FakeMember(7, "alice", roles=[FakeRole(310, position=10)], permissions=STAFF)
    )
    channel = guild.add_channel(FakeChannel(10))
    repo = Menus()
    ctx = FakeCtx(author, channel)
    ext = bare_extension(ReactionRolesExtension, ctx.bot, repository=lambda guild_id: repo)
    yield ext, ctx, repo, (red, blue, admin)
    DRAFTS.clear()


def draft_of(ctx) -> str:
    return ctx.replies[-1]["components"][0].components[0].custom_id.split(":")[1]


async def click(ext, ctx, draft_id, action, values=None, member=None):
    clicked = FakeCtx(member or ctx.author, ctx.channel, bot=ctx.bot)
    clicked.custom_id = f"rrdraft:{draft_id}:{action}"
    clicked.values = values or []
    await run(ReactionRolesExtension.on_draft, ext, clicked)
    return clicked


async def test_roles_are_picked_previewed_then_published_as_a_drop_down(world):
    ext, ctx, repo, (red, blue, _) = world
    await run(
        ReactionRolesExtension.rolemenu_create,
        ext,
        ctx,
        ctx.channel,
        "Couleurs",
        style="select",
        max_roles=1,
    )
    reply = ctx.replies[-1]
    assert reply["ephemeral"] and reply["components"][1].components[0].disabled  # Publier
    draft_id = draft_of(ctx)

    picked = await click(ext, ctx, draft_id, "roles", [red, blue])
    view = picked.origin_edits[-1]
    assert "<@&301> — Rouge" in view["embeds"][0].description
    assert "Un seul rôle à la fois" in view["embeds"][0].description
    assert not view["components"][1].components[0].disabled

    published = await click(ext, ctx, draft_id, "publish")
    assert published.origin_edits[-1]["content"] == "✅ Menu publié dans <#10>."
    (menu,) = repo.menus.values()
    assert (menu.style, menu.max_roles) == ("select", 1)
    assert [e.label for e in menu.entries] == ["Rouge", "Bleu"]
    (post,) = ctx.channel.sent
    select = post.sent_with["components"][0].components[0]
    assert select.custom_id == f"rrolesel:{menu.id}" and select.max_values == 1
    assert menu.message_id == str(post.id) and draft_id not in DRAFTS


async def test_a_staff_role_is_refused_in_the_preview(world):
    ext, ctx, repo, (red, _, admin) = world
    await run(ReactionRolesExtension.rolemenu_create, ext, ctx, ctx.channel, "Rôles")
    draft_id = draft_of(ctx)
    picked = await click(ext, ctx, draft_id, "roles", [red, admin])
    view = picked.origin_edits[-1]
    assert view["content"].startswith("❌ Entrée 2")
    assert view["components"][1].components[0].disabled
    await click(ext, ctx, draft_id, "publish")
    assert repo.menus == {} and ctx.channel.sent == []


async def test_only_its_author_uses_a_draft(world):
    ext, ctx, repo, (red, _, _) = world
    await run(ReactionRolesExtension.rolemenu_create, ext, ctx, ctx.channel, "Rôles")
    other = ctx.channel.guild.add_member(FakeMember(8, "eve", permissions=STAFF))
    refused = await click(ext, ctx, draft_of(ctx), "roles", [red], member=other)
    assert refused.last.startswith("🔒")


async def test_an_unknown_draft_says_to_start_again(world):
    ext, ctx, _, _ = world
    late = await click(ext, ctx, "0" * 12, "publish")
    assert "expiré" in late.origin_edits[-1]["content"]


async def test_a_member_picks_in_the_drop_down_and_clears(world):
    from features.reactionroles import RoleMenu, RoleMenuEntry
    from src.core import clock

    ext, ctx, repo, (red, blue, _) = world
    menu_id = await repo.add(
        RoleMenu(
            guild_id="1",
            channel_id="10",
            title="Couleurs",
            entries=[
                RoleMenuEntry(role_id="301", label="Rouge"),
                RoleMenuEntry(role_id="302", label="Bleu"),
            ],
            style="select",
            max_roles=1,
            created_by="7",
            created_at=clock.now(),
        )
    )
    bob = ctx.channel.guild.add_member(FakeMember(9, "bob", roles=[red]))
    pick = FakeCtx(bob, ctx.channel, bot=ctx.bot, custom_id=f"rrolesel:{menu_id}")
    pick.values = ["1"]
    await run(ReactionRolesExtension.on_role_select, ext, pick)
    assert [r.id for r in bob.roles] == [302]
    assert pick.last == "Rôle <@&302> ajouté ✅\nRôle <@&301> retiré ❌"
    clear = FakeCtx(bob, ctx.channel, bot=ctx.bot, custom_id=f"rroleclr:{menu_id}")
    await run(ReactionRolesExtension.on_role_clear, ext, clear)
    assert bob.roles == []


async def test_one_role_at_a_time_with_buttons_swaps_it(world):
    from features.reactionroles import RoleMenu, RoleMenuEntry
    from src.core import clock

    ext, ctx, repo, (red, blue, _) = world
    menu_id = await repo.add(
        RoleMenu(
            guild_id="1",
            channel_id="10",
            title="Couleurs",
            entries=[
                RoleMenuEntry(role_id="301", emoji="🔴", label="Rouge"),
                RoleMenuEntry(role_id="302", emoji="🔵", label="Bleu"),
            ],
            max_roles=1,
            created_by="7",
            created_at=clock.now(),
        )
    )
    bob = ctx.channel.guild.add_member(FakeMember(9, "bob", roles=[red]))
    button = FakeCtx(bob, ctx.channel, bot=ctx.bot, custom_id=f"rrole:{menu_id}:1")
    await run(ReactionRolesExtension.on_role_button, ext, button)
    assert [r.id for r in bob.roles] == [302]
