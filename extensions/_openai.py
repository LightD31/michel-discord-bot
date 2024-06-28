
from openai import AsyncOpenAI
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
    listen,
    ChannelType,
)
from interactions.api.events import MessageCreate
from interactions.client.errors import CommandOnCooldown
import os
from src import logutil
from src.utils import (
    load_config,
    sanitize_content,
    search_dict_by_sentence,
    extract_answer,
)

logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleIA")


class openAI(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot

    @listen()
    async def on_startup(self):
        self.openai_client = AsyncOpenAI(
            api_key=config["OpenAI"]["openaiApiKey"],
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
        # Get the latest 10 messages in the conversation
        conversation = []
        messages = await ctx.channel.fetch_messages(limit=10)
        for message in messages:
            if message.author.id == self.bot.user.id:
                conversation.append({"role": "system", "content": message.content})
            else:
                conversation.append(
                    {
                        "role": "user",
                        "content": f"{message.author.display_name} : {message.content}",
                    }
                )
        conversation.append(
            {
                "role": "system",
                "content": f"Tu vas jouer le rôle de Michel, un bot sarcastique pour Discord. Ton but est de répondre aux questions des membres de manière sarcastique, concise et précise\nRéponds à la question de {ctx.author.display_name} de manière drôle, en français. Aie un avis sur tout. Utilise des expressions familières et de l'argot si approprié.",
            }
        )
        # Reverse the list to have the last message last
        conversation.reverse()
        # Add the question to the conversation
        conversation.append(
            {"role": "user", "content": f"{ctx.author.display_name} : {question}"}
        )
        logger.info(conversation)

        response = await self.openai_client.chat.completions.create(
            model="gpt-4o",
            max_tokens=300,
            messages=conversation,
        )
        cost = self.calculate_cost(response)
        answer = response.choices[0].message.content
        logger.info(
            f"/ask utilisé par {ctx.author.username}\nQuestion: {question} - Answer: {answer}"
        )
        logger.info(
            "coût : %.5f$ | %.5f$ (%d tks) in | %.5f$ (%d tks) out",
            cost["input"] + cost["output"],
            cost["input"],
            response.usage.prompt_tokens,
            cost["output"],
            response.usage.completion_tokens,
        )
        await ctx.send(f"{ctx.author.mention} : {question}\n{answer}")

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

    def calculate_cost(self, response):
        MODEL_INFO = {
            "gpt-4o": {"input": 5 / 1e6, "output": 15 / 1e6},
            "gpt-4o-2024-05-13": {"input": 5 / 1e6, "output": 15 / 1e6},
            "gpt-4-turbo": {"input": 10 / 1e6, "output": 30 / 1e6},
            "gpt-4": {"input": 30 / 1e6, "output": 60 / 1e6},
            "gpt-4-32k": {"input": 60 / 1e6, "output": 120 / 1e6},
            "gpt-3.5-turbo": {"input": 0.5 / 1e6, "output": 1.5 / 1e6},
            "gpt-3.5-turbo-0125": {"input": 0.5 / 1e6, "output": 1.5 / 1e6},
        }
        if response.model not in MODEL_INFO:
            logger.warn("Model not found in MODEL_INFO: %s", response.model)
            return {"input": 0, "output": 0}
        return {
            "input": MODEL_INFO[response.model]["input"] * response.usage.prompt_tokens,
            "output": MODEL_INFO[response.model]["output"]
            * response.usage.completion_tokens,
        }

    @listen()
    async def on_message(self, event: MessageCreate):
        """
        This method is called when a message is received.

        Args:
            event (interactions.api.events.MessageCreate): The message event.
        """
        if (
            event.message.channel.type == ChannelType.DM
            or event.message.channel.type == ChannelType.GROUP_DM
        ) and event.message.author.id != self.bot.user.id:
            logger.info(
                "Message from %s (ID: %s) in DMs : %s",
                event.message.author.username,
                event.message.author.id,
                event.message.content,
            )
            # Get the latest 5 messages in the conversation
            messages = await event.message.channel.fetch_messages(limit=2)
            conversation = []
            for message in messages:
                if message.author.id == self.bot.user.id:
                    conversation.append({"role": "system", "content": message.content})
                else:
                    conversation.append({"role": "user", "content": message.content})
            conversation.reverse()
            logger.info(conversation)
            response = await self.openai_client.chat.completions.create(
                model="gpt-4o",
                max_tokens=300,
                messages=conversation,
            )
            cost = self.calculate_cost(response)
            answer = response.choices[0].message.content
            logger.info(f"Réponse à {event.message.author.username} : {answer}")
            logger.info(
                "coût : %.5f$ | %.5f$ (%d tks) in | %.5f$ (%d tks) out",
                cost["input"] + cost["output"],
                cost["input"],
                response.usage.prompt_tokens,
                cost["output"],
                response.usage.completion_tokens,
            )
            await event.message.channel.send(answer)
