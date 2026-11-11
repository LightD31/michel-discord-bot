"""A message the bot sends without allowed_mentions never pings @everyone (T03)."""

import pytest
from interactions import AllowedMentions
from interactions.models.discord import message as message_module

from src.discord_ext import mentions
from src.discord_ext.mentions import NO_PINGS, everyone_if_written, mentions_written_in


@pytest.fixture
def installed(monkeypatch):
    # install() patches the library for the process: undo it after the test.
    monkeypatch.setattr(
        message_module, "process_allowed_mentions", message_module.process_allowed_mentions
    )
    monkeypatch.setattr(mentions, "_installed", False)
    mentions.install()
    mentions.install()  # idempotent


def payload(**kwargs):
    return message_module.process_message_payload(content="@everyone <@&7> <@1>", **kwargs)


def test_a_message_without_allowed_mentions_pings_users_and_roles_only(installed):
    allowed = payload()["allowed_mentions"]
    assert allowed == {"parse": ["users", "roles"], "replied_user": True}
    allowed["parse"].append("everyone")  # each payload gets its own copy
    assert payload()["allowed_mentions"]["parse"] == ["users", "roles"]


def test_what_the_caller_says_wins(installed):
    assert payload(allowed_mentions=NO_PINGS)["allowed_mentions"] == {"replied_user": False}
    explicit = {"parse": ["everyone"]}
    assert payload(allowed_mentions=explicit)["allowed_mentions"] is explicit


def test_without_install_the_library_sends_nothing():
    assert "allowed_mentions" not in payload()


@pytest.mark.parametrize(
    ("template", "everyone"),
    [("Bienvenue {mention} !", False), ("@everyone Bienvenue {mention}", True), ("@here", True)],
)
def test_an_admin_template_pings_everyone_only_when_it_says_so(template, everyone):
    parse = everyone_if_written(template).parse
    assert "users" in parse and "roles" in parse
    assert ("everyone" in parse) is everyone


def test_mentions_written_in_lets_only_the_template_ping():
    allowed = mentions_written_in("<@1> <@&2> {title}")
    assert allowed.users == [1] and allowed.roles == [2] and allowed.parse == []
    assert isinstance(allowed, AllowedMentions)
