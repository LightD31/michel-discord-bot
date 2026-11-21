"""What members read in command descriptions and replies: French, spelt right, no stray pings."""

from discord_fakes import FakeChannel, FakeCtx, FakeGuild, FakeMember, bare_extension, run

from extensions.reminders import RemindersExtension


class Reminders:
    def __init__(self):
        self.added = []

    async def count_for_user(self, user_id):
        return 0

    async def add(self, reminder):
        self.added.append(reminder)
        return "ffff"


def ctx_in_guild():
    guild = FakeGuild(1)
    channel = guild.add_channel(FakeChannel(10))
    return FakeCtx(guild.add_member(FakeMember(7, "bob")), channel)


async def test_the_reminder_confirmation_reads_its_schedule_in_french(store_config):
    """It read "Rappel daily créé à 20:30" (Q5)."""
    store_config({"servers": {"1": {"moduleUtils": {"enabled": True}}}})
    repo = Reminders()
    ctx = ctx_in_guild()
    ext = bare_extension(RemindersExtension, ctx.bot, _reminder_repo=lambda gid: repo)
    await run(RemindersExtension.reminder_set, ext, ctx, "Sortir le chien", "20h30", "daily")
    assert repo.added
    assert "daily" not in ctx.last
    assert "Tous les jours à 20:30" in ctx.last and "Sortir le chien" in ctx.last
    assert ctx.replies[-1]["ephemeral"] is True


def test_the_reminder_command_describes_itself():
    """/rappel was described as "Gère les rappels pour voter", copied from /musique rappel."""
    assert "voter" not in str(RemindersExtension.reminder_set.description)


def test_command_descriptions_are_spelt_right(store_config):
    from extensions.spotify.votes import VotesMixin
    from extensions.zevent.commands import CommandsMixin as Zevent

    texts = [
        str(VotesMixin.addwithvote.description),
        str(Zevent.end.description),
        *(str(option.description) for option in RemindersExtension.reminder_set.options),
    ]
    for typo in ("ajoouter", "Créée l'embed", "aujoud'hui"):
        assert not [text for text in texts if typo in text], typo


async def test_rank_of_a_member_without_xp_does_not_ping_them(store_config):
    """A public "@x n'a pas encore de niveau" notified x of someone else's /niveau (Q9)."""
    from extensions.xp import XpExtension

    store_config({"servers": {"1": {"moduleXp": {"enabled": True}}}})
    ctx = ctx_in_guild()
    target = ctx.guild.add_member(FakeMember(8, "alice"))

    class NoStats:
        async def get_user(self, user_id):
            return None

    ext = bare_extension(XpExtension, ctx.bot, _repo=lambda gid: NoStats())
    await run(XpExtension.rank, ext, ctx, utilisateur=target)
    reply = ctx.replies[-1]
    assert "alice" in (reply["content"] or "") or "<@8>" in (reply["content"] or "")
    mentions = reply.get("allowed_mentions")
    assert mentions is not None and not mentions.to_dict().get("users"), mentions
    assert "users" not in (mentions.to_dict().get("parse") or [])
