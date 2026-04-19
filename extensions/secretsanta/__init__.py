"""Extension Discord du Père Noël Secret.

Thin glue layer: the domain model, persistence and assignment algorithms live in
``features/secretsanta/``. The extension combines mixins for the session,
draw, bans and button flows.
"""

import os

from interactions import Client, Extension

from features.secretsanta import SecretSantaRepository
from src import logutil

from .bans import BansMixin
from .buttons import ButtonsMixin
from .draws import DrawsMixin
from .sessions import SessionsMixin

logger = logutil.init_logger(os.path.basename(__file__))


class SecretSantaExtension(Extension, SessionsMixin, DrawsMixin, BansMixin, ButtonsMixin):
    """Discord extension combining the Secret Santa subcommands and buttons."""

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.repository = SecretSantaRepository()
