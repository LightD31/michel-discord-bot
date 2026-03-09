"""Extension Random — commandes de choix aléatoires et lancés de dés."""

import os
import random
from typing import Optional
from interactions import (
    Client,
    Extension,
    slash_command,
    OptionType,
    SlashContext,
    slash_option,
    IntegrationType,
)

from dict import chooseList
from src import logutil

logger = logutil.init_logger(os.path.basename(__file__))

# Constantes
MAX_CHOICES = 100
MAX_DIE_FACES = 1000000
MIN_DIE_FACES = 2
DEFAULT_SEPARATOR = ";"

# Messages d'erreur améliorés
ERROR_MESSAGES = {
    "no_choice": "🤔 Compliqué de faire un choix quand il n'y a pas le choix ! Ajoutez au moins 2 options.",
    "too_many_choices": f"😅 Trop de choix ! Limitez-vous à {MAX_CHOICES} options maximum.",
    "invalid_die_faces": f"🎲 Un dé doit avoir entre {MIN_DIE_FACES} et {MAX_DIE_FACES:,} faces !",
}


class RandomExtension(Extension):
    def __init__(self, bot: Client):
        self.bot = bot

    def _get_random_int(self, min_val: int, max_val: int) -> int:
        """Retourne un entier aléatoire entre min_val et max_val (inclus)."""
        return random.randint(min_val, max_val)

    def _validate_choices(self, choices: list) -> Optional[str]:
        """Valide la liste des choix et retourne un message d'erreur si nécessaire."""
        if len(choices) <= 1:
            return ERROR_MESSAGES["no_choice"]
        if len(choices) > MAX_CHOICES:
            return ERROR_MESSAGES["too_many_choices"]
        return None

    def _validate_die_faces(self, faces: int) -> Optional[str]:
        """Valide le nombre de faces du dé et retourne un message d'erreur si nécessaire."""
        if faces < MIN_DIE_FACES or faces > MAX_DIE_FACES:
            return ERROR_MESSAGES["invalid_die_faces"]
        return None

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
    )
    @slash_option(
        "séparateur",
        "Séparateur des choix (Défaut: ;)",
        opt_type=OptionType.STRING,
        required=False,
    )
    async def pick(
        self, ctx: SlashContext, choix: str, séparateur: str = DEFAULT_SEPARATOR
    ):
        # Nettoyage et validation des choix
        choices = [
            choice.strip() for choice in choix.split(séparateur) if choice.strip()
        ]

        # Validation des entrées
        error_msg = self._validate_choices(choices)
        if error_msg:
            await ctx.send(error_msg)
            return

        random_index = self._get_random_int(0, len(choices) - 1)

        # Sélection d'un message aléatoire de choix
        choice_message = random.choice(chooseList)
        selected_choice = choices[random_index]

        await ctx.send(f"{choice_message} : **{selected_choice}**")

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
        # Validation des entrées
        error_msg = self._validate_die_faces(faces)
        if error_msg:
            await ctx.send(error_msg)
            return

        result = self._get_random_int(1, faces)
        await ctx.send(f":game_die: **{result}** :game_die:")

    @slash_command(
        name="coin",
        description="Lance une pièce de monnaie",
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    async def coin(self, ctx: SlashContext):
        """Lance une pièce de monnaie."""
        result = "🪙 **Pile**" if self._get_random_int(1, 2) == 1 else "🪙 **Face**"
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
    async def shuffle(
        self, ctx: SlashContext, liste: str, séparateur: str = DEFAULT_SEPARATOR
    ):
        """Mélange une liste d'éléments aléatoirement."""
        # Nettoyage et validation des éléments
        items = [item.strip() for item in liste.split(séparateur) if item.strip()]

        # Validation des entrées
        error_msg = self._validate_choices(items)
        if error_msg:
            await ctx.send(error_msg)
            return

        # Mélange de la liste
        shuffled = items.copy()
        random.shuffle(shuffled)

        # Formatage du résultat
        numbered_list = "\n".join(
            f"{idx + 1}. {item}" for idx, item in enumerate(shuffled)
        )

        await ctx.send(f"🔀 **Liste mélangée :**\n{numbered_list}")
