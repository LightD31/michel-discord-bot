"""Extension Welcome — messages de bienvenue et d'au revoir personnalisés par serveur.

A newcomer gets the server's welcome message (its card, buttons to the rules,
the role menu and ``/aide``), the arrival role, and optionally a copy by DM.
Under the message, "👋 Souhaiter la bienvenue" lets the others greet them,
each click adding a name (A8; ``features/welcome/greetings.py``). After 7
days the hourly task takes the button off (P14).
"""

import asyncio
from typing import Any

from interactions import (
    ActionRow,
    Button,
    ButtonStyle,
    Client,
    ComponentContext,
    Extension,
    File,
    IntervalTrigger,
    Task,
    component_callback,
    listen,
)
from interactions.api.events import MemberAdd, MemberRemove

from extensions.welcome._common import WELCOME, WelcomeConfig, logger, message_values
from features.reactionroles.repository import ReactionRolesRepository
from features.welcome import GreetingRepository, greeting_line, render_welcome_card, with_greetings
from src.core import clock, log_context
from src.core.logging import log_failure
from src.core.text import pick_weighted_message
from src.discord_ext.channels import fetch_guild_channel
from src.discord_ext.help_button import help_button
from src.discord_ext.look import ok, oops
from src.discord_ext.mentions import NO_PINGS, everyone_if_written
from src.discord_ext.messages import channel_url, edit_message_if_changed, guild_id_of, jump_url
from src.discord_ext.role_grants import grantable_role

GREET_BUTTON_ID = "bienvenue:saluer"


async def _fetch_avatar_bytes(member) -> bytes | None:
    """Best-effort download of the member's avatar as PNG bytes.

    interactions.py exposes ``display_avatar`` as an :class:`Asset` whose
    ``fetch()`` method talks to Discord's CDN through the bot's authenticated
    HTTP client — the recommended path. We force the PNG extension so animated
    avatars decode to a still frame.
    """
    asset = getattr(member, "display_avatar", None) or getattr(member, "avatar", None)
    if asset is None:
        return None
    try:
        return await asset.fetch(extension=".png", size=256)
    except Exception as e:
        logger.warning("Could not fetch avatar for %s: %s", getattr(member, "id", "?"), e)
        return None


def _server_name_subtitle(guild_name: str, member_count: int | None) -> str:
    if member_count:
        return f"{guild_name} · {member_count} membres"
    return guild_name


def _target_channel(guild, serv_config: dict):
    """The configured welcome channel, else the server's system channel; ``None`` if neither."""
    channel_id = serv_config.get("welcomeChannelId")
    if channel_id:
        return guild.get_channel(channel_id)
    return guild.system_channel


def link_buttons(
    guild_id: Any, settings: dict, roles_message_id: str | None = None
) -> list[Button]:
    """The welcome message's links: the rules, the role menu (where configured).

    With *roles_message_id*, a role menu posted in the roles channel, the link
    opens that menu rather than the top of the channel (P14).
    """
    buttons = []
    rules = str(settings.get("welcomeRulesChannelId") or "")
    if rules.isdigit():
        url = channel_url(guild_id, rules)
        buttons.append(Button(style=ButtonStyle.URL, label="Règles", emoji="📜", url=url))
    roles = str(settings.get("welcomeRolesChannelId") or "")
    if roles.isdigit():
        url = (
            jump_url(guild_id, roles, roles_message_id)
            if roles_message_id
            else channel_url(guild_id, roles)
        )
        buttons.append(
            Button(style=ButtonStyle.URL, label="Choisir mes rôles", emoji="🎭", url=url)
        )
    return buttons


async def roles_menu_message(guild_id: Any, settings: dict) -> str | None:
    """The role menu to link from the welcome, if one is posted in the roles channel."""
    roles = str(settings.get("welcomeRolesChannelId") or "")
    if not roles.isdigit():
        return None
    try:
        return await ReactionRolesRepository(str(guild_id)).first_message_in(roles)
    except Exception as e:  # the channel link is good enough
        log_failure(logger, "Role menu of the welcome not read on %s: %s", guild_id, e)
        return None


def greet_button() -> Button:
    return Button(
        style=ButtonStyle.SUCCESS,
        label="Souhaiter la bienvenue",
        emoji="👋",
        custom_id=GREET_BUTTON_ID,
    )


