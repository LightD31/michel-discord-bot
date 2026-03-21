"""Shared utility functions: image generation, text processing, HTTP fetch, pagination."""

import os
import re
import string
import asyncio
from collections import defaultdict
from io import BytesIO
from typing import Optional, Tuple

import emoji
from aiohttp import ClientSession, ClientError
from interactions import ComponentContext, Message
from interactions.api.events import MessageReactionAdd, MessageReactionRemove
from interactions.ext import paginators
from PIL import Image, ImageDraw, ImageFont

from src import logutil
from src.config_manager import load_config, load_discord2name, save_config  # re-export

logger = logutil.init_logger(os.path.basename(__file__))


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

def milliseconds_to_string(duration_ms) -> str:
    """Convert milliseconds to a French human-readable duration string."""
    duration_ms = int(duration_ms)
    seconds = duration_ms / 1000
    days = seconds // 86400
    seconds %= 86400
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    return (
        f"{int(days)} jour(s) {int(hours):02d} heure(s) "
        f"{int(minutes):02d} minute(s) et {int(seconds):02d} seconde(s)"
    )


# ---------------------------------------------------------------------------
# Image generation
# ---------------------------------------------------------------------------

def create_dynamic_image(
    text: str,
    font_size: int = 20,
    font_path: str = "src/Menlo-Regular.ttf",
    image_padding: int = 10,
    background_color: str = "#1E1F22",
) -> Tuple[Image.Image, BytesIO]:
    """Create a dynamic image with the specified text."""
    if not text:
        raise ValueError("Text cannot be empty")
    if font_size <= 0:
        raise ValueError("Font size must be greater than zero")
    if image_padding < 0:
        raise ValueError("Image padding cannot be negative")

    font = ImageFont.truetype(font_path, font_size)
    draw = ImageDraw.Draw(Image.new("RGB", (0, 0)))
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]

    image_width = text_width + 2 * image_padding
    image_height = text_height + 2 * image_padding
    image = Image.new("RGB", (image_width, image_height), color=background_color)
    draw = ImageDraw.Draw(image)

    x = image_width // 2
    y = image_height // 2
    draw.text((x, y), text, font=font, fill=0xF2F3F5, anchor="mm")

    image_io = BytesIO()
    image.save(image_io, "png")
    image_io.seek(0)

    return image, image_io


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------

def format_number(num) -> str:
    """Format a number with k suffix for thousands."""
    if num >= 1000:
        return f"{num / 1000:.1f}k"
    return str(num)


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------

def escape_md(text: str) -> str:
    """Escape markdown special characters in the given text."""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", text)


def sanitize_content(content: str) -> str:
    """Remove custom emojis, unicode emojis, and mentions from content."""
    content = re.sub(r"<:\w*:\d*>", "", content)
    content = emoji.replace_emoji(content, " ")
    content = re.sub(r"<@\d*>", "", content)
    return content


def remove_punctuation(input_string: str) -> str:
    """Remove all punctuation from the input string."""
    translator = str.maketrans("", "", string.punctuation)
    return input_string.translate(translator).strip()


def search_dict_by_sentence(my_dict: dict, sentence: str):
    """Search a dictionary by matching words in a sentence to keys."""
    words = set(sentence.lower().split())
    for key, value in my_dict.items():
        if isinstance(key, tuple):
            key_lower = tuple(k.lower() for k in key)
            if any(word in key_lower for word in words):
                return value
        else:
            if key.lower() in words:
                return value
    return None


def extract_answer(text: str) -> str | None:
    """Extract content between <answer> tags."""
    pattern = r"<answer>(.*?)</answer>"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Poll formatting
# ---------------------------------------------------------------------------

name_cache: dict[str, dict[str, str]] = {}


