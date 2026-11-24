"""``tools/dashboard_demo.py`` keeps up with the code it demonstrates (P3).

The demo is how every later UI change gets its before/after screenshots, so a
schema change that makes its config invalid, or a repository change that breaks
its seed, must fail here rather than on the day someone needs the pictures.
"""

import importlib

import pytest

from src.core import db
from src.webui import schemas
from tools import dashboard_demo as demo


def test_the_demo_config_passes_the_schemas_it_shows():
    for entry in demo.ROOT.joinpath("extensions").iterdir():
        name = entry.name.removesuffix(".py")
        if not name.startswith(("_", ".")) and (entry.suffix == ".py" or entry.is_dir()):
            importlib.import_module(f"extensions.{name}")
    config = demo.demo_config("/tmp/demo", 8765, "mongodb://localhost:27017")
    assert schemas.config_problems(config) == []


def test_the_fake_bot_has_the_servers_the_pages_need():
    bot = demo.FakeBot()
    ga, gb = (bot.get_guild(g) for g in (demo.GA, demo.GB))
    assert ga.get_channel(demo.CH["jeux"]).name == "jeux"
    assert ga.me.top_role.name == "Michel"
    assert {m.display_name for m in ga.members} >= {"Camille", "Hugo", "LightD"}
    assert len(gb.channels) == 1  # the freshly invited one: almost empty


def test_every_session_has_its_pages():
    modules = ["moduleXp"]
    assert demo.pages_for("membre", modules) == [("accueil", "")]
    admin = {name: rest for name, *rest in demo.pages_for("admin", modules)}
    assert admin["serveur"] == [f"#/server/{demo.GA}"] and "config-globale" not in admin
    # What only shows after a click: the page, then the step that makes it show.
    assert admin["xp-historique"] == [f"#/module/{demo.GA}/moduleXp", "history"]
    assert all(rest[1] in demo.STEPS for rest in admin.values() if len(rest) > 1)
    developer = {name: rest for name, *rest in demo.pages_for("developpeur", modules)}
    assert developer["module-moduleXp"] == [f"#/module/{demo.GA}/moduleXp"]


@pytest.mark.mongo
async def test_the_demo_seeds_its_own_databases_and_nothing_else(mongo, monkeypatch):
    # Ids no real guild has (a leading zero), and a global database of the
    # test's own: whatever server MONGO_TEST_URL names, nothing real is dropped.
    monkeypatch.setattr(demo, "GA", f"0{mongo.guild_id}")
    monkeypatch.setattr(demo, "GB", f"00{mongo.guild_id}")
    monkeypatch.setattr(demo, "DEMO_GLOBAL_DB", f"{db.GLOBAL_DB_NAME}_demo")
    client = mongo.manager.client
    names = (f"guild_{demo.GA}", f"guild_{demo.GB}", demo.DEMO_GLOBAL_DB)
    try:
        # A database the demo didn't create is never dropped.
        await client[names[0]]["users"].insert_one({"_id": "someone"})
        with pytest.raises(SystemExit):
            await demo.claim_databases(client)
        assert await client[names[0]]["users"].count_documents({}) == 1
        await client.drop_database(names[0])

        bot = demo.FakeBot()
        await demo.seed(bot, days=2)
        guild = client[names[0]]
        assert await guild[demo.DEMO_MARKER].count_documents({}) == 1
        assert await guild["infractions"].count_documents({}) == 5
        assert await guild["secret_santa_sessions"].count_documents({}) == 1
        # Seeding again replaces what it wrote: the demo owns these now.
        await demo.seed(bot, days=1)
        assert await guild["infractions"].count_documents({}) == 5
    finally:
        for name in names:
            await client.drop_database(name)
