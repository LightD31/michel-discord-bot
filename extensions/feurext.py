import json
import os
import re
import string
from datetime import datetime
from typing import Optional

from interactions import Extension, listen, slash_command, SlashContext, Embed, EmbedFooter
from interactions.api.events import MessageCreate

from src import logutil
from src.utils import load_config, sanitize_content

logger = logutil.init_logger(os.path.basename(__file__))

config, module_config, enabled_servers = load_config("moduleFeur")

# Emojis for reactions
FEUR_EMOJIS = ["🇫", "🇪", "🇺", "🇷"]
POUR_FEUR_EMOJIS = ["🇵", "🇴", "🇺", "🇷", "🇫", "🇪", "⛎", "®️"]  # ⛎ for second U, ® for second R

# Stats file path
STATS_FILE = "data/feur_stats.json"


class Feur(Extension):
    def __init__(self, bot):
        self.bot = bot
        self.stats = self._load_stats()
    
    def _load_stats(self) -> dict:
        """Load stats from file."""
        try:
            if os.path.exists(STATS_FILE):
                with open(STATS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Error loading feur stats: {e}")
        return {"users": {}, "guilds": {}, "total": 0}
    
    def _save_stats(self):
        """Save stats to file."""
        try:
            os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
            with open(STATS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving feur stats: {e}")
    
    def _record_feur(self, user_id: str, guild_id: Optional[str], feur_type: str):
        """Record a feur event in stats."""
        # Update total
        self.stats["total"] = self.stats.get("total", 0) + 1
        
        # Update user stats
        if user_id not in self.stats["users"]:
            self.stats["users"][user_id] = {"total": 0, "feur": 0, "pour_feur": 0}
        self.stats["users"][user_id]["total"] += 1
        self.stats["users"][user_id][feur_type] = self.stats["users"][user_id].get(feur_type, 0) + 1
        
        # Update guild stats
        if guild_id:
            if guild_id not in self.stats["guilds"]:
                self.stats["guilds"][guild_id] = {"total": 0, "feur": 0, "pour_feur": 0}
            self.stats["guilds"][guild_id]["total"] += 1
            self.stats["guilds"][guild_id][feur_type] = self.stats["guilds"][guild_id].get(feur_type, 0) + 1
        
        self._save_stats()

    def _should_respond(self, content: str, keyword: str) -> bool:
        """
        Determines if the bot should respond to a message containing the keyword.
        
        Args:
            content (str): The sanitized and lowercased message content.
            keyword (str): The keyword to check for ("quoi" or "pourquoi").
            
        Returns:
            bool: True if the bot should respond, False otherwise.
        """
        if keyword not in content:
            return False
            
        words = self._extract_words(content)
        
        # Case 1: Message ends with the keyword (with or without punctuation)
        if words and words[-1] == keyword:
            return True
            
        # Case 2: Keyword followed by "?" in the same sentence
        sentences = self._split_into_sentences(content)
        for sentence in sentences:
            if keyword in sentence and "?" in sentence:
                keyword_index = sentence.find(keyword)
                question_mark_index = sentence.find("?", keyword_index)
                if question_mark_index != -1:
                    return True
                    
        return False
    
    def _extract_words(self, content: str) -> list:
        """
        Extract words from content, removing punctuation and extra whitespace.
        
        Args:
            content (str): The message content.
            
        Returns:
            list: List of words without punctuation.
        """
        translator = str.maketrans("", "", string.punctuation)
        clean_content = content.translate(translator)
        return clean_content.split()
    
    def _split_into_sentences(self, content: str) -> list:
        """
        Split content into sentences based on common sentence delimiters.
        
        Args:
            content (str): The message content.
            
        Returns:
            list: List of sentences.
        """
        sentences = re.split(r'[.!?\n]+|  +', content)
        return [s.strip() for s in sentences if s.strip()]

    async def _add_reactions(self, message, emojis: list):
        """Add letter reactions to a message."""
        try:
            for emoji in emojis:
                await message.add_reaction(emoji)
        except Exception as e:
            logger.debug(f"Could not add reactions: {e}")

    @listen()
    async def on_message(self, event: MessageCreate):
        """
        This method is called when a message is received.

        Args:
            event (interactions.api.events.MessageCreate): The message event.
        """
        if event.message.author.bot is True:
            logger.debug("Message from bot, ignoring")
            return
        if event.message.guild is not None:
            if str(event.message.guild.id) not in module_config.keys():
                return
        
        content = sanitize_content(event.message.content.lower()).strip()
        logger.debug("Message content: %s", content)
        
        user_id = str(event.message.author.id)
        guild_id = str(event.message.guild.id) if event.message.guild else None
        
        # Check for "pourquoi" wordplay (check first as it contains "quoi")
        if self._should_respond(content, "pourquoi"):
            await self._add_reactions(event.message, POUR_FEUR_EMOJIS)
            self._record_feur(user_id, guild_id, "pour_feur")
            return

        # Check for "quoi" wordplay (only if word is exactly "quoi", not part of "pourquoi")
        if self._should_respond(content, "quoi"):
            # Make sure it's not "pourquoi" triggering this
            words = self._extract_words(content)
            if words and words[-1] == "quoi" or ("quoi" in content and "pourquoi" not in content):
                await self._add_reactions(event.message, FEUR_EMOJIS)
                self._record_feur(user_id, guild_id, "feur")

    @slash_command(name="feurstats", description="Affiche les statistiques de feur")
    async def feur_stats(self, ctx: SlashContext):
        """Display feur statistics."""
        guild_id = str(ctx.guild_id) if ctx.guild_id else None
        user_id = str(ctx.author.id)
        
        # Get user stats
        user_stats = self.stats["users"].get(user_id, {"total": 0, "feur": 0, "pour_feur": 0})
        
        # Get guild stats
        guild_stats = self.stats["guilds"].get(guild_id, {"total": 0, "feur": 0, "pour_feur": 0}) if guild_id else None
        
        # Get top users in this guild
        top_users = []
        if guild_id and ctx.guild:
            user_totals = []
            for uid, ustats in self.stats["users"].items():
                try:
                    member = await ctx.guild.fetch_member(int(uid))
                    if member:
                        user_totals.append((member.display_name, ustats.get("total", 0)))
                except Exception:
                    pass
            top_users = sorted(user_totals, key=lambda x: x[1], reverse=True)[:5]
        
        embed = Embed(
            title="📊 Statistiques Feur",
            color=0x9B59B6,
            timestamp=datetime.now(),
            footer=EmbedFooter(text=f"Demandé par {ctx.author.display_name}")
        )
        
        # Global stats
        embed.add_field(
            name="🌍 Total global",
            value=f"**{self.stats.get('total', 0)}** feurs envoyés",
            inline=False
        )
        
        # User stats
        embed.add_field(
            name="👤 Tes stats",
            value=f"Total: **{user_stats.get('total', 0)}**\n"
                  f"Feur: **{user_stats.get('feur', 0)}**\n"
                  f"Pour feur: **{user_stats.get('pour_feur', 0)}**",
            inline=True
        )
        
        # Guild stats
        if guild_stats:
            embed.add_field(
                name="🏠 Stats du serveur",
                value=f"Total: **{guild_stats.get('total', 0)}**\n"
                      f"Feur: **{guild_stats.get('feur', 0)}**\n"
                      f"Pour feur: **{guild_stats.get('pour_feur', 0)}**",
                inline=True
            )
        
        # Top users
        if top_users:
            top_text = "\n".join([f"{i+1}. {name}: **{count}**" for i, (name, count) in enumerate(top_users)])
            embed.add_field(
                name="🏆 Top 5 du serveur",
                value=top_text,
                inline=False
            )
        
        await ctx.send(embed=embed)