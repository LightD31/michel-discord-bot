import os
import random

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from interactions.api.events import MessageCreate
from interactions import (
    Button,
    Extension,
    slash_command,
    Client,
    listen,
    ChannelType,
    slash_option,
    OptionType,
    SlashContext,
    cooldown,
    Buckets,
    auto_defer,
    ButtonStyle,
)
from interactions.client.errors import CommandOnCooldown
from interactions.api.events import Component

from src import logutil
from src.utils import (
    load_config,
    search_dict_by_sentence,
    extract_answer,
)

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleIA")


class IAExtension(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.openai_client = None
        self.anthropic_client = None

    @listen()
    async def on_startup(self):
        self.openai_client = AsyncOpenAI(api_key=config["OpenAI"]["openaiApiKey"])
        self.anthropic_client = AsyncAnthropic(
            api_key=config["Anthropic"]["anthropicApiKey"]
        )

    @slash_command(
        name="ask", description="Ask Michel and vote for the better answer"
    )
    @cooldown(Buckets.USER, 1, 20)
    @auto_defer()
    @slash_option("question", "Ta question", opt_type=OptionType.STRING, required=True)
    async def ask_question(self, ctx: SlashContext, question: str):
        try:
            openaiconversation, anthropicconversation = (
                await self._prepare_conversations(ctx, question)
            )

            openairesponse = await self._get_openai_response(
                openaiconversation, ctx, question
            )
            anthropicresponse = await self._get_anthropic_response(
                anthropicconversation, ctx, question
            )
            self.print_cost(openairesponse)
            self.print_cost(anthropicresponse)
            responses = self._create_responses(openairesponse, anthropicresponse)

            await self._send_response_message(ctx, question, responses)
        except CommandOnCooldown:
            await ctx.send(
                "La commande est en cooldown, veuillez réessayer plus tard",
                ephemeral=True,
            )

    async def _prepare_conversations(self, ctx: SlashContext, question: str):
        openaiconversation = []
        anthropicconversation = []
        messages = await ctx.channel.fetch_messages(limit=10)

        for message in messages:
            author_content = f"{message.author.display_name} : {message.content}"
            if message.author.id == self.bot.user.id:
                anthropicconversation.append(
                    {"role": "system", "content": message.content}
                )
                openaiconversation.append(
                    {"role": "assistant", "content": message.content}
                )
            else:
                anthropicconversation.append(
                    {"role": "user", "content": author_content}
                )
                openaiconversation.append({"role": "user", "content": author_content})

        openaiconversation.reverse()
        openaiconversation.append(
            {"role": "user", "content": f"{ctx.author.display_name} : {question}"}
        )
        anthropicconversation.append(
            {"role": "user", "content": f"{ctx.author.display_name} : {question}"}
        )

        return openaiconversation, anthropicconversation

    async def _get_openai_response(
        self, openaiconversation, ctx: SlashContext, question
    ):
        dictInfos = {}
        infos = [
            search_dict_by_sentence(dictInfos, question),
            search_dict_by_sentence(dictInfos, str(ctx.author.id)),
            f"Utilisateurs : {', '.join([f'username : {member.username} (Display name :{member.display_name}, ID : <@{member.id}>)' for member in ctx.guild.members])}",
        ]
        openaiconversation.append(
            {
                "role": "system",
                "content": (
                    f"Tu vas jouer le rôle de Michel·le, un assistant sarcastique aux idées de gauche, dans un chat Discord. "
                    f"Ton but est d'écrire une réponse au dernier message du chat, en restant dans le personnage de Michel "
                    f"de façon concise.\nVoici les 10 derniers messages du chat Discord :<messages>{openaiconversation}</messages>\n"
                    f"Et voici un dictionnaire d'informations complémentaires pour te donner plus de contexte : <info>{infos}</info>"
                    f"Lis attentivement les messages et les informations complémentaires pour bien comprendre le contexte de la conversation. "
                    f"Ensuite, rédige une réponse sarcastique au dernier message, comme le ferait Michel·le. N'hésite pas à utiliser l'humour et l'ironie, "
                    f"tout en restant dans les limites du raisonnable. Appuie-toi sur les éléments de contexte fournis pour rendre ta réponse pertinente. "
                    f"Rappelle-toi que tu dois rester dans le personnage de Michel·le tout au long de ta réponse. Son ton est caustique mais pas méchant. "
                ),
            }
        )
        return await self.openai_client.chat.completions.create(
            model="gpt-4o",
            max_tokens=300,
            messages=openaiconversation,
        )

    async def _get_anthropic_response(
        self, anthropicconversation, ctx: SlashContext, question
    ):
        dictInfos = {}
        infos = [
            search_dict_by_sentence(dictInfos, question),
            search_dict_by_sentence(dictInfos, str(ctx.author.id)),
            f"Utilisateurs : {', '.join([f'username : {member.username} (Display name :{member.display_name}, ID : <@{member.id}>)' for member in ctx.guild.members])}",
        ]

        return await self.anthropic_client.messages.create(
            model="claude-3-5-sonnet-20240620",
            temperature=0.7,
            max_tokens=300,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Tu vas jouer le rôle de Michel·le, un assistant sarcastique aux idées de gauche, dans un chat Discord. "
                                f"Ton but est d'écrire une réponse au dernier message du chat, en restant dans le personnage de Michel "
                                f"de façon concise.\nVoici les 10 derniers messages du chat Discord :<messages>{anthropicconversation}</messages>\n"
                                f"Et voici un dictionnaire d'informations complémentaires pour te donner plus de contexte : <info>{infos}</info>"
                                f"Lis attentivement les messages et les informations complémentaires pour bien comprendre le contexte de la conversation. "
                                f"Ensuite, rédige une réponse sarcastique au dernier message, comme le ferait Michel·le. N'hésite pas à utiliser l'humour et l'ironie, "
                                f"tout en restant dans les limites du raisonnable. Appuie-toi sur les éléments de contexte fournis pour rendre ta réponse pertinente. "
                                f"Rappelle-toi que tu dois rester dans le personnage de Michel·le tout au long de ta réponse. Son ton est caustique mais pas méchant. "
                                f"Écris ta réponse entre des balises <answer>."
                            ),
                        }
                    ],
                }
            ],
        )
    def _create_responses(self, openairesponse, anthropicresponse):
        responses = [
            {
                "custom_id": "openai",
                "content": openairesponse.choices[0].message.content,
            },
            {
                "custom_id": "anthropic",
                "content": extract_answer(anthropicresponse.content[0].text),
            },
        ]
        random.shuffle(responses)
        return responses

    async def _send_response_message(self, ctx: SlashContext, question: str, responses):
        message_content = (
            f"**{ctx.author.mention} : {question}**\n\n"
            f"Réponse 1 : \n```{responses[0]['content']}```\n"
            f"Réponse 2 : \n```{responses[1]['content']}```\n"
            f"Votez pour la meilleure réponse en cliquant sur le bouton correspondant"
        )
        components = [
            Button(
                label="Réponse 1",
                style=ButtonStyle.SECONDARY,
                custom_id=responses[0]["custom_id"],
            ),
            Button(
                label="Réponse 2",
                style=ButtonStyle.SECONDARY,
                custom_id=responses[1]["custom_id"],
            ),
        ]

        message_info = await ctx.send(message_content, components=components)
        await self._handle_vote(ctx, message_info, question, responses, components)

    async def _handle_vote(
            self, ctx: SlashContext, message_info, question: str, responses, components
        ):
            try:
                button_ctx: Component = await self.bot.wait_for_component(
                    components=components, timeout=60
                )
                if button_ctx.ctx.author_id != ctx.author.id:
                    await button_ctx.ctx.send(
                        "Vous n'avez pas le droit de voter sur ce message", ephemeral=True
                    )
                    return

                response = button_ctx.ctx.custom_id
                logger.info(f"Vote : {response}")
                self._save_response_to_file(response)

                # Identify the selected response
                selected_response = next((resp for resp in responses if resp["custom_id"] == response), None)
                if selected_response:
                    # Create new message content with only the selected response
                    new_message_content = (
                        f"**{ctx.author.mention} : {question}**\n{selected_response['content']}"
                    )
                    await message_info.edit(
                        content=new_message_content,
                        components=[],
                    )

                openai_votes, anthropic_votes = self._count_votes()
                logger.info(
                    f"Votes : OpenAI : {openai_votes}, Anthropic : {anthropic_votes}"
                )

                await button_ctx.ctx.send("Vote enregistré", ephemeral=True)
            except TimeoutError:
                # Keep the original behavior on timeout
                await message_info.edit(
                    content=f"**{ctx.author.mention} : {question}**\n\nRéponse 1 : \n```{responses[0]['content']}```\nRéponse 2 : \n```{responses[1]['content']}```",
                    components=[],
                )

    def print_cost(self, message):
        model_info = {
            "gpt-4o": {"input": 5 / 1e6, "output": 15 / 1e6},
            "gpt-4o-2024-05-13": {"input": 5 / 1e6, "output": 15 / 1e6},
            "gpt-4-turbo": {"input": 10 / 1e6, "output": 30 / 1e6},
            "gpt-4": {"input": 30 / 1e6, "output": 60 / 1e6},
            "gpt-4-32k": {"input": 60 / 1e6, "output": 120 / 1e6},
            "gpt-3.5-turbo": {"input": 0.5 / 1e6, "output": 1.5 / 1e6},
            "gpt-3.5-turbo-0125": {"input": 0.5 / 1e6, "output": 1.5 / 1e6},
            "claude-3-haiku-20240307": {"input": 0.25 / 1e6, "output": 0.5 / 1e6},
            "claude-3-5-sonnet-20240620": {"input": 3 / 1e6, "output": 15 / 1e6},
            "claude-3-sonnet-20240229": {"input": 3 / 1e6, "output": 15 / 1e6},
            "claude-3-opus-20240229": {"input": 3 / 1e6, "output": 75 / 1e6},
        }
        input_tokens = (
            getattr(getattr(message, "usage", None), "input_tokens", 0)
            or getattr(getattr(message, "usage", None), "prompt_tokens", 0)
            or 0
        )
        output_tokens = (
            getattr(getattr(message, "usage", None), "output_tokens", 0)
            or getattr(getattr(message, "usage", None), "completion_tokens", 0)
            or 0
        )
        if message.model in model_info:
            input_cost = model_info[message.model]["input"] * input_tokens
            output_cost = model_info[message.model]["output"] * output_tokens
            logger.info(
                "modèle :%s | coût : %.5f$ | %.5f$ (%d tks) in | %.5f$ (%d tks) out",
                message.model,
                input_cost + output_cost,
                input_cost,
                input_tokens,
                output_cost,
                output_tokens,
            )
        else:
            logger.info(
                "modèle :%s | coût : %.5f$ | %.5f$ (%d tks) in | %.5f$ (%d tks) out",
                message.model,
                0.0,
                0.0,
                input_tokens,
                0.0,
                output_tokens,
            )

    @staticmethod
    def _save_response_to_file(response):
        with open("data/responses.txt", "a") as f:
            f.write(f"{response}\n")

    @staticmethod
    def _count_votes():
        with open("data/responses.txt", "r") as f:
            votes = f.readlines()
            openai_votes = votes.count("openai\n")
            anthropic_votes = votes.count("anthropic\n")
            return openai_votes, anthropic_votes
    
    # Answer all DM messages using anthropic
    @listen()
    async def on_message(self, event: MessageCreate):
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
            messages = await event.message.channel.fetch_messages(limit=5)
            conversation = []
            for message in messages:
                author_content = f"{message.author.display_name} : {message.content}"
                if message.author.id == self.bot.user.id:
                    conversation.append(
                        {"role": "system", "content": message.content}
                    )
                else:
                    conversation.append({"role": "user", "content": author_content})
            conversation.reverse()
            response = await self.anthropic_client.messages.create(
                model="claude-3-5-sonnet-20240620",
                temperature=0.7,
                max_tokens=300,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"Tu vas jouer le rôle de Michel, un assistant sarcastique, dans un chat Discord. "
                                    f"Ton but est d'écrire une réponse au dernier message du chat, en restant dans le personnage de Michel "
                                    f"de façon concise.\nVoici les 5 derniers messages du chat Discord :<messages>{conversation}</messages>\n"
                                    f"Rédige une réponse sarcastique au dernier message, comme le ferait Michel. N'hésite pas à utiliser l'humour et l'ironie, "
                                    f"tout en restant dans les limites du raisonnable. Appuie-toi sur les éléments de contexte fournis pour rendre ta réponse pertinente. "
                                    f"Rappelle-toi que tu dois rester dans le personnage de Michel tout au long de ta réponse. Son ton est caustique mais pas méchant. "
                                    f"Écris ta réponse entre des balises <answer>."
                                ),
                            }
                        ],
                    }
                ],
            )
            await event.message.channel.send(extract_answer(response.content[0].text))
            self.print_cost(response)
            logger.info("Response : %s", extract_answer(response.content[0].text))

