"""
CompareAI Extension - Compare responses from multiple AI models.

This extension allows users to ask questions and receive responses from
multiple AI models, then vote for the best response.
"""

import asyncio
import os
import random
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx
from interactions import (
    Button,
    Buckets,
    ButtonStyle,
    Client,
    Extension,
    IntegrationType,
    OptionType,
    SlashContext,
    auto_defer,
    cooldown,
    listen,
    slash_command,
    slash_option,
)
from interactions.api.events import Component
from interactions.client.errors import CommandOnCooldown
from openai import AsyncOpenAI

from src import logutil
from src.utils import load_config, search_dict_by_sentence

# =============================================================================
# Configuration and Constants
# =============================================================================

logger = logutil.init_logger(os.path.basename(__file__))
config, module_config, enabled_servers = load_config("moduleIA")

# Discord message character limit with safety margin
DISCORD_MESSAGE_LIMIT = 1900
VOTE_TIMEOUT_SECONDS = 60
MAX_API_RETRIES = 3
API_TIMEOUT_SECONDS = 10
MAX_RESPONSE_TOKENS = 500
CONVERSATION_HISTORY_LIMIT = 10
DATA_DIR = Path("data")
VOTES_FILE = DATA_DIR / "responses.txt"


class AIProvider(Enum):
    """Enum representing supported AI providers."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    GEMINI = "gemini"
    XAI = "xai"




@dataclass
class ModelConfig:
    """Configuration for an AI model."""
    provider: AIProvider
    model_id: str
    display_name: str


# Available AI models configuration
AVAILABLE_MODELS: dict[AIProvider, ModelConfig] = {
    AIProvider.OPENAI: ModelConfig(
        AIProvider.OPENAI,
        "openai/gpt-4.1",
        "OpenAI GPT-4.1"
    ),
    AIProvider.ANTHROPIC: ModelConfig(
        AIProvider.ANTHROPIC,
        "anthropic/claude-opus-4.5",
        "Anthropic Claude Opus 4.5"
    ),
    AIProvider.DEEPSEEK: ModelConfig(
        AIProvider.DEEPSEEK,
        "deepseek/deepseek-chat-v3-0324",
        "DeepSeek Chat v3-0324"
    ),
    AIProvider.QWEN: ModelConfig(
        AIProvider.QWEN,
        "qwen/qwen3-vl-235b-a22b-instruct",
        "Qwen3 235B A22B Instruct"
    ),
    AIProvider.GEMINI: ModelConfig(
        AIProvider.GEMINI,
        "google/gemini-3-pro-preview",
        "Google Gemini 3 Pro Preview"
    ),
    AIProvider.XAI: ModelConfig(
        AIProvider.XAI,
        "x-ai/grok-4.1-fast:free",
        "X-AI Grok 4.1 Fast"
    ),
}

# Number of models to compare per question
MODELS_TO_COMPARE = 3


@dataclass
class ModelResponse:
    """Container for a model's response."""
    provider: AIProvider
    content: str
    raw_response: object = None


@dataclass
class UserInfo:
    """Information about a user in the conversation."""
    user_id: int
    username: str
    display_name: str
    is_author: bool = False


