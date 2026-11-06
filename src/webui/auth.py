"""
Discord OAuth2 authentication for the Web UI.

The OAuth token pair is used once, at login, to read the user's identity and
guild list, then dropped: nothing past the login needs it. A session lives
:data:`SESSION_TTL_SECONDS` server-side, the same as its cookie, and is keyed
everywhere — memory and MongoDB — by the SHA-256 of its cookie value, so a
leaked database or backup holds nothing that can be replayed as a cookie.
"""

import hashlib
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

# Server-side session lifetime; the session cookie's max-age matches it.
SESSION_TTL_SECONDS = 86400

# Version of the persisted session document. Documents without it predate
# token hashing — they hold the raw cookie value and OAuth tokens — and are
# deleted rather than restored (their users log in again, once).
SESSION_DOC_VERSION = 2


def hash_token(session_token: str) -> str:
    """The key a session is stored under: SHA-256 of its cookie value.

    The token is 256 random bits, so an unsalted fast hash is enough: there is
    nothing to brute-force.
    """
    return hashlib.sha256(session_token.encode()).hexdigest()


@dataclass
class Session:
    """Represents an authenticated user session."""

    user_id: str
    username: str
    avatar: str | None
    guilds: list
    expires_at: float
    # The cookie value. Known only to the process that minted the session and
    # never persisted: a session restored from MongoDB carries "" here.
    session_token: str = field(default_factory=lambda: secrets.token_urlsafe(32), compare=False)
    token_hash: str = ""

    def __post_init__(self) -> None:
        if not self.token_hash:
            self.token_hash = hash_token(self.session_token)


def _session_to_doc(session: Session) -> dict[str, Any]:
    """Serialize a session for MongoDB (``_id`` = token hash, TTL helper field)."""
    doc = asdict(session)
    del doc["session_token"]
    doc["_id"] = doc.pop("token_hash")
    doc["expires_dt"] = expires_dt_from_ts(session.expires_at)
    doc["v"] = SESSION_DOC_VERSION
    return doc


def _doc_to_session(doc: dict[str, Any]) -> Session:
    return Session(
        user_id=doc["user_id"],
        username=doc["username"],
        avatar=doc.get("avatar"),
        guilds=doc.get("guilds", []),
        expires_at=float(doc["expires_at"]),
        session_token="",
        token_hash=doc["_id"],
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

        self.sessions[session.token_hash] = session
        await self._persist(session)
        logger.info(f"User logged in: {session.username} ({session.user_id})")
        return session

    @staticmethod
    def _session_from_payload(
        token_data: dict[str, Any],
        user_data: dict[str, Any],
        guilds: list,
    ) -> Session | None:
        """Build a Session from Discord's responses; None if a field is missing.

        The OAuth tokens are deliberately not kept: the session outlives them
        by design (see the module docstring) and never calls Discord again.
        """
        access_token = token_data.get("access_token")
        user_id = user_data.get("id")
        if not access_token or not user_id:
            logger.error("Discord OAuth payload incomplete (token or user id missing)")
            return None
        return Session(
            user_id=str(user_id),
            username=user_data.get("global_name") or user_data.get("username") or str(user_id),
            avatar=user_data.get("avatar"),
            guilds=guilds if isinstance(guilds, list) else [],
            expires_at=time.time() + SESSION_TTL_SECONDS,
        )

    def is_developer(self, session: Session) -> bool:
        """Check if a session user is a developer (has access to extensions and logs)."""
        return session.user_id in self.developer_user_ids

    def get_session(self, session_token: str) -> Session | None:
        """Retrieve a session by its cookie value."""
        key = hash_token(session_token)
        session = self.sessions.get(key)
        if session and session.expires_at > time.time():
            return session
        if session:
            del self.sessions[key]
        return None

    async def invalidate_session(self, session_token: str) -> None:
        """Remove a session (given its cookie value) from memory and MongoDB."""
        key = hash_token(session_token)
        self.sessions.pop(key, None)
        try:
            await SessionRepository().delete(key)
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
        """Load persisted, unexpired sessions into memory (dashboard startup).

        Pre-hashing documents (no ``v``) are deleted, not restored: they hold a
        replayable cookie value and the user's OAuth tokens.
        """
        repo = SessionRepository()
        try:
            purged = await repo.delete_older_versions(SESSION_DOC_VERSION)
            if purged:
                logger.info("Deleted %d legacy Web UI session(s) stored in clear.", purged)
            docs = await repo.load_all_docs()
        except Exception as e:
            logger.warning("Could not restore Web UI sessions: %s", e)
            return 0
        now = time.time()
        restored = 0
        for doc in docs:
            if doc.get("v") != SESSION_DOC_VERSION:
                continue
            try:
                session = _doc_to_session(doc)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("Skipping malformed persisted session: %s", e)
                continue
            if session.expires_at > now:
                self.sessions[session.token_hash] = session
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
