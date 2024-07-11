"""
This module provides a Discord bot extension that sends a status update to a server at a specific time.
"""

import os

import aiohttp
from interactions import Extension, listen, Task, IntervalTrigger, Client

from src import logutil
from src.utils import load_config

logger = logutil.init_logger(os.path.basename(__file__))
config = load_config()[0]
config = config.get("uptimeKuma", {})

class Uptime(Extension):
    """
    A Discord bot extension that sends a status update to a server at a specific time.
    """
    def __init__(self, bot):
        self.bot : Client = bot
    @listen()
    async def on_startup(self):
        """
        Start background tasks.
        """
        self.send_status_update.start()
        await self.send_status_update()

    @Task.create(IntervalTrigger(seconds=55))
    async def send_status_update(self):
        """
        Perform status checks and gather information about your service/script's status.
        """
        async with aiohttp.ClientSession() as session:
            try:
                # Create the URL
                url = f"https://{config['uptimeKumaUrl']}/api/push/{config['uptimeKumaToken']}?status=up&msg=OK&ping={round(self.bot.latency * 1000, 1)}"

                # Send the status update
                async with session.get(url) as response:
                    response.raise_for_status()
                    logger.debug("Status update sent successfully.")
            except aiohttp.ClientError as error:
                logger.error("Error sending status update: %s", error)
