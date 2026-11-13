"""The birthday role is re-checked each time the bot hands it out."""

from discord_fakes import FakeGuild, FakeMember, FakeRole, bare_extension
from interactions import Permissions

from extensions.birthday import BirthdayExtension

GUILD = 1
ROLE = 301


def _setup(store_config, role: FakeRole):
    store_config(
        {
            "servers": {
                str(GUILD): {"moduleBirthday": {"enabled": True, "birthdayRoleId": str(ROLE)}}
            }
        }
    )
    guild = FakeGuild(GUILD)
    guild.add_role(role)
    member = guild.add_member(FakeMember(7, "bob"))
    return bare_extension(BirthdayExtension), guild, member


async def test_the_birthday_role_is_given_and_taken_back(store_config):
    ext, guild, member = _setup(store_config, FakeRole(ROLE, position=2))
    await ext._toggle_birthday_role(guild, member, GUILD, add=True)
    assert [r.id for r in member.roles] == [ROLE]
    await ext._toggle_birthday_role(guild, member, GUILD, add=False)
    assert member.roles == []


async def test_a_birthday_role_that_became_staff_is_not_given(store_config):
    role = FakeRole(ROLE, position=2, permissions=Permissions.MANAGE_MESSAGES)
    ext, guild, member = _setup(store_config, role)
    await ext._toggle_birthday_role(guild, member, GUILD, add=True)
    assert member.roles == []


async def test_a_staff_birthday_role_is_still_taken_back(store_config):
    role = FakeRole(ROLE, position=2, permissions=Permissions.MANAGE_MESSAGES)
    ext, guild, member = _setup(store_config, role)
    member.roles = [role]
    await ext._toggle_birthday_role(guild, member, GUILD, add=False)
    assert member.roles == []