async def format_poll(event: MessageReactionAdd | MessageReactionRemove):
    """Format a poll message by updating the description with current vote counts."""
    message = event.message
    embed = message.embeds[0]
    options = embed.description.split("\n\n")
    reactions = message.reactions

    reaction_users = defaultdict(list)
    reaction_counts = defaultdict(int)
    max_reaction_count = 0
    for reaction in reactions:
        users = [user for user in await reaction.users().flatten() if not user.bot]
        reaction_users[str(reaction.emoji)] = users
        reaction_counts[str(reaction.emoji)] = reaction.count - 1
        if reaction.count > max_reaction_count:
            max_reaction_count = reaction.count - 1

    max_reaction_indices = [
        i for i, count in reaction_counts.items() if count == max_reaction_count
    ]
    participant_count = len(
        set(user.id for users in reaction_users.values() for user in users)
        - {message.author.id}
    )

    description_list = []
    for i, option in enumerate(options):
        option = option.split(":", 1)[0].replace("**", "")
        emoji_str = option.split(" ", 1)[0]

        reaction_count = reaction_counts[emoji_str]
        user_list = reaction_users[emoji_str]

        user_names = []
        for user in user_list:
            user_name = user.display_name
            user_id = str(user.id)
            server_id = str(event.message.guild.id)
            if server_id not in name_cache:
                name_cache[server_id] = {}
            if user_id not in name_cache[server_id]:
                d2n = load_discord2name(server_id)
                name_cache[server_id][user_id] = d2n.get(user_id, user_name)
            user_name = name_cache[server_id][user_id]
            user_names.append(user_name)
        user_names_str = ", ".join(user_names)

        description = f"{option}"
        if reaction_count > 0:
            description = (
                f"**{option} : {reaction_count}/{participant_count} votes\n({user_names_str})**"
                if emoji_str in max_reaction_indices
                else f"{option} : **{reaction_count}/{participant_count} votes**\n({user_names_str})"
            )

        description_list.append(description)

    embed.description = "\n\n".join(description_list)
    return embed


# ---------------------------------------------------------------------------
# HTTP fetch with retry
# ---------------------------------------------------------------------------

async def fetch(
    url: str,
    return_type: str = "text",
    headers: dict | None = None,
    params: dict | None = None,
    retries: int = 3,
    pause: int = 1,
):
    """Fetch a URL with retry logic. Returns text or JSON."""
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        )
    }
    if headers:
        default_headers.update(headers)

    for i in range(retries):
        try:
            async with ClientSession() as session:
                async with session.get(url, headers=default_headers, params=params) as response:
                    if response.status >= 500:
                        logger.error("Failed to fetch %s: Status %s", url, response.status)
                        if i < retries - 1:
                            await asyncio.sleep(pause * (i + 1))
                            continue
                        raise Exception(f"Failed to fetch {url}: Status {response.status}")
                    if response.status != 200:
                        logger.error("Failed to fetch %s: Status %s", url, response.status)
                        raise Exception(f"Failed to fetch {url}: Status {response.status}")
                    if return_type == "text":
                        return await response.text()
                    elif return_type == "json":
                        return await response.json()
                    else:
                        raise ValueError("Invalid return_type. Expected 'text' or 'json'.")
        except (ClientError, asyncio.TimeoutError) as e:
            logger.error("Error fetching %s: %s", url, e)
            if i == retries - 1:
                raise
            await asyncio.sleep(pause)


# ---------------------------------------------------------------------------
# Custom Paginator (shared across extensions)
# ---------------------------------------------------------------------------

class CustomPaginator(paginators.Paginator):
    """Custom paginator with overridden button handling."""

    async def _on_button(
        self, ctx: ComponentContext, *args, **kwargs
    ) -> Optional[Message]:
        if self._timeout_task:
            self._timeout_task.ping.set()
        match ctx.custom_id.split("|")[1]:
            case "first":
                self.page_index = 0
            case "last":
                self.page_index = len(self.pages) - 1
            case "next":
                if (self.page_index + 1) < len(self.pages):
                    self.page_index += 1
            case "back":
                if self.page_index >= 1:
                    self.page_index -= 1
            case "select":
                self.page_index = int(ctx.values[0])
            case "callback":
                if self.callback:
                    return await self.callback(ctx)

        await ctx.edit_origin(**self.to_dict())
        return None