def welcome_components(
    guild_id: Any, settings: dict, *, in_dm: bool = False, roles_message_id: str | None = None
) -> list[ActionRow]:
    """The buttons under a welcome message; in a DM, only the links."""
    buttons = link_buttons(guild_id, settings, roles_message_id)
    if not in_dm:
        if settings.get("welcomeHelpButton", True):
            buttons.append(help_button())
        if settings.get("welcomeGreetButton", True):
            buttons.append(greet_button())
    return [ActionRow(*buttons)] if buttons else []


def without_greet_button(rows: list[Any]) -> list[ActionRow]:
    """A message's rows, the greet button taken out (and a row left empty with it)."""
    kept = []
    for row in rows:
        buttons = [
            c
            for c in getattr(row, "components", [])
            if getattr(c, "custom_id", None) != GREET_BUTTON_ID
        ]
        if buttons:
            kept.append(ActionRow(*buttons))
    return kept


class WelcomeExtension(Extension):
    MODULE_KEYS = ("moduleWelcome",)

    def __init__(self, bot: Client):
        self.bot = bot
        # One click at a time rewrites a message's names: each edit shows the latest count.
        self._greet_lock = asyncio.Lock()

    @listen()
    async def on_startup(self):
        # The TTL that ends a welcome's greetings after 7 days (logged, never raised).
        for gid in WELCOME.guild_ids():
            await GreetingRepository(gid).ensure_indexes()
        if not self.close_greetings.running:
            self.close_greetings.start()

    @Task.create(IntervalTrigger(hours=1))
    async def close_greetings(self) -> None:
        """Take the 👋 button off the welcomes whose 7 days are over."""
        now = clock.now()
        for gid in WELCOME.guild_ids():
            with log_context.scope(guild_id=gid):
                repo = GreetingRepository(gid)
                try:
                    due = await repo.due_to_close(now)
                except Exception as e:
                    log_failure(logger, "Greetings to close not read on %s: %s", gid, e)
                    continue
                for greeting in due:
                    try:
                        if await self._take_greet_button_off(gid, greeting):
                            await repo.mark_closed(greeting.message_id)
                    except Exception as e:  # an outage: the next run tries again
                        log_failure(logger, "Greet button not removed on %s: %s", gid, e)

    async def _take_greet_button_off(self, guild_id: str, greeting: Any) -> bool:
        """``True`` once the message has no greet button (or is gone): nothing left to do."""
        if not greeting.channel_id:  # opened before P14: the channel wasn't kept
            return True
        channel = await fetch_guild_channel(
            self.bot, guild_id, greeting.channel_id, logger=logger, raise_outages=True
        )
        if channel is None or not hasattr(channel, "fetch_message"):
            return True
        message = await channel.fetch_message(int(greeting.message_id))
        if message is None:
            return True
        rows = without_greet_button(message.components or [])
        await edit_message_if_changed(message, components=rows, logger=logger)
        return True

    @listen()
    async def on_member_add(self, event: MemberAdd):
        """A listener that sends a message when a member joins a guild."""
        logger.info("Member %s joined the server %s", event.member.username, event.guild.name)
        if not WELCOME.enabled(event.guild.id):
            logger.info("Server not enabled")
            return
        serv_config = WELCOME.get(event.guild.id)
        await self._grant_arrival_role(event.member, event.guild, serv_config)

        channel = _target_channel(event.guild, serv_config)
        if channel is None:
            logger.warning("No welcome channel available on %s", event.guild.name)
            return
        filled_message = pick_weighted_message(
            serv_config,
            "welcomeMessageList",
            "Bienvenue {mention} !",
            **message_values(event.member, event.guild),
        )
        files = await self._build_card_files(
            event.member,
            event.guild,
            title="Bienvenue",
            enabled=serv_config.get("welcomeImageEnabled", True),
        )
        roles_message_id = await roles_menu_message(event.guild.id, serv_config)
        # The admins' template filled with a mention: it may ping @everyone.
        message = await channel.send(
            filled_message,
            files=files,
            components=welcome_components(
                event.guild.id, serv_config, roles_message_id=roles_message_id
            ),
            allowed_mentions=everyone_if_written(filled_message),
        )
        if serv_config.get("welcomeGreetButton", True) and message is not None:
            try:
                await GreetingRepository(event.guild.id).open(
                    str(message.id), str(event.member.id), clock.now(), str(channel.id)
                )
            except Exception as e:  # the welcome is out: a count lost is no reason to stop
                log_failure(logger, "Greetings not opened on %s: %s", event.guild.id, e)
        if serv_config.get("welcomeDmCopy"):
            await self._send_dm_copy(
                event.member, event.guild, serv_config, filled_message, roles_message_id
            )

    async def _grant_arrival_role(self, member, guild, serv_config: dict) -> None:
        role_id = serv_config.get("welcomeArrivalRoleId")
        if not role_id:
            return
        # Re-checked at every arrival: the role may have become a staff role since.
        role, refusal = grantable_role(guild, role_id)
        if role is None:
            logger.warning("Arrival role %s not given on %s: %s", role_id, guild.id, refusal)
            return
        try:
            await member.add_role(role, reason="Rôle d'arrivée")
        except Exception as e:
            log_failure(logger, "Arrival role not given on %s: %s", guild.id, e)

    async def _send_dm_copy(
        self, member, guild, serv_config: dict, text: str, roles_message_id: str | None = None
    ) -> None:
        try:
            await member.send(
                f"-# Depuis {guild.name}\n{text}",
                components=welcome_components(
                    guild.id, serv_config, in_dm=True, roles_message_id=roles_message_id
                ),
                allowed_mentions=NO_PINGS,
            )
        except Exception as e:  # closed DMs are the member's choice
            logger.info("Welcome DM not sent to %s on %s: %s", member.id, guild.id, e)

    @component_callback(GREET_BUTTON_ID)
    async def on_greet(self, ctx: ComponentContext) -> None:
        """ "👋 Souhaiter la bienvenue": the clicker's name joins the message's line."""
        guild_id = guild_id_of(ctx)
        message = ctx.message
        if message is None:  # a button always comes with its message
            return
        repo = GreetingRepository(guild_id)
        async with self._greet_lock:
            outcome = await repo.greet(str(message.id), str(ctx.author.id), clock.now())
            if outcome == "self":
                await ctx.send("😄 C'est toi qu'on accueille ! Bienvenue à toi.", ephemeral=True)
                return
            if outcome == "already":
                await ctx.send(ok("Tu lui as déjà souhaité la bienvenue."), ephemeral=True)
                return
            if outcome == "expired":
                await ctx.send(
                    oops("Ce message a plus de 7 jours : l'accueil est passé.", icon="⏱️"),
                    ephemeral=True,
                )
                return
            names = [_display_name(ctx.guild, uid) for uid in outcome.greeters]
            line = greeting_line(names, len(outcome.greeters))
            await ctx.edit_origin(
                content=with_greetings(message.content, line), allowed_mentions=NO_PINGS
            )

    @listen()
    async def on_member_remove(self, event: MemberRemove):
        """A listener that sends a message when a member leaves a guild."""
        logger.info("Member %s left the server %s", event.member.username, event.guild.name)
        if not WELCOME.enabled(event.guild.id):
            logger.info("Server not enabled")
            return
        serv_config: dict = WELCOME.get(event.guild.id)
        channel = _target_channel(event.guild, serv_config)
        if channel is None:
            logger.warning("No welcome channel available on %s", event.guild.name)
            return
        filled_message = pick_weighted_message(
            serv_config,
            "leaveMessageList",
            "{username} nous a quittés.",
            **message_values(event.member, event.guild),
        )
        files = await self._build_card_files(
            event.member,
            event.guild,
            title="Au revoir",
            enabled=serv_config.get("welcomeImageEnabled", True),
        )
        await channel.send(
            filled_message, files=files, allowed_mentions=everyone_if_written(filled_message)
        )

    async def _build_card_files(self, member, guild, *, title: str, enabled: bool) -> list[File]:
        if not enabled:
            return []
        try:
            avatar = await _fetch_avatar_bytes(member)
            buffer = await asyncio.to_thread(  # Pillow work, off the event loop
                render_welcome_card,
                avatar_bytes=avatar,
                username=member.username,
                title=title,
                subtitle=_server_name_subtitle(guild.name, getattr(guild, "member_count", None)),
            )
            return [File(file=buffer, file_name="welcome.png")]
        except Exception as e:
            logger.warning("Could not render welcome card for %s: %s", member.username, e)
            return []


def _display_name(guild: Any, user_id: str) -> str:
    member = guild.get_member(int(user_id)) if guild is not None else None
    return getattr(member, "display_name", None) or ""


def setup(bot: Client) -> None:
    WelcomeExtension(bot)


__all__ = ["WelcomeConfig", "WelcomeExtension", "setup"]
