import json
import os
import emoji
import string
import re
from collections import defaultdict
from io import BytesIO
from typing import Tuple
import asyncio
from aiohttp import ClientSession, ClientError
from interactions.api.events import MessageReactionAdd, MessageReactionRemove
from PIL import Image, ImageDraw, ImageFont

from src import logutil

logger = logutil.init_logger(os.path.basename(__file__))


def milliseconds_to_string(duration_ms):
    duration_ms = int(duration_ms)
    seconds = duration_ms / 1000
    days = seconds // 86400
    seconds %= 86400
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    return f"{int(days)} jour(s) {int(hours):02d} heure(s) {int(minutes):02d} minute(s) et {int(seconds):02d} seconde(s)"


def create_dynamic_image(
    text: str,
    font_size: int = 20,
    font_path: str = "src/Menlo-Regular.ttf",
    image_padding: int = 10,
    background_color: str = "#1E1F22",
) -> Tuple[Image.Image, BytesIO]:
    """
    Creates a dynamic image with the specified text.

    Args:
        text (str): The text to display on the image.
        font_size (int): The size of the font to use.
        font_path (str): The path to the font file to use.
        image_padding (int): The amount of padding to add to the image.
        background_color (str): The background color of the image.

    Returns:
        A tuple containing the image object and a BytesIO object containing the image data.
    """
    # Validate input
    if not text:
        raise ValueError("Text cannot be empty")
    if font_size <= 0:
        raise ValueError("Font size must be greater than zero")
    if image_padding < 0:
        raise ValueError("Image padding cannot be negative")

    # Create a font object
    font = ImageFont.truetype(font_path, font_size)

    # Create a drawing object
    draw = ImageDraw.Draw(Image.new("RGB", (0, 0)))

    # Calculate the bounding box of the text
    bbox = draw.textbbox((0, 0), text, font=font)

    # Calculate the width and height of the text
    text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]

    # Add padding to the image size
    image_width = text_width + 2 * image_padding
    image_height = text_height + 2 * image_padding

    # Create a new image with the calculated size and background color
    image = Image.new("RGB", (image_width, image_height), color=background_color)

    # Create a new drawing object
    draw = ImageDraw.Draw(image)

    # Calculate the position to center the text
    x = image_width // 2
    y = image_height // 2

    # Draw the text on the image
    draw.text((x, y), text, font=font, fill=0xF2F3F5, anchor="mm")

    # Save the image as a PNG file and return the image object and a BytesIO object containing the image data
    imageIO = BytesIO()
    image.save(imageIO, "png")
    imageIO.seek(0)

    return image, imageIO


def format_number(num):
    if num >= 1000:
        return f"{num/1000:.1f}k"
    else:
        return str(num)


name_cache = {}


def escape_md(text):
    """Escape markdown special characters in the given text."""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", text)


async def format_poll(event: MessageReactionAdd | MessageReactionRemove, config):
    """
    Formats a poll message by updating the description with the current vote counts and participants.

    Args:
        event (MessageReactionAdd | MessageReactionRemove): The event object representing the reaction add or remove event.

    Returns:
        Embed: The updated embed object with the formatted poll description.
    """
    message = event.message
    embed = message.embeds[0]
    options = embed.description.split("\n\n")
    reactions = message.reactions

    # Fetch all users at once and store them in a dictionary
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
        emoji = option.split(" ", 1)[0]

        reaction_count = reaction_counts[emoji]
        user_list = reaction_users[emoji]

        user_names = []
        for user in user_list:
            user_name = user.display_name
            user_id = str(user.id)
            server_id = str(event.message.guild.id)
            if server_id not in name_cache:
                name_cache[server_id] = {}
            if user_id not in name_cache[server_id]:
                # If not in cache, compute it and store it in the cache
                name_cache[server_id][user_id] = (
                    config["discord2name"].get(server_id, {}).get(user_id, user_name)
                )
            user_name = name_cache[server_id][user_id]
            user_names.append(user_name)
        user_names_str = ", ".join(user_names)

        description = f"{option}"
        if reaction_count > 0:
            description = (
                f"**{option} : {reaction_count}/{participant_count} votes\n({user_names_str})**"
                if emoji in max_reaction_indices
                else f"{option} : **{reaction_count}/{participant_count} votes**\n({user_names_str})"
            )

        description_list.append(description)

    embed.description = "\n\n".join(description_list)
    return embed


