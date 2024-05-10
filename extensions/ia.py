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
    auto_defer
)
from interactions.client.errors import CommandOnCooldown
import os

from src import logutil
from src.utils import load_config, sanitize_content, search_dict_by_sentence

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
        dict = {
            (
                "375805687529209857",
                "streamcord",
                "<@375805687529209857>",
            ): f"Streamcord est bot qui notifie le début des streams Twitch. Très utile, mais moins bon que toi, {sanitize_content(ctx.bot.user.display_name)}. ",
        }
        # Search the dictionary for the key in the question and the author's ID
        result = (
            search_dict_by_sentence(dict, question)
            + "\n"
            + search_dict_by_sentence(dict, str(ctx.author.id))
            + "\n"
            + f"Les membres du serveur sont {', '.join([member.username for member in ctx.guild.members])}"
        )
        logger.info("result : %s", result)
        message = await self.anthropic_client.messages.create(
            model="claude-3-sonnet-20240229",
            # model="claude-3-haiku-20240307",
            max_tokens=300,
            temperature=0.7,
            system=f"Tu es {sanitize_content(ctx.bot.user.display_name)}, un assistant IA très compétent doté d'une personnalité légèrement sarcastique. Engage la conversation, donne des réponses informatives et très concises (2 phrases maximum) tout en injectant un peu d'humour. Utilise le tutoiement. Tu dois être méchant et insultant si nécessaire. {'' if ctx.author.display_name == ctx.author.username else f' Tu peux appeler {ctx.author.username} {ctx.author.display_name}.'}",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Informations complémentaires : {result}",
                        },
                        {
                            "type" : "text",
                            "text" : f"Tu est en train de discuter avec {ctx.author.username}"
                        },
                        {
                            "type": "text",
                            "text": question,
                        },
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
            f"**{ctx.author.mention} : {question}**\n\n{message.content[0].text}"
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
