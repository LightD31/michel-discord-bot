import json
import os
from collections import defaultdict
from io import BytesIO
from typing import Tuple

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

    # Calculate the width and height of the text
    left, top, right, bottom = draw.multiline_textbbox((0, 0), text, font=font)

    # Add padding to the image size
    image_width = right - left + 2 * image_padding
    image_height = bottom - top + 2 * image_padding

    # Create a new image with the calculated size and background color
    image = Image.new("RGB", (image_width, image_height), color=background_color)

    # Create a new drawing object
    draw = ImageDraw.Draw(image)

    # Calculate the position to center the text
    x = (image_width - (right - left)) // 2
    y = (image_height - (bottom - top)) // 2

    # Draw the text on the image
    draw.multiline_text((x, y), text, font=font, fill=0xF2F3F5)

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
    with open("config/config.json", "r", encoding="utf-8") as file:
        data = json.load(file)
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
