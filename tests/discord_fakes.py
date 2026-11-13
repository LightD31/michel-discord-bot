"""Stand-ins for the Discord objects an extension handler touches.

A handler's tests call the command's callback directly with these
(:func:`run`), injecting an in-memory repository through the extension's
``_repo()``. Each fake records what the handler did to it (messages sent,
edits, DMs, roles), so a test asserts on the outcome rather than on calls.
Only what the handlers use is modelled; add to it as tests need more.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace
from typing import Any

from interactions import Permissions
from interactions.client.errors import Forbidden, NotFound

_ids = itertools.count(1000)


def discord_error(cls: type = Forbidden, status: int = 403) -> Exception:
    """A Discord HTTP error as interactions.py raises it (Forbidden, NotFound…)."""
    return cls(SimpleNamespace(status=status, reason=cls.__name__), response_data={})


def not_found() -> Exception:
    return discord_error(NotFound, 404)


class FakeUser:
    def __init__(self, uid: int | None = None, name: str = "membre", *, bot: bool = False):
        self.id = uid if uid is not None else next(_ids)
        self.username = name
        self.display_name = name
        self.global_name = name
        self.mention = f"<@{self.id}>"
        self.bot = bot
        self.avatar_url = f"https://cdn.example/{self.id}.png"
        self.dms: list[str] = []
        self.dms_closed = False

    async def send(self, content: str | None = None, **kwargs: Any) -> FakeMessage:
        if self.dms_closed:
            raise discord_error()
        self.dms.append(content or "")
        return FakeMessage(content=content, **kwargs)

    def __eq__(self, other: object) -> bool:
        return getattr(other, "id", None) == self.id

    def __hash__(self) -> int:
        return hash(self.id)


class FakeRole:
    def __init__(
        self,
        rid: int | None = None,
        name: str = "rôle",
        *,
        position: int = 1,
        mentionable=False,
        permissions: Permissions = Permissions.NONE,
        managed: bool = False,
    ):
        self.id = rid if rid is not None else next(_ids)
        self.name = name
        self.position = position
        self.mentionable = mentionable
        self.permissions = permissions
        self.managed = managed
        self.mention = f"<@&{self.id}>"


class FakeMember(FakeUser):
    """A member of a guild: roles, and the permissions they hold (everywhere)."""

    def __init__(
        self,
        uid: int | None = None,
        name: str = "membre",
        *,
        guild: FakeGuild | None = None,
        roles: list[FakeRole] | None = None,
        permissions: Permissions = Permissions.NONE,
        bot: bool = False,
    ):
        super().__init__(uid, name, bot=bot)
        self.guild = guild
        self.roles = list(roles or [])
        self.guild_permissions = permissions
        self.timeouts: list[Any] = []

    @property
    def top_role(self) -> FakeRole:
        return max(self.roles, key=lambda r: r.position, default=FakeRole(position=0))

    def has_permission(self, *permissions: Permissions) -> bool:
        if Permissions.ADMINISTRATOR in self.guild_permissions:
            return True
        return all(p in self.guild_permissions for p in permissions)

    def has_role(self, *roles: Any) -> bool:
        wanted = {int(getattr(r, "id", r)) for r in roles}
        return any(r.id in wanted for r in self.roles)

    async def add_role(self, role: Any, reason: str | None = None) -> None:
        rid = int(getattr(role, "id", role))
        if not self.has_role(rid):
            self.roles.append(role if isinstance(role, FakeRole) else FakeRole(rid))

    async def remove_role(self, role: Any, reason: str | None = None) -> None:
        rid = int(getattr(role, "id", role))
        self.roles = [r for r in self.roles if r.id != rid]

    async def timeout(
        self, communication_disabled_until: Any = None, reason: str | None = None
    ) -> None:
        self.timeouts.append(communication_disabled_until)


class FakeMessage:
    def __init__(
        self,
        mid: int | None = None,
        *,
        content: str | None = None,
        embeds: list[Any] | None = None,
        embed: Any = None,
        author: Any = None,
        channel: FakeChannel | None = None,
        components: Any = None,
        **_: Any,
    ):
        self.id = mid if mid is not None else next(_ids)
        self.content = content
        self.embeds = list(embeds or ([embed] if embed is not None else []))
        self.components = components
        self.author = author
        self.channel = channel
        self.guild = channel.guild if channel is not None else None
        self.reactions: list[Any] = []
        self.added_reactions: list[str] = []
        self.edits: list[dict[str, Any]] = []
        self.replies: list[str] = []
        self.reply_kwargs: list[dict[str, Any]] = []
        self.deleted = False

    async def edit(self, **kwargs: Any) -> FakeMessage:
        self.edits.append(kwargs)
        if "embeds" in kwargs:
            self.embeds = list(kwargs["embeds"] or [])
        if "embed" in kwargs:
            self.embeds = [kwargs["embed"]]
        if "content" in kwargs:
            self.content = kwargs["content"]
        return self

    async def reply(self, content: str | None = None, **kwargs: Any) -> FakeMessage:
        self.replies.append(content or "")
        self.reply_kwargs.append(kwargs)
        return FakeMessage(content=content, channel=self.channel)

    async def add_reaction(self, emoji: Any) -> None:
        self.added_reactions.append(str(emoji))

    async def clear_all_reactions(self) -> None:
        self.reactions = []

    async def delete(self, **_: Any) -> None:
        self.deleted = True


class FakeChannel:
    def __init__(
        self,
        cid: int | None = None,
        name: str = "général",
        *,
        guild: FakeGuild | None = None,
        nsfw: bool = False,
    ):
        self.id = cid if cid is not None else next(_ids)
        self.name = name
        self.mention = f"<#{self.id}>"
        self.guild = guild
        self.nsfw = nsfw
        self.sent: list[FakeMessage] = []
        self.messages: dict[int, FakeMessage] = {}
        # Per-member permissions in this channel; members not listed hold their guild ones.
        self.overrides: dict[int, Permissions] = {}

    async def send(self, content: str | None = None, **kwargs: Any) -> FakeMessage:
        message = FakeMessage(content=content, channel=self, **kwargs)
        message.sent_with = kwargs  # allowed_mentions, components…
        self.sent.append(message)
        self.messages[message.id] = message
        return message

    async def fetch_message(self, message_id: Any) -> FakeMessage:
        try:
            return self.messages[int(message_id)]
        except (KeyError, ValueError):
            raise not_found() from None

    def permissions_for(self, member: Any) -> Permissions:
        if member.id in self.overrides:
            return self.overrides[member.id]
        return getattr(member, "guild_permissions", Permissions.NONE)


class FakeGuild:
    def __init__(self, gid: int = 1, name: str = "Serveur"):
        self.id = gid
        self.name = name
        self.members: dict[int, FakeMember] = {}
        self.roles: dict[int, FakeRole] = {}
        self.channels: dict[int, FakeChannel] = {}
        self.bans: list[tuple[Any, dict[str, Any]]] = []
        self.kicks: list[tuple[Any, dict[str, Any]]] = []
        self.me = self.add_member(FakeMember(99, "Michel", bot=True, roles=[FakeRole(position=50)]))

    def add_member(self, member: FakeMember) -> FakeMember:
        member.guild = self
        self.members[member.id] = member
        return member

    def add_channel(self, channel: FakeChannel) -> FakeChannel:
        channel.guild = self
        self.channels[channel.id] = channel
        return channel

    def get_member(self, uid: Any) -> FakeMember | None:
        return self.members.get(int(uid))

    async def fetch_member(self, uid: Any) -> FakeMember | None:
        return self.members.get(int(uid))

    def get_role(self, rid: Any) -> FakeRole | None:
        return self.roles.get(int(rid))

    def add_role(self, role: FakeRole) -> FakeRole:
        self.roles[role.id] = role
        return role

    async def fetch_role(self, rid: Any) -> FakeRole | None:
        return self.get_role(rid)

    def is_owner(self, uid: Any) -> bool:
        return int(uid) == getattr(self, "owner_id", -1)

    def get_channel(self, cid: Any) -> FakeChannel | None:
        return self.channels.get(int(cid))

    async def fetch_channel(self, cid: Any) -> FakeChannel | None:
        return self.get_channel(cid)

    async def ban(self, user: Any, **kwargs: Any) -> None:
        self.bans.append((user, kwargs))

    async def kick(self, user: Any, **kwargs: Any) -> None:
        self.kicks.append((user, kwargs))


class FakeBot:
    def __init__(self, guild: FakeGuild | None = None):
        self.user = FakeUser(99, "Michel", bot=True)
        self.guilds = [guild] if guild else []
        self.users: dict[int, FakeUser] = {}

    def _channels(self) -> dict[int, FakeChannel]:
        return {cid: ch for guild in self.guilds for cid, ch in guild.channels.items()}

    def get_channel(self, cid: Any) -> FakeChannel | None:
        return self._channels().get(int(cid))

    async def fetch_channel(self, cid: Any) -> FakeChannel | None:
        return self.get_channel(cid)

    def get_guild(self, gid: Any) -> FakeGuild | None:
        return next((g for g in self.guilds if g.id == int(gid)), None)

    async def fetch_guild(self, gid: Any) -> FakeGuild | None:
        return self.get_guild(gid)

    def get_user(self, uid: Any) -> FakeUser | None:
        uid = int(uid)
        for guild in self.guilds:
            if uid in guild.members:
                return guild.members[uid]
        return self.users.get(uid)

    async def fetch_user(self, uid: Any, **_: Any) -> FakeUser | None:
        return self.get_user(uid)

    async def fetch_member(self, uid: Any, gid: Any, **_: Any) -> FakeMember | None:
        guild = self.get_guild(gid)
        return guild.get_member(uid) if guild else None


class FakeCtx:
    """A slash, component or modal context: who, where, and every reply it got."""

    def __init__(
        self,
        author: FakeMember | FakeUser,
        channel: FakeChannel | None = None,
        *,
        bot: FakeBot | None = None,
        message: FakeMessage | None = None,
        custom_id: str = "",
    ):
        self.author = self.user = author
        self.channel = channel
        self.guild = channel.guild if channel is not None else None
        self.guild_id = self.guild.id if self.guild is not None else None
        self.channel_id = channel.id if channel is not None else None
        self.bot = self.client = bot or FakeBot(self.guild)
        self.message = message
        self.custom_id = custom_id
        self.replies: list[dict[str, Any]] = []
        self.deferred: bool | None = None
        self.origin_edits: list[dict[str, Any]] = []

    async def send(self, content: str | None = None, **kwargs: Any) -> FakeMessage:
        self.replies.append({"content": content, **kwargs})
        message = FakeMessage(content=content, channel=self.channel, author=self.bot.user, **kwargs)
        if self.channel is not None and not kwargs.get("ephemeral"):
            self.channel.messages[message.id] = message
        return message

    respond = send

    async def defer(self, ephemeral: bool = False, **_: Any) -> None:
        self.deferred = ephemeral

    async def edit_origin(self, **kwargs: Any) -> None:
        self.origin_edits.append(kwargs)

    async def edit(self, *_: Any, **kwargs: Any) -> None:
        self.origin_edits.append(kwargs)

    @property
    def texts(self) -> list[str]:
        """The text of every reply, in order (empty for embed-only replies)."""
        return [reply.get("content") or "" for reply in self.replies]

    @property
    def last(self) -> str:
        return self.texts[-1] if self.replies else ""


async def run(command: Any, ext: Any, ctx: FakeCtx, *args: Any, **options: Any) -> Any:
    """Call a slash command, subcommand, button or modal callback as interactions.py would."""
    callback = getattr(command, "callback", command)
    return await callback(ext, ctx, *args, **options)


def bare_extension(cls: type, bot: Any = None, **attributes: Any) -> Any:
    """An extension instance without interactions' wiring (no client, no listeners)."""
    ext = object.__new__(cls)
    ext.bot = bot
    for name, value in attributes.items():
        setattr(ext, name, value)
    return ext


__all__ = [
    "FakeBot",
    "FakeChannel",
    "FakeCtx",
    "FakeGuild",
    "FakeMember",
    "FakeMessage",
    "FakeRole",
    "FakeUser",
    "bare_extension",
    "discord_error",
    "not_found",
    "run",
]
