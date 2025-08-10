import os
import random
from typing import Tuple, Optional, Dict, Any
from interactions import Client, Extension, slash_command, OptionType, SlashContext, slash_option
from rdoclient import RandomOrgClient

from dict import chooseList
from src import logutil
from src.utils import load_config

# Configuration
config = load_config()[0]
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
    "api_error": "⚡ Random.org temporairement indisponible, utilisation du générateur Python.",
}

class RandomClass(Extension):
    def __init__(self, bot: Client):
        self.bot = bot
        # Gestion sécurisée de la configuration
        if config and "random" in config and "randomOrgApiKey" in config["random"]:
            self.random_client = RandomOrgClient(config["random"]["randomOrgApiKey"])
        else:
            logger.warning("Clé API Random.org non trouvée, utilisation de Python random uniquement")
            self.random_client = None

    def _get_random_index(self, min_val: int, max_val: int) -> Tuple[int, Optional[Dict[str, Any]]]:
        """Helper function to get a random index using random.org API, fallback to Python's random."""
        if not self.random_client:
            return random.randint(min_val, max_val), None
            
        try:
            response = self.random_client.generate_signed_integers(n=1, min=min_val, max=max_val)
            random_index = response["random"]["data"][0]
            return random_index, response
        except Exception:
            logger.error("Random.org API failed, using python random instead.", exc_info=True)
            return random.randint(min_val, max_val), None

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
    async def pick(self, ctx: SlashContext, choix: str, séparateur: str = DEFAULT_SEPARATOR):
        # Nettoyage et validation des choix
        choices = [choice.strip() for choice in choix.split(séparateur) if choice.strip()]
        
        # Validation des entrées
        error_msg = self._validate_choices(choices)
        if error_msg:
            await ctx.send(error_msg)
            return

        await ctx.defer()
        random_index, response = self._get_random_index(1, len(choices))
        random_index -= 1  # Ajustement pour l'index basé sur 0
        
        # Sélection d'un message aléatoire de choix
        choice_message = random.choice(chooseList)
        selected_choice = choices[random_index]
        
        if response and self.random_client:
            link = self.random_client.create_url(response["random"], response["signature"])
            await ctx.send(
                f"{choice_message} : **{selected_choice}**\n*[Sélectionné par random.org](<{link}>)*"
            )
        else:
            await ctx.send(
                f"{choice_message} : **{selected_choice}**\n*{ERROR_MESSAGES['api_error']}*"
            )

    @slash_command(name="roll", description="Lance un dé")
    @slash_option(
        name="faces", 
        description="Nombre de faces du dé", 
        opt_type=OptionType.INTEGER, 
        required=True
    )
    async def roll(self, ctx: SlashContext, faces: int):
        # Validation des entrées
        error_msg = self._validate_die_faces(faces)
        if error_msg:
            await ctx.send(error_msg)
            return
            
        await ctx.defer()
        random_index, response = self._get_random_index(1, faces)
        
        if response and self.random_client:
            link = self.random_client.create_url(response["random"], response["signature"])
            await ctx.send(
                f":game_die: **{random_index}** :game_die:\n*[Sélectionné par random.org](<{link}>)*"
            )
        else:
            await ctx.send(
                f":game_die: **{random_index}** :game_die:\n*{ERROR_MESSAGES['api_error']}*"
            )

    @slash_command(name="coin", description="Lance une pièce de monnaie")
    async def coin(self, ctx: SlashContext):
        """Nouvelle commande pour lancer une pièce."""
        await ctx.defer()
        random_index, response = self._get_random_index(1, 2)
        
        result = "🪙 **Pile**" if random_index == 1 else "🪙 **Face**"
        
        if response and self.random_client:
            link = self.random_client.create_url(response["random"], response["signature"])
            await ctx.send(f"{result}\n*[Sélectionné par random.org](<{link}>)*")
        else:
            await ctx.send(f"{result}\n*{ERROR_MESSAGES['api_error']}*")