def load_config(module_name: str = None) -> Tuple[dict, dict, list[str]]:
    """
    Load the configuration for a specific module.

    Args:
        module_name (str): The name of the module.

    Returns:
        A tuple containing the global configuration, the module-specific configuration, and the list of enabled servers.
    """
    # Try to use the new modular config manager first
    try:
        from src.config_manager import ConfigManager
        config_manager = ConfigManager()
        data = config_manager.load_full_config()
        logger.debug("Using modular configuration system")
    except (ImportError, FileNotFoundError):
        # Fallback to the old single file system
        try:
            with open("config/config.json", "r", encoding="utf-8") as file:
                data = json.load(file)
            logger.debug("Using legacy configuration system")
        except FileNotFoundError:
            logger.error("No configuration file found (config.json or main.json)")
            return {}, {}, []
    
    if module_name is None:
        return data.get("config", {}), {}, []
    
    enabled_servers = [
        str(server_id)
        for server_id, server_info in data["servers"].items()
        if server_info.get(module_name, {}).get("enabled", False)
    ]
    module_config = {
        server_id: server_info.get(module_name, {})
        for server_id, server_info in data["servers"].items()
        if str(server_id) in enabled_servers
    }
    config = data.get("config", {})
    logger.info(
        "Loaded config for module %s for servers %s",
        module_name,
        enabled_servers,
    )
    return config, module_config, enabled_servers


def save_config(
    module_name: str, config: dict, module_config: dict, enabled_servers: list[str]
):
    """
    Save the configuration for a specific module.

    Args:
        module_name (str): The name of the module.
        config (dict): The global configuration.
        module_config (dict): The module-specific configuration.
        enabled_servers (list[int]): The list of enabled servers.
    """
    with open("config/config.json", "r", encoding="utf-8") as file:
        data = json.load(file)
    for server_id, server_info in data["servers"].items():
        if str(server_id) in enabled_servers:
            server_info[module_name] = module_config
        else:
            server_info[module_name] = {"enabled": False}
    data["config"] = config
    with open("config/config.json", "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)
    logger.info(
        "Saved config for module %s for servers %s",
        module_name,
        enabled_servers,
    )


def sanitize_content(content):
    # Remove custom emojis
    content = re.sub(r"<:\w*:\d*>", "", content)
    # Remove emojis
    content = emoji.replace_emoji(content, " ")
    # Remove mentions
    content = re.sub(r"<@\d*>", "", content)
    return content


def remove_punctuation(input_string: str):
    # Make a translator object that will replace all punctuation with None
    translator = str.maketrans("", "", string.punctuation)

    # Use the translator object to remove punctuation from the input string
    return input_string.translate(translator).strip()


def search_dict_by_sentence(my_dict, sentence):
    # Create a set of lowercase words in the sentence for efficient lookup
    words = set(sentence.lower().split())

    # Iterate through dictionary keys
    for key, value in my_dict.items():
        # Convert keys to lowercase if they are tuples
        if isinstance(key, tuple):
            key_lower = tuple(k.lower() for k in key)
            # Check if any word in the key matches any word in the sentence
            if any(word in key_lower for word in words):
                return value
        # For non-tuple keys, check if the key directly matches any word in the sentence
        else:
            if key.lower() in words:
                return value
    return None


def extract_answer(text):
    pattern = r"<answer>(.*?)</answer>"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1)
    else:
        return None


async def fetch(url, return_type="text", headers=None, params=None, retries=3, pause=1):
    # Set default headers with User-Agent if not provided
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    if headers:
        default_headers.update(headers)
    
    headers = default_headers
    
    for i in range(retries):
        try:
            async with ClientSession() as session:
                async with session.get(url, headers=headers, params=params) as response:
                    if response.status != 200:
                        logger.error(f"Failed to fetch {url}: Status {response.status}")
                        raise Exception(
                            f"Failed to fetch {url}: Status {response.status}"
                        )
                    if return_type == "text":
                        return await response.text()
                    elif return_type == "json":
                        return await response.json()
                    else:
                        raise ValueError(
                            "Invalid return_type. Expected 'text' or 'json'."
                        )
        except (ClientError, asyncio.TimeoutError) as e:
            logger.error(f"Error fetching {url}: {e}")
            if i == retries - 1:  # This was the last attempt
                raise
            else:
                await asyncio.sleep(pause)
