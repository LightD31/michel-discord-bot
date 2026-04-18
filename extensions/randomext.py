"""Extension Random — thin Discord glue layer.

Validation logic lives in features/random/.
"""

import os
import random

from interactions import (
    Client,
    Extension,
    IntegrationType,
    OptionType,
    SlashContext,
    slash_command,
    slash_option,
)

from dict import chooseList
from features.random import validate_choices, validate_die_faces
from src import logutil

logger = logutil.init_logger(os.path.basename(__file__))

DEFAULT_SEPARATOR = ";"


class RandomExtension(Extension):
    def __init__(self, bot: Client):
        self.bot = bot

    @slash_command(
        name="pick",
        description="Choisit un élément aléatoire (Grâce aux éclairs !)",
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @slash_option(
        "choix", "Choix, séparés par des point-virgules", opt_type=OptionType.STRING, required=True
    )
    @slash_option(
        "séparateur", "Séparateur des choix (Défaut: ;)", opt_type=OptionType.STRING, required=False
    )
    async def pick(self, ctx: SlashContext, choix: str, séparateur: str = DEFAULT_SEPARATOR):
        choices = [c.strip() for c in choix.split(séparateur) if c.strip()]
        error_msg = validate_choices(choices)
        if error_msg:
            await ctx.send(error_msg)
            return
        selected = choices[random.randint(0, len(choices) - 1)]
        await ctx.send(f"{random.choice(chooseList)} : **{selected}**")

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
    )
    @slash_option(
        "séparateur",
        "Séparateur des éléments (Défaut: ;)",
        opt_type=OptionType.STRING,
        required=False,
    )
    async def shuffle(self, ctx: SlashContext, liste: str, séparateur: str = DEFAULT_SEPARATOR):
        items = [item.strip() for item in liste.split(séparateur) if item.strip()]
        error_msg = validate_choices(items)
        if error_msg:
            await ctx.send(error_msg)
            return
        shuffled = items.copy()
        random.shuffle(shuffled)
        numbered = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(shuffled))
        await ctx.send(f"🔀 **Liste mélangée :**\n{numbered}")
