import os
import random
from interactions import Client, Extension, slash_command, OptionType, SlashContext, slash_option
from rdoclient import RandomOrgClient

from dict import chooseList
from src import logutil
from src.utils import load_config

config = load_config()[0]

logger = logutil.init_logger(os.path.basename(__file__))

class RandomClass(Extension):
    def __init__(self, bot: Client):
        self.bot = bot
        self.random_client = RandomOrgClient(config.get("random").get("randomOrgApiKey"))

    def _get_random_index(self, min_val: int, max_val: int) -> int:
        """Helper function to get a random index using random.org API, fallback to Python's random."""
        try:
            response = self.random_client.generate_signed_integers(n=1, min=min_val, max=max_val)
            random_index = response["random"]["data"][0]
            return random_index, response
        except Exception as e:
            logger.error("Random.org API failed, using python random instead.", exc_info=True)
            return random.randint(min_val, max_val), None

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
    async def pick(self, ctx: SlashContext, choix: str, séparateur: str = ";"):
        choices = [choice.strip() for choice in choix.split(séparateur)]
        if len(choices) <= 1:
            await ctx.send("Compliqué de faire un choix quand il n'y a pas le choix")
            return
        await ctx.defer()
        random_index, response = self._get_random_index(1, len(choices))
        random_index -= 1  # Adjusting for 0-based index
        
        if response:
            link = self.random_client.create_url(response["random"], response["signature"])
            await ctx.send(
                f"{random.choice(chooseList)} : {choices[random_index]}\n*[Sélectionné par random.org](<{link}>)*"
            )
        else:
            await ctx.send(
                f"{random.choice(chooseList)} : {choices[random_index]}\n*Sélectionné par Python (erreur de random.org)*\n"
            )

    @slash_command(name="roll", description="Lance un dé")
    @slash_option(name="faces", description="Nombre de faces du dé", opt_type=OptionType.INTEGER, required=True)
    async def roll(self, ctx: SlashContext, faces: int):
        if faces < 2:
            await ctx.send("Un dé doit avoir au moins 2 faces !")
            return
        await ctx.defer()
        random_index, response = self._get_random_index(1, faces)
        
        if response:
            link = self.random_client.create_url(response["random"], response["signature"])
            await ctx.send(
                f":game_die: **{random_index}** :game_die:\n*[Sélectionné par random.org](<{link}>)*"
            )
        else:
            await ctx.send(f":game_die: **{random_index}** :game_die:")
