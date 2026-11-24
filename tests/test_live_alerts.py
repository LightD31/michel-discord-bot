"""The "🔔 M'avertir" button under a live or video notification."""

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, FakeRole
from interactions import Permissions

import extensions.twitch._common  # noqa: F401 — registers moduleTwitch
import extensions.youtube  # noqa: F401 — registers moduleYoutube
from features.twitch import live_text
from src.discord_ext.live_alerts import configured_alert_roles, toggle_alert_role
from src.webui import schemas


def click(custom_id: str) -> FakeCtx:
    guild = FakeGuild(1, "Les Copains")
    guild.add_role(FakeRole(555, "Alertes live"))
    guild.add_role(FakeRole(556, "Modo", permissions=Permissions.KICK_MEMBERS))
    member = guild.add_member(FakeMember(5, "Hugo"))
    ctx = FakeCtx(member, guild.add_channel(FakeChannel(7)), custom_id=custom_id)
    return ctx


async def test_the_bell_gives_the_role_then_takes_it_back():
    ctx = click("twitch:alerte:555")
    await toggle_alert_role(ctx, {"555"}, what="des lives")
    assert ctx.author.has_role(555)
    assert ctx.replies[-1]["ephemeral"] and ctx.last.startswith("🔔 C'est noté")
    await toggle_alert_role(ctx, {"555"}, what="des lives")
    assert not ctx.author.has_role(555)
    assert ctx.last == "🔕 C'est noté : tu ne seras plus averti des lives."


async def test_a_role_no_longer_configured_or_a_staff_role_is_refused():
    old = click("twitch:alerte:555")
    await toggle_alert_role(old, {"557"}, what="des lives")
    assert not old.author.has_role(555) and "ne sert plus" in old.last
    staff = click("youtube:alerte:556")
    await toggle_alert_role(staff, {"556"}, what="des nouvelles vidéos")
    assert not staff.author.has_role(556)
    assert staff.last.startswith("🔒 Je ne peux pas te donner ce rôle : ce rôle donne")


def test_the_configured_roles_are_read_from_each_entry():
    entries = {"a": {"r": "555"}, "b": {"r": ""}, "c": {"r": "x"}, "d": "junk", "e": {"r": 556}}
    assert configured_alert_roles(entries, "r") == {"555", "556"}
    assert configured_alert_roles(None, "r") == set()


@pytest.mark.parametrize(
    ("template", "role", "text"),
    [
        ("{streamer} joue à {jeu} : {titre} {lien}", "5", "<@&5> Zera joue à Celeste : T L"),
        ("", "5", "<@&5>"),
        ("  ", None, None),
        ("{streamer} {inconnu}", None, "Zera {inconnu}"),
    ],
)
def test_the_live_text_leads_with_the_role(template, role, text):
    assert live_text(template, role, streamer="Zera", titre="T", jeu="Celeste", lien="L") == text


def test_a_mention_role_is_checked_both_as_pinged_and_handed_out():
    twitch = schemas.module_model("moduleTwitch")
    youtube = schemas.module_model("moduleYoutube")
    streamers = {"twitchStreamerList": {"zerator": {"twitchMentionRoleId": "555"}, "x": {}}}
    channels = {"youtubeChannelList": {"@a": {"mentionRoleId": "556"}}}
    assert schemas.role_refs(twitch, streamers) == {("mention", "555"), ("grant", "555")}
    assert schemas.role_refs(youtube, channels) == {("mention", "556"), ("grant", "556")}
