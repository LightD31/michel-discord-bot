"""Shared helpers and config for the Secret Santa extension mixins."""

import os
from pathlib import Path

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    ComponentContext,
    SlashContext,
    spread_to_rows,
)

from src.core import logging as logutil
from src.core.config import load_config
from src.webui.schemas import SchemaBase, enabled_field, register_module


@register_module("moduleSecretSanta")
class SecretSantaConfig(SchemaBase):
    __label__ = "Secret Santa"
    __description__ = (
        "Secret Santa dans Discord : inscriptions, exclusions, tirage et envoi des "
        "assignations par MP. Réglages globaux dans la section « Secret Santa (global) »."
    )
    __icon__ = "🎁"
    __category__ = "Événements"

    enabled: bool = enabled_field()


logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleSecretSanta")

DATA_DIR = Path("data/secret_santa")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_context_id(ctx: SlashContext | ComponentContext) -> str:
    """Unique id for the session's scope (guild or DM channel)."""
    if ctx.guild:
        return f"guild_{ctx.guild.id}"
    return f"channel_{ctx.channel.id}"


def create_join_buttons(context_id: str, disabled: bool = False) -> list[ActionRow]:
    """Build the join / leave buttons used on the session embed."""
    return spread_to_rows(
        Button(
            style=ButtonStyle.SUCCESS,
            label="Participer 🎁",
            custom_id=f"secretsanta_join:{context_id}",
            disabled=disabled,
        ),
        Button(
            style=ButtonStyle.DANGER,
            label="Se retirer",
            custom_id=f"secretsanta_leave:{context_id}",
            disabled=disabled,
        ),
    )
