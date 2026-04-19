"""Extension Tricount — gestion de dépenses partagées entre membres d'un serveur."""

from interactions import Client, Extension

from .expenses import ExpensesMixin
from .groups import GroupsMixin
from .reports import ReportsMixin


class TricountExtension(GroupsMixin, ExpensesMixin, ReportsMixin, Extension):
    """Discord extension combining group management, expenses, and reporting."""

    def __init__(self, bot: Client):
        self.bot: Client = bot


def setup(bot: Client):
    TricountExtension(bot)
