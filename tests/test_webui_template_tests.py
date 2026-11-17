"""Template tests: rendered like the bot does, DMed to the admin, warnings said (F11)."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from interactions.client.errors import Forbidden

import src.webui.routes.template_tests as routes
from src.webui import audit, template_tests
from src.webui.template_tests import TESTS, TemplateRefused, render_test, sample_values

VALUES = sample_values("7", "alice")


def test_welcome_is_filled_as_the_bot_fills_it():
    test = TESTS["moduleWelcome"]["welcomeMessageList"]
    rendered = render_test(test, ["Bienvenue {mention} !", "Salut {pseudo}"], VALUES)
    assert rendered.texts == ["Bienvenue <@7> !", "Salut {pseudo}"]  # unknown kept, as sent
    (warning,) = rendered.warnings
    assert "{pseudo}" in warning and "tel quel" in warning


def test_a_youtube_template_with_an_unknown_name_falls_back_to_the_link():
    test = TESTS["moduleYoutube"]["youtubeNotificationTemplate"]
    rendered = render_test(test, ["Nouvelle vidéo de {label} : {titre}"], VALUES)
    assert rendered.texts == [f"https://www.youtube.com/watch?v={template_tests.SAMPLE_VIDEO_ID}"]
    assert "simple lien" in rendered.warnings[0]
    ok = render_test(test, ["{label} : https://youtu.be/{video_id}"], VALUES)
    assert ok.texts == [f"Chaîne de test : https://youtu.be/{template_tests.SAMPLE_VIDEO_ID}"]
    assert ok.warnings == []


def test_everyone_is_flagged_and_a_refused_template_raises():
    test = TESTS["moduleXp"]["levelUpMessageList"]
    rendered = render_test(test, ["@everyone {mention} passe niveau {lvl} !"], VALUES)
    assert rendered.texts == ["@everyone <@7> passe niveau 5 !"]
    assert "tout le serveur" in rendered.warnings[0]
    with pytest.raises(TemplateRefused, match="Message 1"):
        render_test(test, ["{lvl:>99999}"], VALUES)


def test_only_the_first_texts_are_tested():
    test = TESTS["moduleCustomCommands"]["commands"]
    rendered = render_test(test, [f"r{i}" for i in range(15)], VALUES)
    assert len(rendered.texts) == template_tests.MAX_TEXTS
    assert "premiers" in rendered.warnings[-1]


def test_the_catalogue_names_each_fields_variables():
    catalogue = template_tests.catalogue()
    assert catalogue["moduleBirthday"]["birthdayMessageList"]["variables"] == ["mention", "age"]


class User:
    def __init__(self, closed=False):
        self.closed, self.sent = closed, []

    async def send(self, content, **kwargs):
        if self.closed:
            raise Forbidden(SimpleNamespace(status=403, reason="Forbidden"), response_data={})
        self.sent.append((content, kwargs))


class Ctx:
    def __init__(self, user):
        self.user = user

        async def fetch_user(uid):
            return user

        self.bot = SimpleNamespace(
            get_guild=lambda gid: SimpleNamespace(name="Le serveur"), fetch_user=fetch_user
        )

    async def require_guild_admin(self, request, guild_id):  # noqa: ARG002
        return SimpleNamespace(username="alice", user_id="7")

    def require_bot_loop(self):
        return None

    async def call_bot(self, fn, *args, what, **kwargs):  # noqa: ARG002
        return await fn(*args, **kwargs)


def post(ctx, body):
    router = routes.create_router(ctx)
    endpoint = next(r.endpoint for r in router.routes if r.path.endswith("template-test"))
    return asyncio.run(endpoint(None, "1", routes.TemplateTestBody(**body)))


@pytest.fixture(autouse=True)
def no_audit(monkeypatch):
    async def record(*args, **kwargs):
        return None

    monkeypatch.setattr(audit, "record", record)


def test_the_admin_gets_one_dm_per_text():
    user = User()
    body = {"module": "moduleXp", "field": "levelUpMessageList", "texts": ["GG {mention}", " "]}
    result = json.loads(post(Ctx(user), body).body)
    assert result["sent"] == 1 and result["previews"] == ["GG <@7>"]
    ((content, kwargs),) = user.sent
    assert content.startswith("🧪 Test : Passage de niveau — serveur « Le serveur »")
    assert content.endswith("GG <@7>")
    assert kwargs["allowed_mentions"] is routes.NO_PINGS


def test_closed_dms_and_unknown_fields_are_reported():
    with pytest.raises(HTTPException) as closed:
        post(
            Ctx(User(closed=True)),
            {"module": "moduleXp", "field": "levelUpMessageList", "texts": ["x"]},
        )
    assert closed.value.status_code == 409
    with pytest.raises(HTTPException) as unknown:
        post(Ctx(User()), {"module": "moduleXp", "field": "xpChannelId", "texts": ["x"]})
    assert unknown.value.status_code == 404
    with pytest.raises(HTTPException) as refused:
        post(
            Ctx(User()),
            {"module": "moduleXp", "field": "levelUpMessageList", "texts": ["{mention.id}"]},
        )
    assert refused.value.status_code == 422
