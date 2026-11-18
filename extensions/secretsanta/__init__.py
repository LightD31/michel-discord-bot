"""Extension Discord du Père Noël Secret.

Thin glue layer: the domain model, persistence and assignment algorithms live in
``features/secretsanta/``. The extension combines mixins for the session,
draw, bans and button flows.
"""

from interactions import Client, Extension

from features.secretsanta import SecretSantaRepository
from features.secretsanta.relay import RelayLimiter
from src.core import logging as logutil

from .bans import BansMixin
from .buttons import ButtonsMixin
from .draws import DrawsMixin
from .exchange import ExchangeMixin
from .sessions import SessionsMixin

logger = logutil.init_logger(__name__)


class SecretSantaExtension(
    Extension, SessionsMixin, DrawsMixin, BansMixin, ButtonsMixin, ExchangeMixin
):
    """Discord extension combining the Secret Santa subcommands and buttons."""

    MODULE_KEYS = ("moduleSecretSanta",)

    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.repository = SecretSantaRepository()
        self.relay_limiter = RelayLimiter()
