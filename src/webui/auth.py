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
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlencode

from aiohttp import ClientError

from src.core import logging as logutil
from src.core.config import global_config
from src.core.http import http_client
from src.webui.sessions import SessionRepository, expires_dt_from_ts

logger = logutil.init_logger("webui.auth")

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_OAUTH2_URL = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"

# Server-side session lifetime; the session cookie's max-age matches it.
SESSION_TTL_SECONDS = 86400
# Any Discord account can log in, and each login used to add a session for
# 24 h with no limit: the oldest of a user's sessions goes past this many.
MAX_SESSIONS_PER_USER = 5
# What a session keeps of each of the user's guilds (Discord sends more).
GUILD_FIELDS = ("id", "name", "icon", "owner", "permissions")


def configured_developer_ids() -> list[str]:
    """``webui.developerUserIds`` as the config store holds it now, as strings."""
    webui = global_config().get("webui")
    ids = webui.get("developerUserIds") if isinstance(webui, dict) else None
    return [str(i).strip() for i in ids] if isinstance(ids, list) else []


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
    return doc


def _kept_guilds(guilds: object, shared_guild_ids: set[str] | None) -> list[dict[str, Any]]:
    """The user's guilds a session keeps (see ``DiscordOAuth._session_from_payload``)."""
    if not isinstance(guilds, list):
        return []
    kept = []
    for guild in guilds:
        if not isinstance(guild, dict) or "id" not in guild:
            continue
        if shared_guild_ids is not None and str(guild["id"]) not in shared_guild_ids:
            continue
        kept.append({key: guild[key] for key in GUILD_FIELDS if key in guild})
    return kept


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
        developer_ids: Callable[[], Iterable[object]] = configured_developer_ids,
        shared_guild_ids: Callable[[], Iterable[str]] | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        # Read on every check, so removing someone from the list in the config
        # takes their developer rights away at once, without a restart.
        self._developer_ids = developer_ids
        # The guilds the bot is in, read at each login: a session keeps only
        # those of the user's guilds (up to 200 otherwise, kept 24 h).
        self._shared_guild_ids = shared_guild_ids
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

        # None (keep them all) until the bot has its guilds: a login while the
        # gateway is still connecting mustn't leave the user with none.
        shared = set(map(str, self._shared_guild_ids())) if self._shared_guild_ids else None
        shared = shared or None
        session = self._session_from_payload(token_data, user_data, guilds, shared)
        if session is None:
            return None

        await self._evict_oldest_sessions(session.user_id, keep=MAX_SESSIONS_PER_USER - 1)
        self.sessions[session.token_hash] = session
        await self._persist(session)
        logger.info(f"User logged in: {session.username} ({session.user_id})")
        return session

    @staticmethod
    def _session_from_payload(
        token_data: dict[str, Any],
        user_data: dict[str, Any],
        guilds: list,
        shared_guild_ids: set[str] | None = None,
    ) -> Session | None:
        """Build a Session from Discord's responses; None if a field is missing.

        The OAuth tokens are deliberately not kept: the session outlives them
        by design (see the module docstring) and never calls Discord again.
        Of the user's guilds it keeps the ones the bot is in (all of them when
        *shared_guild_ids* is ``None``: a dashboard without a bot), and of each
        only :data:`GUILD_FIELDS`.
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
            guilds=_kept_guilds(guilds, shared_guild_ids),
            expires_at=time.time() + SESSION_TTL_SECONDS,
        )

    async def _evict_oldest_sessions(self, user_id: str, *, keep: int) -> None:
        """Drop *user_id*'s oldest sessions so that at most *keep* remain."""
        mine = sorted(
            (s for s in self.sessions.values() if s.user_id == user_id),
            key=lambda s: s.expires_at,
        )
        for old in mine[: max(0, len(mine) - keep)]:
            self.sessions.pop(old.token_hash, None)
            try:
                await SessionRepository().delete(old.token_hash)
            except Exception as e:
                logger.warning("Could not delete an evicted session: %s", e)

    def is_developer(self, session: Session) -> bool:
        """Check if a session user is a developer (has access to extensions and logs)."""
        return session.user_id in {str(i) for i in self._developer_ids()}

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
