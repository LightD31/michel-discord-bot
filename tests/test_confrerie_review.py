"""Owner review of drafts: permissions, single approval, Notion failures, notices."""

import pytest
from confrerie_fixtures import OEUVRES_SCHEMA

from extensions.confrerie import review
from extensions.confrerie.review import ReviewMixin, review_buttons
from features.confrerie.drafts import (
    SOURCE_DEFI,
    SOURCE_DEMANDE,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    STATUS_REJECTED,
    Draft,
    demande_draft_id,
)

OWNER_ID = 1000
MEMBER_ID = 2000


class FakeRepo:
    def __init__(self, *drafts: Draft) -> None:
        self.docs = {d.id: d.to_doc() for d in drafts}

    def _draft(self, draft_id):
        doc = self.docs.get(draft_id)
        return Draft.from_doc(doc) if doc else None

    async def insert_if_new(self, draft):
        if draft.id in self.docs:
            return False
        self.docs[draft.id] = draft.to_doc()
        return True

    async def get(self, draft_id):
        return self._draft(draft_id)

    async def save(self, draft):
        self.docs[draft.id] = draft.to_doc()

    async def claim(self, draft_id):
        doc = self.docs.get(draft_id)
        if not doc or doc["status"] != STATUS_PENDING:
            return None
        doc["status"] = STATUS_PROCESSING
        return self._draft(draft_id)

    async def set_status(self, draft_id, status, **fields):
        self.docs[draft_id].update(status=status, **fields)

    async def delete(self, draft_id):
        self.docs.pop(draft_id, None)

    def status(self, draft_id):
        return self.docs[draft_id]["status"]


class FakeNotion:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.created: list[dict] = []

    async def get_properties_schema(self, database_id):
        return OEUVRES_SCHEMA

    async def create_page(self, database_id, properties):
        if self.fail:
            raise RuntimeError("Notion down")
        self.created.append(properties)
        return {"id": "page", "public_url": "https://confrerie.notion.site/page"}


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.mention = f"<@{user_id}>"
        self.display_name = f"user{user_id}"
        self.dms: list[str] = []

    async def send(self, content=None, **kwargs):
        self.dms.append(content)


class FakeChannel:
    def __init__(self) -> None:
        self.sent: list[tuple] = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs.get("reply_to")))


class FakeBot:
    def __init__(self) -> None:
        self.users = {MEMBER_ID: FakeUser(MEMBER_ID), OWNER_ID: FakeUser(OWNER_ID)}
        self.channel = FakeChannel()

    def get_user(self, user_id):
        return self.users.get(int(user_id))

    async def fetch_user(self, user_id):
        return self.users.get(int(user_id))

    async def fetch_channel(self, channel_id):
        return self.channel


class FakeCtx:
    def __init__(self, custom_id: str, author_id: int = OWNER_ID) -> None:
        self.custom_id = custom_id
        self.author = FakeUser(author_id)
        self.guild = None
        self.replies: list[str] = []
        self.origin_edits: list[dict] = []
        self.modals: list = []

    async def send(self, content=None, **kwargs):
        self.replies.append(content)

    async def defer(self, **kwargs):
        pass

    async def edit_origin(self, **kwargs):
        self.origin_edits.append(kwargs)

    async def send_modal(self, modal):
        self.modals.append(modal)


class Harness(ReviewMixin):
    def __init__(self, repo: FakeRepo, notion: FakeNotion) -> None:
        self.repo = repo
        self.notion_client = notion
        self.bot = FakeBot()

    def _drafts(self):
        return self.repo


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(
        review,
        "module_config",
        {"confrerieOwnerId": str(OWNER_ID), "confrerieNotionDbOeuvresId": "db"},
    )
    monkeypatch.setattr(review, "config", {"discord": {}})


def _draft(source=SOURCE_DEFI, draft_id="defi-1-2") -> Draft:
    return Draft(
        id=draft_id,
        source=source,
        guild_id="9",
        submitter_id=MEMBER_ID,
        title="Grief",
        authors=["Mahé"],
        types=["Défi"],
        defi="Défi 12 : Questionnaire Personnage",
        link="https://discord.com/channels/9/1/3",
        channel_id=1,
        message_id=3,
    )


async def _click(harness, action, draft_id, author_id=OWNER_ID):
    ctx = FakeCtx(f"confrerie_draft:{action}:{draft_id}", author_id)
    await ReviewMixin.on_draft_button.callback(harness, ctx)
    return ctx


async def test_only_the_owner_can_review():
    harness = Harness(FakeRepo(_draft()), FakeNotion())
    ctx = await _click(harness, "approve", "defi-1-2", author_id=MEMBER_ID)
    assert "propriétaire" in ctx.replies[0]
    assert harness.repo.status("defi-1-2") == STATUS_PENDING
    assert harness.notion_client.created == []


async def test_approval_creates_one_page_and_replies_in_the_thread():
    harness = Harness(FakeRepo(_draft()), FakeNotion())
    ctx = await _click(harness, "approve", "defi-1-2")

    assert len(harness.notion_client.created) == 1
    assert harness.repo.status("defi-1-2") == STATUS_APPROVED
    edit = ctx.origin_edits[0]
    assert all(button.disabled for row in edit["components"] for button in row.components)
    content, reply_to = harness.bot.channel.sent[0]
    assert "https://confrerie.notion.site/page" in content
    assert reply_to == 3

    # A second click (or a click on a stale copy of the message) is a no-op.
    again = await _click(harness, "approve", "defi-1-2")
    assert "déjà été traitée" in again.replies[0]
    assert len(harness.notion_client.created) == 1


async def test_notion_failure_puts_the_draft_back_up_for_review():
    harness = Harness(FakeRepo(_draft()), FakeNotion(fail=True))
    ctx = await _click(harness, "approve", "defi-1-2")
    assert harness.repo.status("defi-1-2") == STATUS_PENDING
    assert ctx.origin_edits == []
    assert "réessayez" in ctx.replies[0]


async def test_rejected_demande_tells_the_member():
    draft_id = demande_draft_id()
    harness = Harness(FakeRepo(_draft(SOURCE_DEMANDE, draft_id)), FakeNotion())
    await _click(harness, "reject", draft_id)
    assert harness.repo.status(draft_id) == STATUS_REJECTED
    assert "pas été retenue" in harness.bot.users[MEMBER_ID].dms[0]
    assert harness.notion_client.created == []


async def test_edit_opens_a_prefilled_modal():
    harness = Harness(FakeRepo(_draft()), FakeNotion())
    ctx = await _click(harness, "edit", "defi-1-2")
    modal = ctx.modals[0]
    assert modal.custom_id == "confrerie_draft_edit:defi-1-2"
    assert harness.repo.status("defi-1-2") == STATUS_PENDING


async def test_submit_draft_dedupes_and_records_the_review_message(monkeypatch):
    harness = Harness(FakeRepo(), FakeNotion())

    class Sent:
        id = 55
        channel = type("C", (), {"id": 66})()

    async def fake_send(draft):
        return Sent()

    monkeypatch.setattr(harness, "_send_for_review", fake_send)
    assert await harness.submit_draft(_draft()) is True
    assert harness.repo.docs["defi-1-2"]["review_message_id"] == 55
    assert await harness.submit_draft(_draft()) is False


def test_button_custom_ids_fit_discord_limit():
    draft = _draft(SOURCE_DEMANDE, demande_draft_id())
    ids = [b.custom_id for row in review_buttons(draft) for b in row.components]
    assert len(ids) == 4
    assert all(len(i) <= 100 for i in ids)
