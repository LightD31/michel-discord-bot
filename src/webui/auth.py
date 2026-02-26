"""
Discord OAuth2 authentication for the Web UI.
"""

import secrets
import time
from typing import Optional
from dataclasses import dataclass, field

import aiohttp
from src import logutil

logger = logutil.init_logger("webui.auth")

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_OAUTH2_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"


@dataclass
class Session:
    """Represents an authenticated user session."""
    user_id: str
    username: str
    avatar: Optional[str]
    guilds: list
    access_token: str
    refresh_token: str
    expires_at: float
    session_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))


class DiscordOAuth:
    """Handles Discord OAuth2 flow and session management."""

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, admin_user_ids: Optional[list[str]] = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.admin_user_ids = admin_user_ids or []
        self.sessions: dict[str, Session] = {}
        self._cleanup_counter = 0

    def get_oauth_url(self, state: str) -> str:
        """Generate the Discord OAuth2 authorization URL."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "identify guilds",
            "state": state,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{DISCORD_OAUTH2_URL}?{query}"

    async def exchange_code(self, code: str) -> Optional[Session]:
        """Exchange an authorization code for tokens and create a session."""
        async with aiohttp.ClientSession() as http:
            # Exchange code for token
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            }
            async with http.post(DISCORD_TOKEN_URL, data=data) as resp:
                if resp.status != 200:
                    logger.error(f"Token exchange failed: {resp.status}")
                    return None
                token_data = await resp.json()

            access_token = token_data["access_token"]
            refresh_token = token_data["refresh_token"]
            expires_in = token_data["expires_in"]

            # Get user info
            headers = {"Authorization": f"Bearer {access_token}"}
            async with http.get(f"{DISCORD_API_BASE}/users/@me", headers=headers) as resp:
                if resp.status != 200:
                    logger.error(f"User info fetch failed: {resp.status}")
                    return None
                user_data = await resp.json()

            # Get user guilds
            async with http.get(f"{DISCORD_API_BASE}/users/@me/guilds", headers=headers) as resp:
                if resp.status != 200:
                    logger.error(f"Guilds fetch failed: {resp.status}")
                    guilds = []
                else:
                    guilds = await resp.json()

        session = Session(
            user_id=user_data["id"],
            username=user_data.get("global_name") or user_data["username"],
            avatar=user_data.get("avatar"),
            guilds=guilds,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
        )

        # Check if user is authorized (admin)
        if self.admin_user_ids and session.user_id not in self.admin_user_ids:
            logger.warning(f"Unauthorized user attempted login: {session.username} ({session.user_id})")
            return None

        self.sessions[session.session_token] = session
        self._maybe_cleanup()
        logger.info(f"User logged in: {session.username} ({session.user_id})")
        return session

    def get_session(self, session_token: str) -> Optional[Session]:
        """Retrieve a session by its token."""
        session = self.sessions.get(session_token)
        if session and session.expires_at > time.time():
            return session
        if session:
            del self.sessions[session_token]
        return None

    def invalidate_session(self, session_token: str):
        """Remove a session."""
        self.sessions.pop(session_token, None)

    def _maybe_cleanup(self):
        """Periodically clean up expired sessions."""
        self._cleanup_counter += 1
        if self._cleanup_counter >= 10:
            self._cleanup_counter = 0
            now = time.time()
            expired = [k for k, v in self.sessions.items() if v.expires_at <= now]
            for k in expired:
                del self.sessions[k]

    def get_user_managed_guilds(self, session: Session) -> list[dict]:
        """
        Return guilds where the user has MANAGE_GUILD permission (0x20)
        or is an admin. Useful to filter which servers' config the user can edit.
        """
        MANAGE_GUILD = 0x20
        ADMINISTRATOR = 0x8
        managed = []
        for guild in session.guilds:
            perms = int(guild.get("permissions", 0))
            if guild.get("owner") or (perms & ADMINISTRATOR) or (perms & MANAGE_GUILD):
                managed.append(guild)
        return managed
