import os

import interactions
import requests

from src import logutil

logger = logutil.init_logger(os.path.basename(__file__))


class IAExt(interactions.Extension):
    # Command to send a request using Kobold API
    @interactions.slash_command(
        name="kobold", description="Send a request to Kobold API"
    )
    @interactions.slash_option(
        name="prompt",
        description="Prompt for Kobold API",
        opt_type=interactions.OptionType.STRING,
    )
    # Defer
    @interactions.auto_defer()
    async def kobold(self, ctx: interactions.SlashContext, prompt: str):
        # Send request to Kobold API
        person = ctx.author.display_name
        data = {
            "max_context_length": 2048,
            "max_length": 100,
            "prompt": f"[INST]Write Michel's next reply in this fictional roleplay with {person}.[/INST]\n{person} : "
            + prompt,
            "rep_pen": 1.1,
            "rep_pen_range": 256,
            "rep_pen_slope": 1,
            "temperature": 0.5,
            "tfs": 1,
            "top_a": 0,
            "top_k": 100,
            "top_p": 0.9,
            "typical": 1,
        }
        response = requests.post(
            "https://candidate-insert-commit-php.trycloudflare.com/api/v1/generate",
            json=data,
            timeout=90,
        )
        logger.info(response.json())
        # Send response
        await ctx.send(response.json()["results"][0]["text"])
