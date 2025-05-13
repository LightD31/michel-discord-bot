import os
import random
import httpx
import re

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
        self.openrouter_client = None
        self.model_prices = {}

    @listen()
    async def on_startup(self):
        self.openrouter_client = AsyncOpenAI(
            api_key=config["OpenRouter"]["openrouterApiKey"],
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://discord.bot",
                "X-Title": "Michel Discord Bot",
            },
        )
        # Précharger les informations de prix des modèles
        await self._load_model_prices()

    async def _load_model_prices(self):
        """Charge les prix des modèles depuis l'API OpenRouter"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={
                        "Authorization": f"Bearer {config['OpenRouter']['openrouterApiKey']}",
                        "HTTP-Referer": "https://discord.bot",
                        "X-Title": "Michel Discord Bot",
                    },
                )
                data = response.json()
                
                for model in data["data"]:
                    model_id = model["id"]
                    self.model_prices[model_id] = {
                        "input": float(model["pricing"]["prompt"]),
                        "output": float(model["pricing"]["completion"]),
                    }
                logger.info(f"Loaded pricing for {len(self.model_prices)} models from OpenRouter")
        except Exception as e:
            logger.error(f"Error loading model prices: {e}")
            # Fallback vers les prix par défaut si l'API échoue

    @slash_command(
        name="ask", description="Ask Michel and vote for the better answer"
    )
    @cooldown(Buckets.USER, 1, 20)
    @auto_defer()
    @slash_option("question", "Ta question", opt_type=OptionType.STRING, required=True)
    async def ask_question(self, ctx: SlashContext, question: str):
        try:
            conversation = await self._prepare_conversation(ctx, question)

            # Obtenir les réponses des trois modèles
            openai_response = await self._get_model_response(
                conversation, "openai/gpt-4.1", ctx, question
            )
            anthropic_response = await self._get_model_response(
                conversation, "anthropic/claude-3.7-sonnet", ctx, question
            )
            deepseek_response = await self._get_model_response(
                conversation, "deepseek/deepseek-chat-v3-0324", ctx, question
            )

            # Calculer le coût total de la commande
            total_cost = 0
            openai_cost = self.calculate_cost(openai_response)
            anthropic_cost = self.calculate_cost(anthropic_response)
            deepseek_cost = self.calculate_cost(deepseek_response)
            total_cost = openai_cost + anthropic_cost + deepseek_cost

            # Afficher les coûts individuels
            self.print_cost(openai_response)
            self.print_cost(anthropic_response)
            self.print_cost(deepseek_response)
            
            # Afficher le coût total
            logger.info(f"Coût total de la commande : {total_cost:.5f}$")

            responses = self._create_responses(
                {"custom_id": "openai", "content": openai_response.choices[0].message.content},
                {"custom_id": "anthropic", "content": anthropic_response.choices[0].message.content},
                {"custom_id": "deepseek", "content": deepseek_response.choices[0].message.content},
            )

            await self._send_response_message(ctx, question, responses)
        except CommandOnCooldown:
            await ctx.send(
                "La commande est en cooldown, veuillez réessayer plus tard",
                ephemeral=True,
            )

    async def _prepare_conversation(self, ctx: SlashContext, question: str):
        conversation = []
        messages = await ctx.channel.fetch_messages(limit=10)

        for message in messages:
            author_content = f"{message.author.display_name} : {message.content}"
            if message.author.id == self.bot.user.id:
                conversation.append({"role": "assistant", "content": message.content})
            else:
                conversation.append({"role": "user", "content": author_content})

        conversation.reverse()
        conversation.append({"role": "user", "content": f"{ctx.author.display_name} : {question}"})

        return conversation

    async def _get_model_response(self, conversation, model, ctx: SlashContext, question):
        dictInfos = {}
        infos = [
            search_dict_by_sentence(dictInfos, question),
            search_dict_by_sentence(dictInfos, str(ctx.author.id)),
            f"Utilisateurs : {', '.join([f'username : {member.username} (Display name :{member.display_name}, ID : <@{member.id}>)' for member in ctx.guild.members])}",
        ]

        messages = conversation.copy()
        messages.append(
            {
                "role": "system",
                "content": (
                    f"Tu vas jouer le rôle de Michel·le, un assistant sarcastique aux idées de gauche, dans un chat Discord. Tu exposeras ta vision politique seulement si cela est pertinent. "
                    f"Ton but est d'écrire une réponse au dernier message du chat, en restant dans le personnage de Michel "
                    f"de façon concise.\nVoici les 10 derniers messages du chat Discord :<messages>{conversation}</messages>\n"
                    f"Et voici un dictionnaire d'informations complémentaires pour te donner plus de contexte : <info>{infos}</info>"
                    f"Lis attentivement les messages et les informations complémentaires pour bien comprendre le contexte de la conversation. "
                    f"Ensuite, rédige une réponse sarcastique au dernier message, comme le ferait Michel·le. N'hésite pas à utiliser l'humour et l'ironie, "
                    f"tout en restant dans les limites du raisonnable. Appuie-toi sur les éléments de contexte fournis pour rendre ta réponse pertinente. "
                    f"Rappelle-toi que tu dois rester dans le personnage de Michel·le tout au long de ta réponse. Son ton est caustique mais pas méchant. "
                ),
            }
        )

        # Ajout du paramètre usage.include pour obtenir les comptages de tokens
        return await self.openrouter_client.chat.completions.create(
            model=model,
            max_tokens=300,
            messages=messages,
            extra_body={"usage.include": True}
        )

    def _create_responses(self, openai_response, anthropic_response, deepseek_response):
        responses = [openai_response, anthropic_response, deepseek_response]
        random.shuffle(responses)
        return responses

    async def _split_and_send_message(self, ctx_or_channel, content, components=None):
        """
        Divise un message si sa longueur dépasse 2000 caractères et l'envoie en plusieurs parties.
        Utilise une approche simple pour garantir que chaque message respecte la limite de Discord.
        
        Args:
            ctx_or_channel: Le contexte ou le canal où envoyer le message
            content: Le contenu du message
            components: Les composants à ajouter (seulement au dernier message)
            
        Returns:
            Le dernier message envoyé
        """
        if len(content) <= 1900:  # Marge de sécurité
            # Si le message est suffisamment court, l'envoyer directement
            return await ctx_or_channel.send(content, components=components)
        
        # Diviser le message en parties de moins de 1900 caractères
        messages = []
        current_message = ""
        
        # Diviser par paragraphes pour essayer de préserver la structure
        paragraphs = content.split('\n\n')
        
        for paragraph in paragraphs:
            # Si le paragraphe lui-même est trop long, le diviser
            if len(paragraph) > 1900:
                # Si on a déjà du contenu dans le message actuel, l'ajouter aux messages
                if current_message:
                    messages.append(current_message)
                    current_message = ""
                
                # Diviser le paragraphe en morceaux
                while len(paragraph) > 0:
                    # Trouver un bon endroit pour couper, de préférence à une fin de ligne
                    cut_index = 1900
                    if len(paragraph) > cut_index:
                        # Chercher un retour à la ligne avant la limite
                        newline_index = paragraph[:cut_index].rfind('\n')
                        if newline_index > 0:
                            cut_index = newline_index + 1
                        else:
                            # Si pas de retour à la ligne, chercher un espace
                            space_index = paragraph[:cut_index].rfind(' ')
                            if space_index > cut_index - 100:  # Ne pas couper trop loin en arrière
                                cut_index = space_index + 1
                    
                    messages.append(paragraph[:cut_index])
                    paragraph = paragraph[cut_index:]
            else:
                # Vérifier si l'ajout de ce paragraphe dépasserait la limite
                if len(current_message) + len(paragraph) + 2 > 1900:  # +2 pour \n\n
                    messages.append(current_message)
                    current_message = paragraph
                else:
                    if current_message:
                        current_message += "\n\n" + paragraph
                    else:
                        current_message = paragraph
        
        # Ajouter le dernier message s'il n'est pas vide
        if current_message:
            messages.append(current_message)
        
        # Vérification de sécurité: s'assurer qu'aucun message ne dépasse 1900 caractères
        for i, msg in enumerate(messages):
            if len(msg) > 1900:
                # Diviser en deux simplement
                half = len(msg) // 2
                # Trouver un bon point de coupure
                cut_index = msg[:half].rfind('\n')
                if cut_index < 0 or cut_index < half - 200:
                    cut_index = msg[:half].rfind(' ')
                if cut_index < 0:
                    cut_index = half
                
                messages[i] = msg[:cut_index]
                messages.insert(i + 1, msg[cut_index:])
        
        # Log pour déboguer
        for i, msg in enumerate(messages):
            logger.debug(f"Message part {i+1}: length={len(msg)}")
        
        # Envoyer les messages en séquence
        last_message = None
        for i, msg_content in enumerate(messages):
            try:
                # Ajouter les composants uniquement au dernier message
                if i == len(messages) - 1:
                    last_message = await ctx_or_channel.send(msg_content, components=components)
                else:
                    last_message = await ctx_or_channel.send(msg_content)
            except Exception as e:
                logger.error(f"Erreur lors de l'envoi de la partie {i+1} ({len(msg_content)} caractères): {e}")
                # Essayer d'envoyer un message plus court en cas d'échec
                if len(msg_content) > 1000:
                    await ctx_or_channel.send(msg_content[:1000] + "... (message tronqué)")
        
        return last_message

    async def _send_response_message(self, ctx: SlashContext, question: str, responses):
        message_content = (
            f"**{ctx.author.mention} : {question}**\n\n"
            f"Réponse 1 : \n> {responses[0]['content'].replace('\n', '\n> ')}\n\n"
            f"Réponse 2 : \n> {responses[1]['content'].replace('\n', '\n> ')}\n\n"
            f"Réponse 3 : \n> {responses[2]['content'].replace('\n', '\n> ')}\n\n"
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
            Button(
                label="Réponse 3",
                style=ButtonStyle.SECONDARY,
                custom_id=responses[2]["custom_id"],
            ),
        ]

        message_info = await self._split_and_send_message(ctx, message_content, components=components)
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
                # Utiliser la méthode de division pour le message édité
                if len(new_message_content) <= 2000:
                    await message_info.edit(
                        content=new_message_content,
                        components=[],
                    )
                else:
                    # Si le message édité est trop long, supprimer l'ancien et envoyer un nouveau message divisé
                    await message_info.delete()
                    await self._split_and_send_message(ctx, new_message_content)

            openai_votes, anthropic_votes, deepseek_votes = self._count_votes()
            logger.info(
                f"Votes : OpenAI : {openai_votes}, Anthropic : {anthropic_votes}, Deepseek : {deepseek_votes}"
            )

            await button_ctx.ctx.send("Vote enregistré", ephemeral=True)
        except TimeoutError:
            # En cas de timeout, éditer le message existant pour retirer les boutons
            try:
                # Simplement supprimer les boutons mais conserver le contenu du message original
                await message_info.edit(components=[])
                logger.info("Timeout de vote - Boutons supprimés")
            except Exception as e:
                logger.error(f"Erreur lors de la gestion du timeout : {e}")
                # Ne pas envoyer de nouveau message en cas d'erreur

    # Ajouter une nouvelle méthode pour calculer le coût d'une réponse
    def calculate_cost(self, message):
        input_tokens = getattr(getattr(message, "usage", None), "prompt_tokens", 0) or 0
        output_tokens = getattr(getattr(message, "usage", None), "completion_tokens", 0) or 0
        
        model_id = message.model
        
        if model_id in self.model_prices:
            input_cost = self.model_prices[model_id]["input"] * input_tokens
            output_cost = self.model_prices[model_id]["output"] * output_tokens
            return input_cost + output_cost
        else:
            # Si le modèle n'est pas dans les prix récupérés depuis l'API, retourner 0
            logger.warning(f"Prix non trouvé pour le modèle {model_id}, utilisation de 0$")
            return 0

    def print_cost(self, message):
        # Récupérer le comptage de tokens à partir de la réponse
        input_tokens = getattr(getattr(message, "usage", None), "prompt_tokens", 0) or 0
        output_tokens = getattr(getattr(message, "usage", None), "completion_tokens", 0) or 0
        
        # Calculer le coût en utilisant les prix du modèle depuis l'API
        model_id = message.model
        
        if model_id in self.model_prices:
            # Utiliser les prix récupérés de l'API
            input_cost = self.model_prices[model_id]["input"] * input_tokens
            output_cost = self.model_prices[model_id]["output"] * output_tokens
            total_cost = input_cost + output_cost
            
            # Afficher le coût 
            logger.info(
                "modèle :%s | coût : %.5f$ | %.5f$ (%d tks) in | %.5f$ (%d tks) out",
                model_id,
                total_cost,
                input_cost,
                input_tokens,
                output_cost,
                output_tokens,
            )
        else:
            # Afficher un message lorsque les informations de prix ne sont pas disponibles
            logger.info(
                "modèle :%s | coût : inconnu | %d tks in | %d tks out",
                model_id,
                input_tokens,
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
            deepseek_votes = votes.count("deepseek\n")
            return openai_votes, anthropic_votes, deepseek_votes

    # Answer all DM messages using openrouter
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
                    conversation.append({"role": "assistant", "content": message.content})
                else:
                    conversation.append({"role": "user", "content": author_content})
            conversation.reverse()
            
            # Ajouter le message système et les messages utilisateur
            dm_messages = [
                {
                    "role": "system",
                    "content": (
                        f"Tu vas jouer le rôle de Michel, un assistant sarcastique, dans un chat Discord. "
                        f"Ton but est d'écrire une réponse au dernier message du chat, en restant dans le personnage de Michel "
                        f"de façon concise.\nVoici les 5 derniers messages du chat Discord :<messages>{conversation}</messages>\n"
                        f"Rédige une réponse sarcastique au dernier message, comme le ferait Michel. N'hésite pas à utiliser l'humour et l'ironie, "
                        f"tout en restant dans les limites du raisonnable. Appuie-toi sur les éléments de contexte fournis pour rendre ta réponse pertinente. "
                        f"Rappelle-toi que tu dois rester dans le personnage de Michel tout au long de ta réponse. Son ton est caustique mais pas méchant."
                    ),
                }
            ] + conversation
            
            # Utiliser le modèle de l'OpenRouter pour les DMs avec le paramètre usage.include
            response = await self.openrouter_client.chat.completions.create(
                model="anthropic/claude-3-5-sonnet",
                temperature=0.7,
                max_tokens=300,
                messages=dm_messages,
                extra_body={"usage.include": True }
            )
            
            # Utiliser la méthode de division pour les messages de DM
            await self._split_and_send_message(event.message.channel, response.choices[0].message.content)
            self.print_cost(response)
            logger.info("Response : %s", response.choices[0].message.content)