@dataclass
class ModelPricing:
    """Pricing information for a model."""
    input_cost_per_token: float
    output_cost_per_token: float
    
    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate total cost for the given token counts."""
        return (self.input_cost_per_token * input_tokens + 
                self.output_cost_per_token * output_tokens)


# =============================================================================
# Vote Manager
# =============================================================================

class VoteManager:
    """Handles vote storage and counting."""
    
    def __init__(self, votes_file: Path = VOTES_FILE):
        self.votes_file = votes_file
        self._ensure_data_dir()
    
    def _ensure_data_dir(self) -> None:
        """Ensure the data directory exists."""
        self.votes_file.parent.mkdir(parents=True, exist_ok=True)
    
    def save_vote(self, provider: str) -> bool:
        """Save a vote to the file. Returns True on success."""
        try:
            with open(self.votes_file, "a", encoding="utf-8") as f:
                f.write(f"{provider}\n")
            return True
        except OSError as e:
            logger.error(f"Error saving vote: {e}")
            return False
    
    def count_votes(self) -> dict[str, int]:
        """Count all votes by provider."""
        counts = {provider.value: 0 for provider in AIProvider}
        
        if not self.votes_file.exists():
            return counts
        
        try:
            with open(self.votes_file, "r", encoding="utf-8") as f:
                for line in f:
                    provider = line.strip()
                    if provider in counts:
                        counts[provider] += 1
        except OSError as e:
            logger.error(f"Error counting votes: {e}")
        
        return counts


# =============================================================================
# Message Utilities
# =============================================================================

class MessageSplitter:
    """Utility for splitting long Discord messages."""
    
    @staticmethod
    def split_message(content: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
        """
        Split a message into chunks that fit within Discord's limit.
        
        Attempts to split at paragraph boundaries, then line breaks, then spaces.
        """
        if len(content) <= limit:
            return [content]
        
        messages = []
        paragraphs = content.split("\n\n")
        current_chunk = ""
        
        for paragraph in paragraphs:
            if len(paragraph) > limit:
                # Handle oversized paragraphs
                if current_chunk:
                    messages.append(current_chunk)
                    current_chunk = ""
                messages.extend(MessageSplitter._split_long_text(paragraph, limit))
            elif len(current_chunk) + len(paragraph) + 2 > limit:
                # Current chunk would exceed limit
                messages.append(current_chunk)
                current_chunk = paragraph
            else:
                # Add to current chunk
                current_chunk = f"{current_chunk}\n\n{paragraph}" if current_chunk else paragraph
        
        if current_chunk:
            messages.append(current_chunk)
        
        return messages
    
    @staticmethod
    def _split_long_text(text: str, limit: int) -> list[str]:
        """Split text that exceeds the limit."""
        chunks = []
        
        while text:
            if len(text) <= limit:
                chunks.append(text)
                break
            
            # Find best split point
            cut_index = MessageSplitter._find_split_point(text, limit)
            chunks.append(text[:cut_index])
            text = text[cut_index:].lstrip()
        
        return chunks
    
    @staticmethod
    def _find_split_point(text: str, limit: int) -> int:
        """Find the best point to split text."""
        # Try to split at newline
        newline_idx = text[:limit].rfind("\n")
        if newline_idx > limit // 2:
            return newline_idx + 1
        
        # Try to split at space
        space_idx = text[:limit].rfind(" ")
        if space_idx > limit - 100:
            return space_idx + 1
        
        # Fall back to hard limit
        return limit


# =============================================================================
# Main Extension Class
# =============================================================================

class IAExtension(Extension):
    """Discord extension for comparing AI model responses."""
    
    def __init__(self, bot: Client):
        self.bot: Client = bot
        self.openrouter_client: Optional[AsyncOpenAI] = None
        self.model_prices: dict[str, ModelPricing] = {}
        self.vote_manager = VoteManager()
        self.message_splitter = MessageSplitter()

    @listen()
    async def on_startup(self) -> None:
        """Initialize the OpenRouter client on bot startup."""
        self.openrouter_client = AsyncOpenAI(
            api_key=config["OpenRouter"]["openrouterApiKey"],
            base_url="https://openrouter.ai/api/v1",
            default_headers={
                "HTTP-Referer": "https://discord.bot",
                "X-Title": "Michel Discord Bot",
            },
        )
        await self._load_model_prices()

    async def _load_model_prices(self) -> None:
        """Load model prices from OpenRouter API with retry and exponential backoff."""
        for attempt in range(MAX_API_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=API_TIMEOUT_SECONDS) as client:
                    response = await client.get(
                        "https://openrouter.ai/api/v1/models",
                        headers={
                            "Authorization": f"Bearer {config['OpenRouter']['openrouterApiKey']}",
                            "HTTP-Referer": "https://discord.bot",
                            "X-Title": "Michel Discord Bot",
                        },
                    )
                    response.raise_for_status()
                    data = response.json()
                    
                    self._parse_model_prices(data)
                    logger.info(f"Loaded pricing for {len(self.model_prices)} models")
                    return
                    
            except httpx.TimeoutException:
                logger.warning(f"Timeout loading prices (attempt {attempt + 1}/{MAX_API_RETRIES})")
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error loading prices: {e.response.status_code}")
                break  # Don't retry on HTTP errors
            except Exception as e:
                logger.error(f"Error loading prices (attempt {attempt + 1}/{MAX_API_RETRIES}): {e}")
                
            if attempt < MAX_API_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
        
        logger.warning("Could not load model prices, using defaults")

    def _parse_model_prices(self, data: dict) -> None:
        """Parse model pricing data from API response."""
        for model in data.get("data", []):
            model_id = model.get("id")
            pricing = model.get("pricing", {})
            
            if model_id and pricing.get("prompt") and pricing.get("completion"):
                self.model_prices[model_id] = ModelPricing(
                    input_cost_per_token=float(pricing["prompt"]),
                    output_cost_per_token=float(pricing["completion"]),
                )

    @slash_command(
        name="ask",
        description="Ask Michel and vote for the better answer",
        integration_types=[IntegrationType.GUILD_INSTALL, IntegrationType.USER_INSTALL],
    )
    @cooldown(Buckets.USER, 1, 20)
    @auto_defer()
    @slash_option("question", "Ta question", opt_type=OptionType.STRING, required=True)
    async def ask_question(self, ctx: SlashContext, question: str) -> None:
        """Main command to ask a question and compare AI responses."""
        if not self.openrouter_client:
            await ctx.send("❌ Le client OpenRouter n'est pas initialisé", ephemeral=True)
            return
            
        try:
            await self._process_question(ctx, question)
        except CommandOnCooldown:
            await ctx.send(
                "La commande est en cooldown, veuillez réessayer plus tard",
                ephemeral=True,
            )
        except Exception as e:
            logger.error(f"Unexpected error in ask_question: {e}")
            await ctx.send("❌ Une erreur inattendue s'est produite", ephemeral=True)

    async def _process_question(self, ctx: SlashContext, question: str) -> None:
        """Process a question by getting responses from multiple models."""
        conversation = await self._prepare_conversation(ctx, question)
        
        # Select random models to compare
        selected_providers = random.sample(list(AIProvider), MODELS_TO_COMPARE)
        
        # Get responses from selected models
        responses = await self._get_all_model_responses(
            conversation, selected_providers, ctx, question
        )
        
        if not responses:
            return
        
        # Log costs
        self._log_total_cost(responses)
        
        # Prepare and send response message
        model_responses = self._prepare_model_responses(responses, selected_providers)
        await self._send_response_message(ctx, question, model_responses)

    async def _get_all_model_responses(
        self,
        conversation: list[dict],
        providers: list[AIProvider],
        ctx: SlashContext,
        question: str,
    ) -> dict[AIProvider, object]:
        """Get responses from all selected models."""
        responses = {}
        
        for provider in providers:
            model_config = AVAILABLE_MODELS[provider]
            try:
                response = await self._get_model_response(
                    conversation, model_config.model_id, ctx, question
                )
                responses[provider] = response
            except Exception as e:
                logger.error(f"Error calling {provider.value}: {e}")
                await ctx.send(
                    f"❌ Erreur avec le modèle {provider.value}: {e}",
                    ephemeral=True,
                )
                return {}
        
        return responses

    def _log_total_cost(self, responses: dict[AIProvider, Any]) -> None:
        """Log the cost of each response and total cost."""
        total_cost = 0.0
        
        for response in responses.values():
            cost = self._calculate_cost(response)
            total_cost += cost
            self._log_response_cost(response)
        
        logger.info(f"Total command cost: ${total_cost:.5f}")

    def _prepare_model_responses(
        self,
        responses: dict[AIProvider, Any],
        providers: list[AIProvider],
    ) -> list[dict[str, str]]:
        """Prepare and shuffle model responses for display."""
        responses_data = [
            {
                "custom_id": provider.value,
                "content": self._extract_response_content(
                    responses[provider].choices[0].message.content
                ),
            }
            for provider in providers
        ]
        random.shuffle(responses_data)
        return responses_data

    async def _prepare_conversation(
        self, ctx: SlashContext, question: str
    ) -> list[dict[str, str]]:
        """Build conversation history from recent channel messages."""
        conversation: list[dict[str, str]] = []
        messages = await ctx.channel.fetch_messages(limit=CONVERSATION_HISTORY_LIMIT)

        for message in messages:
            if message.author.id == self.bot.user.id:
                conversation.append({"role": "assistant", "content": message.content})
            else:
                author_content = f"{message.author.display_name} : {message.content}"
                conversation.append({"role": "user", "content": author_content})

        conversation.reverse()
        conversation.append({
            "role": "user",
            "content": f"{ctx.author.display_name} : {question}"
        })

        return conversation

    async def _get_model_response(
        self,
        conversation: list[dict[str, str]],
        model: str,
        ctx: SlashContext,
        question: str,
    ) -> Any:
        """Get a response from a specific AI model."""
        if not self.openrouter_client:
            raise RuntimeError("OpenRouter client not initialized")
        
        # Build context information
        server_name = ctx.guild.name if ctx.guild else "DM"
        channel_name = getattr(ctx.channel, "name", "unknown-channel")
        
        # Get mentioned users in conversation
        mentioned_users = await self._extract_mentioned_users(ctx, conversation)
        
        # Get contextual information
        context_info = search_dict_by_sentence({}, question) or ""
        
        # Build system prompt
        system_prompt = self._build_system_prompt(
            server_name, channel_name, ctx.author, mentioned_users, context_info
        )
        
        messages: list[dict[str, str]] = conversation.copy()
        messages.append({"role": "system", "content": system_prompt})

        return await self.openrouter_client.chat.completions.create(
            model=model,
            max_tokens=MAX_RESPONSE_TOKENS,
            messages=messages,  # type: ignore
            extra_body={"usage.include": True},
        )

    async def _extract_mentioned_users(
        self, ctx: SlashContext, conversation: list[dict[str, str]]
    ) -> list[UserInfo]:
        """Extract mentioned users from the conversation."""
        mentioned_users = [
            UserInfo(
                user_id=ctx.author.id,
                username=ctx.author.username,
                display_name=ctx.author.display_name,
                is_author=True,
            )
        ]
        seen_ids: set[int] = {ctx.author.id}
        
        mention_pattern = re.compile(r"<@!?(\d+)>")
        
        for msg in conversation:
            if msg["role"] != "user":
                continue
                
            for user_id_str in mention_pattern.findall(msg["content"]):
                try:
                    user_id = int(user_id_str)
                    if user_id in seen_ids:
                        continue
                        
                    user = await self.bot.fetch_user(user_id)
                    if user:
                        mentioned_users.append(
                            UserInfo(
                                user_id=user.id,
                                username=user.username,
                                display_name=user.display_name,
                            )
                        )
                        seen_ids.add(user_id)
                except (ValueError, Exception) as e:
                    logger.warning(f"Error fetching user {user_id_str}: {e}")
        
        return mentioned_users[:5]  # Limit to 5 users

    def _build_system_prompt(
        self,
        server_name: str,
        channel_name: str,
        author: Any,
        mentioned_users: list[UserInfo],
        context_info: str,
    ) -> str:
        """Build the system prompt for the AI model."""
        users_str = ", ".join(
            f"{u.display_name} ({u.username})"
            + (" (auteur de la question)" if u.is_author else "")
            for u in mentioned_users
        )
        
        author_display = getattr(author, "display_name", "Unknown")
        author_username = getattr(author, "username", "unknown")
        
        return (
            "# Rôle et contexte\n"
            "Tu es Michel·le, un assistant Discord sarcastique et impertinent avec des idées de gauche. "
            "Tu es connu pour ton humour caustique mais jamais cruel, et ta façon unique de répondre aux questions.\n\n"
            
            "# Contexte de la conversation\n"
            f"- Serveur: {server_name}\n"
            f"- Canal: {channel_name}\n"
            f"- Question posée par: {author_display} ({author_username})\n\n"
            
            "# Informations sur les personnes impliquées dans la conversation\n"
            f"{users_str}\n\n"
            
            "# Consignes\n"
            "1. Réponds au dernier message du chat avec le ton sarcastique caractéristique de Michel·le\n"
            "2. Sois concis et direct dans tes réponses\n"
            "3. Utilise l'humour et l'ironie quand c'est approprié\n"
            "4. N'expose tes idées politiques que si cela est pertinent pour la question\n"
            "5. Reste dans le personnage de Michel·le tout au long de ta réponse\n\n"
            
            "# Style de réponse\n"
            "- Ton sarcastique et un peu provocateur\n"
            "- Direct et sans détour\n"
            "- Utilise parfois des expressions familières appropriées\n"
            "- N'hésite pas à remettre en question les présupposés quand nécessaire\n\n"
            
            "# Format de réponse OBLIGATOIRE\n"
            "Tu DOIS encadrer ta réponse finale entre les balises <response> et </response>.\n"
            "N'ajoute pas d'autre contenu, seul le contenu entre les balises sera affiché à l'utilisateur.\n"
            "Exemple:<response>Voici ma réponse sarcastique</response>\n\n"
            
            "# Informations contextuelles complémentaires\n"
            f"{context_info or 'Aucune information contextuelle supplémentaire disponible.'}"
        )

    def _extract_response_content(self, raw_content: str) -> str:
        """Extract content between <response> and </response> tags."""
        match = re.search(r"<response>(.*?)</response>", raw_content, re.DOTALL)
        
        if match:
            extracted = match.group(1).strip()
            logger.debug(f"Extracted response content: {extracted[:100]}...")
            return extracted
        
        logger.warning("No <response> tags found, using full content")
        return raw_content.strip()

    def _get_model_display_name(self, provider_id: str) -> str:
        """Get the display name for a provider."""
        try:
            provider = AIProvider(provider_id)
            return AVAILABLE_MODELS[provider].display_name
        except (ValueError, KeyError):
            return provider_id

    async def _split_and_send_message(
        self, ctx_or_channel, content: str, components=None
    ):
        """
        Split and send a message that may exceed Discord's character limit.
        
        Args:
            ctx_or_channel: The context or channel to send to
            content: The message content
            components: Optional components (only added to last message)
            
        Returns:
            The last message sent
        """
        if len(content) <= DISCORD_MESSAGE_LIMIT:
            return await ctx_or_channel.send(content, components=components)
        
        message_parts = self.message_splitter.split_message(content)
        last_message = None
        
        for i, part in enumerate(message_parts):
            is_last = i == len(message_parts) - 1
            try:
                last_message = await ctx_or_channel.send(
                    part,
                    components=components if is_last else None,
                )
            except Exception as e:
                logger.error(f"Error sending message part {i + 1}: {e}")
                if len(part) > 1000:
                    await ctx_or_channel.send(f"{part[:1000]}... (truncated)")
        
        return last_message

    async def _send_response_message(
        self, ctx: SlashContext, question: str, responses: list[dict]
    ) -> None:
        """Send the response message with voting buttons."""
        message_content = self._format_responses_message(ctx, question, responses)
        components = self._create_vote_buttons(responses)

        message_info = await self._split_and_send_message(
            ctx, message_content, components=components
        )
        await self._handle_vote(ctx, message_info, question, responses, components)

    def _format_responses_message(
        self, ctx: SlashContext, question: str, responses: list[dict]
    ) -> str:
        """Format the responses into a Discord message."""
        formatted_responses = "\n\n".join(
            f"Réponse {i + 1} : \n> {resp['content'].replace(chr(10), chr(10) + '> ')}"
            for i, resp in enumerate(responses)
        )
        return (
            f"**{ctx.author.mention} : {question}**\n\n"
            f"{formatted_responses}\n\n"
            f"Votez pour la meilleure réponse en cliquant sur le bouton correspondant"
        )

    def _create_vote_buttons(self, responses: list[dict]) -> list[Button]:
        """Create voting buttons for responses."""
        return [
            Button(
                label=f"Réponse {i + 1}",
                style=ButtonStyle.SECONDARY,
                custom_id=resp["custom_id"],
            )
            for i, resp in enumerate(responses)
        ]

    async def _handle_vote(
        self,
        ctx: SlashContext,
        message_info,
        question: str,
        responses: list[dict],
        components: list[Button],
    ) -> None:
        """Handle the voting process for AI responses."""
        try:
            button_ctx: Component = await self.bot.wait_for_component(
                components=components, timeout=VOTE_TIMEOUT_SECONDS
            )
            
            if button_ctx.ctx.author_id != ctx.author.id:
                await button_ctx.ctx.send(
                    "Vous n'avez pas le droit de voter sur ce message",
                    ephemeral=True,
                )
                return

            await self._process_vote(ctx, button_ctx, message_info, question, responses)
            
        except TimeoutError:
            await self._handle_vote_timeout(ctx, message_info, question, responses)
        except Exception as e:
            logger.error(f"Unexpected error during voting: {e}")
            try:
                await ctx.send("❌ Erreur lors du traitement du vote", ephemeral=True)
            except Exception:
                pass

    async def _process_vote(
        self,
        ctx: SlashContext,
        button_ctx: Component,
        message_info,
        question: str,
        responses: list[dict],
    ) -> None:
        """Process a vote selection."""
        provider_id = button_ctx.ctx.custom_id
        logger.info(f"Vote registered: {provider_id}")
        
        self.vote_manager.save_vote(provider_id)

        selected = next(
            (r for r in responses if r["custom_id"] == provider_id), None
        )
        
        if selected:
            model_name = self._get_model_display_name(provider_id)
            new_content = (
                f"**{ctx.author.mention} : {question}**\n\n"
                f"**Réponse choisie ({model_name}) :**\n{selected['content']}"
            )
            
            try:
                if len(new_content) <= 2000:
                    await message_info.edit(content=new_content, components=[])
                else:
                    await message_info.delete()
                    await self._split_and_send_message(ctx, new_content)
            except Exception as e:
                logger.error(f"Error editing message: {e}")
                await ctx.send(f"✅ Vote enregistré pour {provider_id}")

        # Log vote counts
        vote_counts = self.vote_manager.count_votes()
        logger.info(f"Total votes: {vote_counts}")

        await button_ctx.ctx.send("✅ Vote enregistré avec succès", ephemeral=True)

    async def _handle_vote_timeout(
        self,
        ctx: SlashContext,
        message_info,
        question: str,
        responses: list[dict],
    ) -> None:
        """Handle vote timeout by revealing model names."""
        try:
            formatted_responses = "\n\n".join(
                f"**Réponse {i + 1} ({self._get_model_display_name(r['custom_id'])}) :** "
                f"\n> {r['content'].replace(chr(10), chr(10) + '> ')}"
                for i, r in enumerate(responses)
            )
            timeout_content = (
                f"**{ctx.author.mention} : {question}**\n\n"
                f"{formatted_responses}\n\n"
                f"⏰ *Temps de vote expiré*"
            )
            
            if len(timeout_content) <= 2000:
                await message_info.edit(content=timeout_content, components=[])
            else:
                await message_info.delete()
                await self._split_and_send_message(ctx, timeout_content)
                
            logger.info("Vote timeout - buttons removed and models revealed")
        except Exception as e:
            logger.error(f"Error handling timeout: {e}")

    # =========================================================================
    # Cost Calculation
    # =========================================================================

    def _calculate_cost(self, message) -> float:
        """Calculate the cost of an API response."""
        if not hasattr(message, "usage") or not message.usage:
            logger.warning("Usage information missing from response")
            return 0.0
        
        input_tokens = getattr(message.usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(message.usage, "completion_tokens", 0) or 0
        model_id = getattr(message, "model", "unknown")
        
        pricing = self.model_prices.get(model_id)
        if pricing:
            return pricing.calculate_cost(input_tokens, output_tokens)
        
        logger.warning(f"Pricing not found for model {model_id}")
        return 0.0

    def _log_response_cost(self, message) -> None:
        """Log the cost details of an API response."""
        if not hasattr(message, "usage") or not message.usage:
            model_id = getattr(message, "model", "unknown")
            logger.info(f"Model: {model_id} | Cost: usage info missing")
            return
        
        input_tokens = getattr(message.usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(message.usage, "completion_tokens", 0) or 0
        model_id = getattr(message, "model", "unknown")
        
        pricing = self.model_prices.get(model_id)
        if pricing:
            input_cost = pricing.input_cost_per_token * input_tokens
            output_cost = pricing.output_cost_per_token * output_tokens
            total_cost = input_cost + output_cost
            
            logger.info(
                "Model: %s | Cost: $%.5f | $%.5f (%d tks) in | $%.5f (%d tks) out",
                model_id,
                total_cost,
                input_cost,
                input_tokens,
                output_cost,
                output_tokens,
            )
        else:
            logger.info(
                "Model: %s | Cost: unknown | %d tks in | %d tks out",
                model_id,
                input_tokens,
                output_tokens,
            )
