"""Extension Random — thin Discord glue layer.

Validation logic lives in features/random/. Anyone can install these commands:
they are named and described in English with a French localisation, and answer
in the language of whoever runs them (src/discord_ext/locale.py).
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

from features.messages import chooseList, chooseListEn
from features.random import validate_choices, validate_die_faces
from src.core import logging as logutil
from src.core.text import clip
from src.discord_ext.locale import en_fr_desc, en_fr_name, is_french, say
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
        description=en_fr_desc(
            "Pick a random item (thanks to lightning!)",
            "Choisit un élément aléatoire (Grâce aux éclairs !)",
        ),
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @slash_option(
        en_fr_name("choices", "choix"),
        en_fr_desc("Choices, separated by semicolons", "Choix, séparés par des point-virgules"),
        opt_type=OptionType.STRING,
        required=True,
        max_length=MAX_INPUT_LENGTH,
        argument_name="choix",
    )
    @slash_option(
        en_fr_name("separator", "séparateur"),
        en_fr_desc("Separator between choices (default: ;)", "Séparateur des choix (Défaut: ;)"),
        opt_type=OptionType.STRING,
        required=False,
        max_length=10,
        argument_name="séparateur",
    )
    @cooldown(Buckets.USER, 5, 30)
    async def pick(self, ctx: SlashContext, choix: str, séparateur: str = DEFAULT_SEPARATOR):
        french = is_french(ctx)
        choices = [c.strip() for c in choix.split(séparateur) if c.strip()]
        error_msg = validate_choices(choices, french=french)
        if error_msg:
            await ctx.send(error_msg)
            return
        selected = choices[random.randint(0, len(choices) - 1)]
        phrase = random.choice(chooseList) + " :" if french else random.choice(chooseListEn) + ":"
        await ctx.send(clip(f"{phrase} **{selected}**"), allowed_mentions=NO_PINGS)

    @slash_command(
        name="roll",
        description=en_fr_desc("Roll a die", "Lance un dé"),
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @slash_option(
        name="faces",
        description=en_fr_desc("Number of faces of the die", "Nombre de faces du dé"),
        opt_type=OptionType.INTEGER,
        required=True,
    )
    @cooldown(Buckets.USER, 5, 30)
    async def roll(self, ctx: SlashContext, faces: int):
        error_msg = validate_die_faces(faces, french=is_french(ctx))
        if error_msg:
            await ctx.send(error_msg)
            return
        await ctx.send(f":game_die: **{random.randint(1, faces)}** :game_die:")

    @slash_command(
        name="coin",
        description=en_fr_desc("Flip a coin", "Lance une pièce de monnaie"),
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @cooldown(Buckets.USER, 5, 30)
    async def coin(self, ctx: SlashContext):
        tails = random.randint(1, 2) == 1
        if tails:
            result = say(ctx, fr="🪙 **Pile**", en="🪙 **Tails**")
        else:
            result = say(ctx, fr="🪙 **Face**", en="🪙 **Heads**")
        await ctx.send(result)

    @slash_command(
        name="shuffle",
        description=en_fr_desc("Shuffle a list of items", "Mélange une liste d'éléments"),
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @slash_option(
        en_fr_name("list", "liste"),
        en_fr_desc(
            "Items to shuffle, separated by semicolons",
            "Éléments à mélanger, séparés par des point-virgules",
        ),
        opt_type=OptionType.STRING,
        required=True,
        max_length=MAX_INPUT_LENGTH,
        argument_name="liste",
    )
    @slash_option(
        en_fr_name("separator", "séparateur"),
        en_fr_desc("Separator between items (default: ;)", "Séparateur des éléments (Défaut: ;)"),
        opt_type=OptionType.STRING,
        required=False,
        max_length=10,
        argument_name="séparateur",
    )
    @cooldown(Buckets.USER, 5, 30)
    async def shuffle(self, ctx: SlashContext, liste: str, séparateur: str = DEFAULT_SEPARATOR):
        french = is_french(ctx)
        items = [item.strip() for item in liste.split(séparateur) if item.strip()]
        error_msg = validate_choices(items, french=french)
        if error_msg:
            await ctx.send(error_msg)
            return
        shuffled = items.copy()
        random.shuffle(shuffled)
        numbered = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(shuffled))
        title = "🔀 **Liste mélangée :**" if french else "🔀 **Shuffled list:**"
        await ctx.send(clip(f"{title}\n{numbered}"), allowed_mentions=NO_PINGS)
