import os
import random
from interactions import Client,Extension, slash_command, OptionType, SlashContext, slash_option
from rdoclient import RandomOrgClient

from dict import chooseList
from src import logutil
from src.utils import load_config

config = load_config()[0]

logger = logutil.init_logger(os.path.basename(__file__))

class RandomClass(Extension):
    def __init__(self, bot: Client):
        self.bot = bot

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
    async def pick(self, ctx: SlashContext, choix, séparateur=";"):
        choices = [choice.strip() for choice in choix.split(séparateur)]
        if len(choices) <= 1:
            await ctx.send("Compliqué de faire un choix quand il n'y a pas le choix")
            return
        try:
            # Use random.org API to generate a random integer between 0 and len(choices)-1
            r = RandomOrgClient(config.get("random").get("randomOrgApiKey"))
            response = r.generate_signed_integers(n=1, min=1, max=len(choices))
            link = r.create_url(response["random"], response["signature"])
            random_index = response["random"]["data"][0] - 1
            await ctx.send(
                f"{random.choice(chooseList)} : {choices[random_index]}\n*[Sélectionné par random.org](<{link}>)*\nid : {response['random'].get('serialNumber')}\n"
            )
        except Exception as e:
            logger.error(
                "Random.org API failed, using python random instead.\n%s\n%s",
                response.get("error"),
                e,
            )
            # If random.org API fails, use python random
            logger.warning("Random.org API failed, using python random instead.")
            random_index = random.randint(0, len(choices) - 1)
            await ctx.send(
                f"{random.choice(chooseList)} : {choices[random_index]}\n*Sélectionné par Python (erreur de random.org)*\n"
            )
