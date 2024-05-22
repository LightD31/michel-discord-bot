from anthropic import AsyncAnthropic
from interactions import (
    Extension,
    slash_command,
    Client,
    listen,
    slash_option,
    OptionType,
    SlashContext,
    cooldown,
    Buckets,
    auto_defer,
)
from interactions.client.errors import CommandOnCooldown
import os
import re
from src import logutil
from src.utils import (
    load_config,
    sanitize_content,
    search_dict_by_sentence,
    extract_answer,
)

logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleIA")


class IA(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot

    @listen()
    async def on_startup(self):
        self.anthropic_client = AsyncAnthropic(
            api_key=config["Anthropic"]["anthropicApiKey"],
        )

    @slash_command(
        name="ask",
        description="Pour poser toutes sortes de questions à Michel.",
        scopes=enabled_servers,
    )
    @cooldown(Buckets.USER, 1, 30)
    @auto_defer()
    @slash_option("question", "La question", opt_type=OptionType.STRING, required=True)
    async def ask(self, ctx: SlashContext, question: str):

        # Define a dictionary with relevant information
        dict = {}
        # Search the dictionary for the key in the question and the author's ID
        result = [
            search_dict_by_sentence(dict, question),
            search_dict_by_sentence(dict, str(ctx.author.id)),
            f"Utilisateurs : {', '.join([f'username : {member.username} (Display name :{member.display_name}, ID : {member.id})' for member in ctx.guild.members])}",
        ]

        logger.info("result : %s", result)
        message = await self.anthropic_client.messages.create(
            model="claude-3-sonnet-20240229",
            # model="claude-3-haiku-20240307",
            temperature=0.7,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Tu vas jouer le rôle de Michel, un bot sarcastique pour Discord. Ton but est de répondre aux questions des membres de manière sarcastique et concise, en utilisant des informations supplémentaires si nécessaire. Voici un dictionnaire contenant des informations que tu peux utiliser pour répondre aux questions :\n\n<dictionary>\n{result}\n</dictionary>\n\nVoici la question de {ctx.author.display_name} à laquelle tu dois répondre :\n<question>{question}</question>\n\nCommence par chercher dans le dictionnaire des informations pertinentes pour répondre à la question. Si tu trouves des informations utiles, inclus-les dans ta réponse. Ne parle pas du dictionnaire.\n\nRéponds à la question de manière sarcastique, en français. Aie un avis sur tout. N'hésite pas à être méchant si nécessaire. Utilise des expressions familières et de l'argot si approprié.\n\nÉcris ta réponse à l'intérieur de balises <answer>.",
                        }
                    ],
                }
            ],
        )
        cost = self.calculate_cost(message)
        logger.info(
            "/ask utilisé par %s : %s",
            ctx.author.display_name,
            question,
        )
        logger.info(
            f"coût : %.5f$ | %.5f$ (%d tks) in | %.5f$ (%d tks) out",
            cost["total_cost"],
            cost["input_cost"],
            message.usage.input_tokens,
            cost["output_cost"],
            message.usage.output_tokens,
        )
        await ctx.send(
            f"**{ctx.author.mention} : {question}**\n\n{extract_answer(message.content[0].text)}"
        )

    @ask.error
    async def on_command_error(
        self, error: Exception, ctx: SlashContext, question: str
    ):
        if isinstance(error, CommandOnCooldown):
            logger.info("/ask command on cooldown for %s", ctx.author.display_name)
            await ctx.send(
                f"Commande en cooldown. Veuillez réessayer dans {'{:.2f}'.format(error.cooldown.get_cooldown_time())} secondes.",
                ephemeral=True,
            )
        else:
            raise error

    def calculate_cost(self, message):
        """
        Calculates the cost of using a specific model based on the message.

        Args:
            message: The message object containing the model information.

        Returns:
            A tuple containing the total cost, input cost, and output cost.

        Raises:
            None.
        """
        if message.model == "claude-3-haiku-20240307":
            input_cost_per_token = 0.25 / 1e6  # $0.25 per million tokens
            output_cost_per_token = 0.5 / 1e6  # $0.5 per million tokens
        elif message.model == "claude-3-opus-20240229":
            input_cost_per_token = 15 / 1e6  # $15 per million tokens
            output_cost_per_token = 75 / 1e6  # $75 per million tokens
        elif message.model == "claude-3-sonnet-20240229":
            input_cost_per_token = 3 / 1e6  # $3 per million tokens
            output_cost_per_token = 15 / 1e6  # $15 per million tokens
        else:
            return {"total_cost": -1, "input_cost": -1, "output_cost": -1}

        input_cost = message.usage.input_tokens * input_cost_per_token
        output_cost = message.usage.output_tokens * output_cost_per_token
        total_cost = input_cost + output_cost
        return {
            "total_cost": total_cost,
            "input_cost": input_cost,
            "output_cost": output_cost,
        }
