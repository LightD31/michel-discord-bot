"""Greeting a newcomer (A8) against a real MongoDB: once each, never oneself, for 7 days."""

import asyncio
import importlib
import sys
from datetime import timedelta

import pytest
from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, FakeMessage, bare_extension

from features.userdata import UserDataRepository
from features.welcome import GreetingRepository
from features.welcome.greetings import KEEP_FOR
from src.core import clock

pytestmark = pytest.mark.mongo

NEWCOMER = "111111111111111111"
LEA = "222222222222222222"
HUGO = "333333333333333333"


async def test_each_member_greets_once_and_never_themselves(mongo):
    repo = GreetingRepository(mongo.guild_id)
    await repo.ensure_indexes()
    now = clock.now()
    await repo.open("900", NEWCOMER, now)
    await repo.open("900", LEA, now)  # a second open changes nothing

    # Two clicks at once from Léa, one from Hugo: each counted once.
    results = await asyncio.gather(
        repo.greet("900", LEA, now), repo.greet("900", LEA, now), repo.greet("900", HUGO, now)
    )
    refusals = [r for r in results if isinstance(r, str)]
    assert refusals == ["already"]
    doc = await mongo.guild_collection("welcome_greetings").find_one({"_id": "900"})
    assert doc["member_id"] == NEWCOMER and sorted(doc["greeters"]) == [LEA, HUGO]

    assert await repo.greet("900", NEWCOMER, now) == "self"
    assert await repo.greet("901", LEA, now) == "expired"  # no such welcome
    assert await repo.greet("900", "444444444444444444", now + KEEP_FOR) == "expired"
    assert len((await mongo.guild_collection("welcome_greetings").find_one())["greeters"]) == 2


async def test_the_button_writes_the_names_under_the_welcome(mongo, store_config):
    store_config({"servers": {mongo.guild_id: {"moduleWelcome": {"enabled": True}}}})
    sys.modules.pop("extensions.welcome", None)
    module = importlib.import_module("extensions.welcome")
    guild = FakeGuild(int(mongo.guild_id), "Les Copains")
    channel = guild.add_channel(FakeChannel(7, "bienvenue"))
    welcome = FakeMessage(900, content="Bienvenue <@111111111111111111> !", channel=channel)
    await GreetingRepository(mongo.guild_id).open("900", NEWCOMER, clock.now())
    ext = bare_extension(module.WelcomeExtension, _greet_lock=asyncio.Lock())

    async def click(uid: str, name: str) -> FakeCtx:
        member = guild.get_member(uid) or guild.add_member(FakeMember(int(uid), name))
        ctx = FakeCtx(member, channel, message=welcome)
        ctx.guild_id = mongo.guild_id  # the test guild's id keeps its leading zero
        await module.WelcomeExtension.on_greet.callback(ext, ctx)
        if ctx.origin_edits:
            welcome.content = ctx.origin_edits[-1]["content"]
        return ctx

    await click(LEA, "Léa")
    await click(HUGO, "Hugo")
    assert welcome.content == (
        "Bienvenue <@111111111111111111> !\n\n-# 👋 Léa et Hugo te souhaitent la bienvenue"
    )
    again = await click(LEA, "Léa")
    assert again.replies[0]["ephemeral"] and "déjà" in again.last
    itself = await click(NEWCOMER, "Bob")
    assert itself.replies[0]["ephemeral"] and "C'est toi" in itself.last
    sys.modules.pop("extensions.welcome", None)


async def test_mes_donnees_covers_both_sides_of_a_greeting(mongo):
    g = mongo.guild_collection("welcome_greetings")
    expires = clock.now() + timedelta(days=3)
    await g.insert_many(
        [
            {"_id": "900", "member_id": NEWCOMER, "greeters": [LEA, HUGO], "expires_at": expires},
            {"_id": "901", "member_id": HUGO, "greeters": [NEWCOMER, LEA], "expires_at": expires},
        ]
    )
    data = await UserDataRepository(LEA).export([mongo.guild_id])
    mine = data["guilds"][mongo.guild_id]["welcome_greetings"]
    # The welcomes Léa greeted, never who else did or who arrived.
    assert sorted(d["_id"] for d in mine) == ["900", "901"]
    assert all(set(d) == {"_id", "expires_at"} for d in mine)

    await UserDataRepository(NEWCOMER).forget([mongo.guild_id])
    assert await g.find_one({"_id": "900"}) is None  # their own welcome
    assert (await g.find_one({"_id": "901"}))["greeters"] == [LEA]  # out of Hugo's count
