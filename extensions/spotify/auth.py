"""Spotify OAuth refresh slash command (`/updatetoken`, dev guild only)."""

import os

import spotipy
from interactions import (
    Modal,
    ModalContext,
    ParagraphText,
    ShortText,
    SlashContext,
    slash_command,
)

from src.core import logging as logutil

from ._common import (
    DEV_GUILD,
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
)

logger = logutil.init_logger(os.path.basename(__file__))


class AuthMixin:
    """Dev-only helper to refresh the cached Spotify OAuth token via modal."""

    @slash_command(
        name="updatetoken",
        description="Met à jour le token de l'application Spotify",
        scopes=[DEV_GUILD],
    )
    async def updatetoken(self, ctx: SlashContext):
        sp_oauth = spotipy.SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            client_secret=SPOTIFY_CLIENT_SECRET,
            scope=(
                "playlist-modify-private playlist-read-private "
                "playlist-modify-public playlist-read-collaborative"
            ),
            open_browser=False,
            cache_handler=spotipy.CacheFileHandler("data/.cache"),
        )

        token_info = sp_oauth.get_cached_token()
        if token_info:
            logger.info(
                "token_info : %s\nIsExpired : %s\nIsValid : %s",
                token_info,
                sp_oauth.is_token_expired(token_info),
                sp_oauth.validate_token(token_info),
            )

        auth_url = sp_oauth.get_authorize_url()
        modal = Modal(
            ShortText(label="Auth URL :", value=auth_url, custom_id="auth_url"),
            ParagraphText(label="Answer URL :", custom_id="answer_url"),
            title="Spotify Auth",
        )
        await ctx.send_modal(modal)
        modal_ctx: ModalContext = await ctx.bot.wait_for_modal(modal)
        auth_code = modal_ctx.responses["answer_url"]
        sp_oauth.get_access_token(sp_oauth.parse_response_code(auth_code), as_dict=False)
        await modal_ctx.send("Token mis à jour !", ephemeral=True)
