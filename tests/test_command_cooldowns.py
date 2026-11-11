"""Global and heavy commands carry a cooldown (F04).

The global ones can be installed by anyone, in any server; the heavy ones
read a whole server's data or call Spotify. A cooldown is answered with a
French ephemeral message by extensions/errors.py.
"""

import pytest
from interactions import Buckets

from extensions.admin import AdminExtension
from extensions.feur import FeurExtension
from extensions.random_ import RandomExtension
from extensions.tricount.reports import ReportsMixin
from extensions.xp.commands import CommandsMixin as XpCommands


@pytest.mark.parametrize(
    "command",
    [
        RandomExtension.pick,
        RandomExtension.roll,
        RandomExtension.coin,
        RandomExtension.shuffle,
        AdminExtension.ping,
        FeurExtension.feur_stats,
        XpCommands.rank,
        XpCommands.leaderboard,
        ReportsMixin.tricount_graphique,
    ],
    ids=lambda command: str(command.name),
)
def test_the_command_has_a_per_member_cooldown(command):
    assert command.cooldown is not None
    assert command.cooldown.bucket == Buckets.USER
