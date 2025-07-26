import os
import random
import httpx
import re
import asyncio

from openai import AsyncOpenAI
from interactions import (
    Button,
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
    ButtonStyle,
)
from interactions.client.errors import CommandOnCooldown
from interactions.api.events import Component

from src import logutil
from src.utils import (
    load_config,
    search_dict_by_sentence,
)

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleIA")


class IAExtension(Extension):
    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.openrouter_client = None
        self.model_prices = {}
        # Configuration des modèles par défaut
        self.default_models = {
            "openai": "openai/gpt-4.1",
            "anthropic": "anthropic/claude-sonnet-4",
            "deepseek": "deepseek/deepseek-chat-v3-0324"
        }

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
        """Charge les prix des modèles depuis l'API OpenRouter avec retry et timeout"""
        max_retries = 3
        timeout = 10
        
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.get(
                        "https://openrouter.ai/api/v1/models",
                        headers={
                            "Authorization": f"Bearer {config['OpenRouter']['openrouterApiKey']}",
                            "HTTP-Referer": "https://discord.bot",
                            "X-Title": "Michel Discord Bot",
                        },
                    )
                    response.raise_for_status()  # Lever une exception pour les codes d'erreur HTTP
                    data = response.json()
                    
                    for model in data["data"]:
                        model_id = model["id"]
                        pricing = model.get("pricing", {})
                        if pricing.get("prompt") and pricing.get("completion"):
                            self.model_prices[model_id] = {
                                "input": float(pricing["prompt"]),
                                "output": float(pricing["completion"]),
                            }
                    
                    logger.info(f"Loaded pricing for {len(self.model_prices)} models from OpenRouter")
                    return  # Succès, sortir de la boucle
                    
            except httpx.TimeoutException:
                logger.warning(f"Timeout lors du chargement des prix (tentative {attempt + 1}/{max_retries})")
            except httpx.HTTPStatusError as e:
                logger.error(f"Erreur HTTP lors du chargement des prix: {e.response.status_code}")
                break  # Ne pas retry sur erreur HTTP
            except Exception as e:
                logger.error(f"Erreur lors du chargement des prix (tentative {attempt + 1}/{max_retries}): {e}")
                
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)  # Backoff exponentiel
        
        logger.warning("Impossible de charger les prix des modèles, utilisation des prix par défaut")

    @slash_command(
        name="ask", description="Ask Michel and vote for the better answer"
    )
    @cooldown(Buckets.USER, 1, 20)
    @auto_defer()
    @slash_option("question", "Ta question", opt_type=OptionType.STRING, required=True)
    async def ask_question(self, ctx: SlashContext, question: str):
        if not self.openrouter_client:
            await ctx.send("❌ Le client OpenRouter n'est pas initialisé", ephemeral=True)
            return
            
        try:
            conversation = await self._prepare_conversation(ctx, question)

            # Obtenir les réponses des trois modèles avec gestion d'erreur individuelle
            responses = {}
            models = {
                "openai": "openai/gpt-4.1",
                "anthropic": "anthropic/claude-sonnet-4", 
                "deepseek": "deepseek/deepseek-chat-v3-0324"
            }
            
            for provider, model in models.items():
                try:
                    response = await self._get_model_response(conversation, model, ctx, question)
                    responses[provider] = response
                except Exception as e:
                    logger.error(f"Erreur lors de l'appel au modèle {provider}: {e}")
                    await ctx.send(f"❌ Erreur avec le modèle {provider}: {str(e)}", ephemeral=True)
                    return

            openai_response = responses["openai"]
            anthropic_response = responses["anthropic"] 
            deepseek_response = responses["deepseek"]

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
        if not self.openrouter_client:
            raise RuntimeError("OpenRouter client not initialized")
            
        # Extraire des informations pertinentes sur le contexte avec validation
        server_name = ctx.guild.name if ctx.guild else "DM"
        channel_name = ctx.channel.name if hasattr(ctx.channel, 'name') else "canal-inconnu"
        author = ctx.author
        
        # Obtenir des informations sur les utilisateurs impliqués dans la conversation
        mentioned_users = []
        mentioned_user_ids = set()
        
        # Extraire les utilisateurs mentionnés dans la conversation ou la question
        pattern = r'<@!?(\d+)>'
        
        # Ajouter l'auteur de la question
        mentioned_users.append({
            "id": author.id,
            "username": author.username,
            "display_name": author.display_name,
            "is_author": True
        })
        mentioned_user_ids.add(author.id)
        
        # Analyser la conversation pour trouver les utilisateurs mentionnés
        for msg in conversation:
            if msg["role"] == "user":
                # Extraire l'ID de l'utilisateur qui parle (format: "nom : message")
                user_parts = msg["content"].split(" : ", 1)
                if len(user_parts) > 1:
                    # Trouver les utilisateurs mentionnés dans le message
                    mentions = re.findall(pattern, msg["content"])
                    for user_id in mentions:
                        try:
                            user_id = int(user_id)
                            if user_id not in mentioned_user_ids:
                                user = await self.bot.fetch_user(user_id)
                                if user:
                                    mentioned_users.append({
                                        "id": user.id,
                                        "username": user.username,
                                        "display_name": user.display_name,
                                        "is_author": user.id == author.id
                                    })
                                    mentioned_user_ids.add(user.id)
                        except (ValueError, Exception) as e:
                            logger.warning(f"Erreur lors de la récupération de l'utilisateur {user_id}: {e}")
        
        # Rechercher des informations contextuelles pertinentes
        dictInfos = {}  # À implémenter selon les besoins
        context_info = search_dict_by_sentence(dictInfos, question)

        messages = conversation.copy()
        messages.append(
            {
                "role": "system",
                "content": (
                    f"# Rôle et contexte\n"
                    f"Tu es Michel·le, un assistant Discord sarcastique et impertinent avec des idées de gauche. "
                    f"Tu es connu pour ton humour caustique mais jamais cruel, et ta façon unique de répondre aux questions.\n\n"
                    
                    f"# Contexte de la conversation\n"
                    f"- Serveur: {server_name}\n"
                    f"- Canal: {channel_name}\n"
                    f"- Question posée par: {author.display_name} ({author.username})\n\n"
                    
                    f"# Informations sur les personnes impliquées dans la conversation\n"
                    f"{', '.join([f'{u['display_name']} ({u['username']})' + (' (auteur de la question)' if u['is_author'] else '') for u in mentioned_users[:5]])}\n\n"
                    
                    f"# Consignes\n"
                    f"1. Réponds au dernier message du chat avec le ton sarcastique caractéristique de Michel·le\n"
                    f"2. Sois concis et direct dans tes réponses\n"
                    f"3. Utilise l'humour et l'ironie quand c'est approprié\n"
                    f"4. N'expose tes idées politiques que si cela est pertinent pour la question\n"
                    f"5. Reste dans le personnage de Michel·le tout au long de ta réponse\n\n"
                    
                    f"# Style de réponse\n"
                    f"- Ton sarcastique et un peu provocateur\n"
                    f"- Direct et sans détour\n"
                    f"- Utilise parfois des expressions familières appropriées\n"
                    f"- N'hésite pas à remettre en question les présupposés quand nécessaire\n\n"
                    
                    f"# Informations contextuelles complémentaires\n"
                    f"{context_info if context_info else 'Aucune information contextuelle supplémentaire disponible.'}"
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

    def _get_model_display_name(self, model_id):
        """Convertit l'ID du modèle en nom d'affichage lisible"""
        model_names = {
            "openai": "OpenAI GPT-4.1",
            "anthropic": "Anthropic Claude 4 Sonnet",
            "deepseek": "DeepSeek Chat v3-0324"
        }
        return model_names.get(model_id, model_id)

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
            logger.info(f"Vote enregistré : {response}")
            self._save_response_to_file(response)

            # Identifier la réponse sélectionnée
            selected_response = next((resp for resp in responses if resp["custom_id"] == response), None)
            if selected_response:
                # Obtenir le nom du modèle pour l'affichage
                model_display_name = self._get_model_display_name(response)
                # Créer le nouveau contenu du message avec seulement la réponse sélectionnée
                new_message_content = (
                    f"**{ctx.author.mention} : {question}**\n\n"
                    f"**Réponse choisie ({model_display_name}) :**\n{selected_response['content']}"
                )
                # Utiliser la méthode de division pour le message édité
                try:
                    if len(new_message_content) <= 2000:
                        await message_info.edit(
                            content=new_message_content,
                            components=[],
                        )
                    else:
                        # Si le message édité est trop long, supprimer l'ancien et envoyer un nouveau message divisé
                        await message_info.delete()
                        await self._split_and_send_message(ctx, new_message_content)
                except Exception as edit_error:
                    logger.error(f"Erreur lors de l'édition du message: {edit_error}")
                    # Fallback: envoyer un nouveau message
                    await ctx.send(f"✅ Vote enregistré pour {response}")

            openai_votes, anthropic_votes, deepseek_votes = self._count_votes()
            logger.info(
                f"Total des votes - OpenAI: {openai_votes}, Anthropic: {anthropic_votes}, Deepseek: {deepseek_votes}"
            )

            await button_ctx.ctx.send("✅ Vote enregistré avec succès", ephemeral=True)
            
        except TimeoutError:
            # En cas de timeout, éditer le message existant pour retirer les boutons et afficher les modèles
            try:
                # Créer le contenu du message avec les noms des modèles
                timeout_content = (
                    f"**{ctx.author.mention} : {question}**\n\n"
                    f"**Réponse 1 ({self._get_model_display_name(responses[0]['custom_id'])}) :** \n> {responses[0]['content'].replace('\n', '\n> ')}\n\n"
                    f"**Réponse 2 ({self._get_model_display_name(responses[1]['custom_id'])}) :** \n> {responses[1]['content'].replace('\n', '\n> ')}\n\n"
                    f"**Réponse 3 ({self._get_model_display_name(responses[2]['custom_id'])}) :** \n> {responses[2]['content'].replace('\n', '\n> ')}\n\n"
                    f"⏰ *Temps de vote expiré*"
                )
                
                if len(timeout_content) <= 2000:
                    await message_info.edit(content=timeout_content, components=[])
                else:
                    # Si le message est trop long, supprimer l'ancien et envoyer un nouveau message divisé
                    await message_info.delete()
                    await self._split_and_send_message(ctx, timeout_content)
                    
                logger.info("Timeout de vote - Boutons supprimés et modèles affichés")
            except Exception as e:
                logger.error(f"Erreur lors de la gestion du timeout : {e}")
        except Exception as e:
            logger.error(f"Erreur inattendue lors du vote: {e}")
            try:
                await ctx.send("❌ Erreur lors du traitement du vote", ephemeral=True)
            except Exception:
                pass  # Éviter les erreurs en cascade

    def calculate_cost(self, message):
        """Calcule le coût d'une réponse avec validation des données"""
        if not hasattr(message, 'usage') or not message.usage:
            logger.warning("Informations d'usage manquantes dans la réponse")
            return 0.0
            
        input_tokens = getattr(message.usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(message.usage, "completion_tokens", 0) or 0
        
        model_id = getattr(message, 'model', 'unknown')
        
        if model_id in self.model_prices:
            try:
                input_cost = self.model_prices[model_id]["input"] * input_tokens
                output_cost = self.model_prices[model_id]["output"] * output_tokens
                return input_cost + output_cost
            except (KeyError, TypeError) as e:
                logger.error(f"Erreur lors du calcul du coût pour {model_id}: {e}")
                return 0.0
        else:
            logger.warning(f"Prix non trouvé pour le modèle {model_id}")
            return 0.0

    def print_cost(self, message):
        """Affiche le coût d'une réponse avec validation des données"""
        if not hasattr(message, 'usage') or not message.usage:
            logger.info(f"modèle : {getattr(message, 'model', 'unknown')} | coût : informations d'usage manquantes")
            return
            
        input_tokens = getattr(message.usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(message.usage, "completion_tokens", 0) or 0
        model_id = getattr(message, 'model', 'unknown')
        
        if model_id in self.model_prices:
            try:
                input_cost = self.model_prices[model_id]["input"] * input_tokens
                output_cost = self.model_prices[model_id]["output"] * output_tokens
                total_cost = input_cost + output_cost
                
                logger.info(
                    "modèle : %s | coût : %.5f$ | %.5f$ (%d tks) in | %.5f$ (%d tks) out",
                    model_id,
                    total_cost,
                    input_cost,
                    input_tokens,
                    output_cost,
                    output_tokens,
                )
            except (KeyError, TypeError) as e:
                logger.error(f"Erreur lors du calcul du coût pour {model_id}: {e}")
        else:
            logger.info(
                "modèle : %s | coût : inconnu | %d tks in | %d tks out",
                model_id,
                input_tokens,
                output_tokens,
            )

    @staticmethod
    def _save_response_to_file(response):
        """Sauvegarde la réponse dans un fichier avec gestion d'erreur"""
        try:
            # Créer le dossier data s'il n'existe pas
            os.makedirs("data", exist_ok=True)
            with open("data/responses.txt", "a", encoding='utf-8') as f:
                f.write(f"{response}\n")
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde du vote: {e}")

    @staticmethod
    def _count_votes():
        """Compte les votes avec gestion d'erreur et création du fichier si nécessaire"""
        try:
            # Créer le dossier et fichier s'ils n'existent pas
            os.makedirs("data", exist_ok=True)
            if not os.path.exists("data/responses.txt"):
                return 0, 0, 0
                
            with open("data/responses.txt", "r", encoding='utf-8') as f:
                votes = f.readlines()
                openai_votes = votes.count("openai\n")
                anthropic_votes = votes.count("anthropic\n")
                deepseek_votes = votes.count("deepseek\n")
                return openai_votes, anthropic_votes, deepseek_votes
        except Exception as e:
            logger.error(f"Erreur lors du comptage des votes: {e}")
            return 0, 0, 0

