"""
Discord OAuth2 authentication for the Web UI.
"""

import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlencode

from aiohttp import ClientError

from src.core import logging as logutil
from src.core.http import http_client
from src.webui.sessions import SessionRepository, expires_dt_from_ts

logger = logutil.init_logger("webui.auth")

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_OAUTH2_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"


@dataclass
class Session:
    """Represents an authenticated user session."""

    user_id: str
    username: str
    avatar: str | None
    guilds: list
    access_token: str
    refresh_token: str
    expires_at: float
    session_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))


def _session_to_doc(session: Session) -> dict[str, Any]:
    """Serialize a session for MongoDB (``_id`` = token, TTL helper field)."""
    doc = asdict(session)
    doc["_id"] = doc.pop("session_token")
    doc["expires_dt"] = expires_dt_from_ts(session.expires_at)
    return doc


def _doc_to_session(doc: dict[str, Any]) -> Session:
    return Session(
        user_id=doc["user_id"],
        username=doc["username"],
        avatar=doc.get("avatar"),
        guilds=doc.get("guilds", []),
        access_token=doc["access_token"],
        refresh_token=doc.get("refresh_token", ""),
        expires_at=float(doc["expires_at"]),
        session_token=doc["_id"],
    )


class DiscordOAuth:
    """Handles Discord OAuth2 flow and session management."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        developer_user_ids: list[str] | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.developer_user_ids = developer_user_ids or []
        self.sessions: dict[str, Session] = {}

    def get_oauth_url(self, state: str) -> str:
        """Generate the Discord OAuth2 authorization URL."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "identify guilds",
            "state": state,
        }
        return f"{DISCORD_OAUTH2_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> Session | None:
        """Exchange an authorization code for tokens and create a session."""
        try:
            http = await http_client.session()
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

            access_token = token_data.get("access_token")
            if not access_token:
                logger.error("Token exchange response missing access_token")
                return None

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
        except (TimeoutError, ClientError, ValueError) as e:
            logger.error("OAuth code exchange failed: %s", e)
            return None

        session = self._session_from_payload(token_data, user_data, guilds)
        if session is None:
            return None

        self.sessions[session.session_token] = session
        await self._persist(session)
        logger.info(f"User logged in: {session.username} ({session.user_id})")
        return session

    @staticmethod
    def _session_from_payload(
        token_data: dict[str, Any],
        user_data: dict[str, Any],
        guilds: list,
    ) -> Session | None:
        """Build a Session from Discord's responses; None if a field is missing."""
        access_token = token_data.get("access_token")
        user_id = user_data.get("id")
        if not access_token or not user_id:
            logger.error("Discord OAuth payload incomplete (token or user id missing)")
            return None
        try:
            expires_in = float(token_data.get("expires_in") or 0)
        except (TypeError, ValueError):
            expires_in = 0.0
        if expires_in <= 0:
            expires_in = 3600.0  # conservative fallback, Discord normally sends 7 days
        return Session(
            user_id=str(user_id),
            username=user_data.get("global_name") or user_data.get("username") or str(user_id),
            avatar=user_data.get("avatar"),
            guilds=guilds if isinstance(guilds, list) else [],
            access_token=access_token,
            refresh_token=token_data.get("refresh_token", ""),
            expires_at=time.time() + expires_in,
        )

    def is_developer(self, session: Session) -> bool:
        """Check if a session user is a developer (has access to extensions and logs)."""
        return session.user_id in self.developer_user_ids

    def get_session(self, session_token: str) -> Session | None:
        """Retrieve a session by its token."""
        session = self.sessions.get(session_token)
        if session and session.expires_at > time.time():
            return session
        if session:
            del self.sessions[session_token]
        return None

    async def invalidate_session(self, session_token: str) -> None:
        """Remove a session from memory and MongoDB."""
        self.sessions.pop(session_token, None)
        try:
            await SessionRepository().delete(session_token)
        except Exception as e:
            logger.warning("Could not delete persisted session: %s", e)

    # --- Persistence (survive bot restarts) ---------------------------

    async def _persist(self, session: Session) -> None:
        """Best-effort write-through to MongoDB — never blocks a login."""
        try:
            await SessionRepository().upsert_doc(_session_to_doc(session))
        except Exception as e:
            logger.warning("Could not persist session to MongoDB: %s", e)

    async def restore_sessions(self) -> int:
        """Load persisted, unexpired sessions into memory (dashboard startup)."""
        try:
            docs = await SessionRepository().load_all_docs()
        except Exception as e:
            logger.warning("Could not restore Web UI sessions: %s", e)
            return 0
        now = time.time()
        restored = 0
        for doc in docs:
            try:
                session = _doc_to_session(doc)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("Skipping malformed persisted session: %s", e)
                continue
            if session.expires_at > now:
                self.sessions[session.session_token] = session
                restored += 1
        if restored:
            logger.info("Restored %d Web UI session(s) from MongoDB.", restored)
        return restored

    async def purge_expired(self) -> int:
        """Drop expired sessions from memory and MongoDB; returns memory count."""
        now = time.time()
        expired = [k for k, v in self.sessions.items() if v.expires_at <= now]
        for k in expired:
            del self.sessions[k]
        try:
            await SessionRepository().delete_expired(now)
        except Exception as e:
            logger.debug("Persisted-session purge failed: %s", e)
        if expired:
            logger.info("Purged %d expired Web UI session(s).", len(expired))
        return len(expired)

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
