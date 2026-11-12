"""Extension Random — thin Discord glue layer.

Validation logic lives in features/random/.
"""

import os
import random

from interactions import (
    Buckets,
    Client,
    Extension,
    IntegrationType,
    OptionType,
    SlashContext,
    cooldown,
    slash_command,
    slash_option,
)

from features.messages import chooseList
from features.random import validate_choices, validate_die_faces
from src.core import logging as logutil
from src.core.text import clip
from src.discord_ext.mentions import NO_PINGS

logger = logutil.init_logger(__name__)

DEFAULT_SEPARATOR = ";"
# The answer repeats the input: keep it within a message's 2000 characters.
MAX_INPUT_LENGTH = 1500
# Anyone can run these, in any server that installs the app: what they type is
# echoed (with NO_PINGS), so "@everyone" or a role in a choice must not ping.


class RandomExtension(Extension):
    MODULE_KEYS = ()
    HELP_LABEL = "🎲 Hasard"  # heading in /aide

    def __init__(self, bot: Client):
        self.bot = bot

    @slash_command(
        name="pick",
        description="Choisit un élément aléatoire (Grâce aux éclairs !)",
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @slash_option(
        "choix",
        "Choix, séparés par des point-virgules",
        opt_type=OptionType.STRING,
        required=True,
        max_length=MAX_INPUT_LENGTH,
    )
    @slash_option(
        "séparateur",
        "Séparateur des choix (Défaut: ;)",
        opt_type=OptionType.STRING,
        required=False,
        max_length=10,
    )
    @cooldown(Buckets.USER, 5, 30)
    async def pick(self, ctx: SlashContext, choix: str, séparateur: str = DEFAULT_SEPARATOR):
        choices = [c.strip() for c in choix.split(séparateur) if c.strip()]
        error_msg = validate_choices(choices)
        if error_msg:
            await ctx.send(error_msg)
            return
        selected = choices[random.randint(0, len(choices) - 1)]
        await ctx.send(
            clip(f"{random.choice(chooseList)} : **{selected}**"), allowed_mentions=NO_PINGS
        )

    @slash_command(
        name="roll",
        description="Lance un dé",
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @slash_option(
        name="faces",
        description="Nombre de faces du dé",
        opt_type=OptionType.INTEGER,
        required=True,
    )
    @cooldown(Buckets.USER, 5, 30)
    async def roll(self, ctx: SlashContext, faces: int):
        error_msg = validate_die_faces(faces)
        if error_msg:
            await ctx.send(error_msg)
            return
        await ctx.send(f":game_die: **{random.randint(1, faces)}** :game_die:")

    @slash_command(
        name="coin",
        description="Lance une pièce de monnaie",
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @cooldown(Buckets.USER, 5, 30)
    async def coin(self, ctx: SlashContext):
        result = "🪙 **Pile**" if random.randint(1, 2) == 1 else "🪙 **Face**"
        await ctx.send(result)

    @slash_command(
        name="shuffle",
        description="Mélange une liste d'éléments",
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @slash_option(
        "liste",
        "Éléments à mélanger, séparés par des point-virgules",
        opt_type=OptionType.STRING,
        required=True,
        max_length=MAX_INPUT_LENGTH,
    )
    @slash_option(
        "séparateur",
        "Séparateur des éléments (Défaut: ;)",
        opt_type=OptionType.STRING,
        required=False,
        max_length=10,
    )
    @cooldown(Buckets.USER, 5, 30)
    async def shuffle(self, ctx: SlashContext, liste: str, séparateur: str = DEFAULT_SEPARATOR):
        items = [item.strip() for item in liste.split(séparateur) if item.strip()]
        error_msg = validate_choices(items)
        if error_msg:
            await ctx.send(error_msg)
            return
        shuffled = items.copy()
        random.shuffle(shuffled)
        numbered = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(shuffled))
        await ctx.send(clip(f"🔀 **Liste mélangée :**\n{numbered}"), allowed_mentions=NO_PINGS)
