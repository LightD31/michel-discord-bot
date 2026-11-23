"""The member space (C6): what Michel keeps about a member, and what they can set themselves.

Any Discord account can log in to the dashboard; a member who manages no
server used to land on a dead end. These routes are their space, for each
server they share with Michel:

- ``GET /api/me/servers`` — the servers, and the sections each one has on;
- ``GET /api/me/servers/{id}`` — their XP card, reminders, birthday, Tricount
  balances, Secret Santa, badges and the agenda's length (:mod:`src.webui.member_space`);
- ``GET /api/me/servers/{id}/agenda`` — the coming days, as ``/agenda`` shows them;
- reminders created, changed and deleted; the birthday set or removed; a
  Tricount repayment recorded, in full or in part, paid or received; the Secret
  Santa joined or left, and its wish list;
- ``POST /api/me/data/export`` and ``…/delete`` — ``/mes-donnees`` from the
  dashboard, on every server at once, as the command does.

**Authorization** is the member tier, :meth:`WebUIContext.require_guild_member`:
a live membership check of the session's user in the server. Every route acts
on ``session.user_id`` and never reads a member id from the request: a body
names a reminder, a group, the person paid back, never whose data it is, and
each write is filtered on the member (a reminder id that isn't theirs changes
nothing). A section's writes answer 404 where its module is off. Nothing here
is recorded in the audit log, which holds what admins change on a server; the
writes go through the mutation limiter like every other.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from features.birthday import TIMEZONES, BirthdayEntry, BirthdayRepository, parse_birth_date
from features.reminders import (
    MAX_REMINDER_LENGTH,
    MAX_REMINDERS_PER_MEMBER,
    Reminder,
    ReminderRepository,
)
from features.reminders.when import EXAMPLES, parse_when, reading
from features.secretsanta import SecretSantaRepository
from features.secretsanta.dates import participant_expiry
from features.tricount.balances import TRANSFER, compute_balances, settlement_plan
from features.tricount.repository import TricountRepository
from features.userdata import UserDataRepository, export_json
from features.userdata.summary import NOT_COVERED, kept_reasons
from src.core import clock
from src.core import logging as logutil
from src.core.db import mongo_manager
from src.core.errors import DatabaseError
from src.discord_ext.secretsanta import WISHLIST_LIMIT, refresh_announcement
from src.webui import member_space
from src.webui.auth import Session
from src.webui.context import WebUIContext
from src.webui.routes.agenda import agenda_on_bot
from src.webui.routes.secretsanta import member_names

logger = logutil.init_logger("webui.routes.me")

MODULE_OFF = "Ce module n'est pas activé sur ce serveur."
WHEN_HINT = f"Pas encore compris, ou déjà passé. Exemples : {EXAMPLES}."


class ReminderBody(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_REMINDER_LENGTH)
    when: str = Field(min_length=1, max_length=60)
    frequency: Literal["daily", "weekly", "monthly", "yearly"] | None = None


class BirthdayBody(BaseModel):
    date: str = Field(min_length=1, max_length=40)
    timezone: str = Field(default=clock.LOCAL_TZ.key, max_length=64)
    hideYear: bool = False


class SettleBody(BaseModel):
    # The other member of the plan's line: whom the member paid back, or who paid them.
    to: str = Field(pattern=r"^[1-9][0-9]{0,19}$")
    cents: int = Field(ge=1, le=10_000_000)
    # "paid": the member paid *to* back; "received": *to* paid the member back (P16).
    direction: Literal["paid", "received"] = "paid"


class WishlistBody(BaseModel):
    text: str = Field(default="", max_length=WISHLIST_LIMIT)


class DeleteBody(BaseModel):
    confirm: Literal[True]


def require_section(server_id: str, section: str) -> None:
    if section not in member_space.sections_on(server_id):
        raise HTTPException(status_code=404, detail=MODULE_OFF)


def parse_reminder_time(text: str) -> Any:
    try:
        return parse_when(text, clock.now())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"📅 {e}") from e


async def server_overview(server_id: str, user_id: str) -> dict[str, Any]:
    """Every section the server has on; a section that can't be read is listed as such."""
    sections = member_space.sections_on(server_id)
    readers = {
        "xp": member_space.xp_card,
        "reminders": member_space.reminders,
        "birthday": member_space.birthday,
        "tricount": member_space.tricount,
        "santa": member_space.santa,
        "badges": member_space.badges,
        "agenda": member_space.agenda,
    }
    out: dict[str, Any] = {"sections": sections, "unavailable": []}
    for name in sections:
        try:
            out[name] = await readers[name](server_id, user_id)
        except DatabaseError as e:
            logger.warning("Member space: %s of %s unreadable: %s", name, server_id, e)
            out[name] = None
            out["unavailable"].append(name)
    if "tricount" in sections:
        out["currency"] = member_space.currency(server_id)
    return out


def create_router(ctx: WebUIContext) -> APIRouter:
    router = APIRouter()

    async def names_of(server_id: str, ids: set[str]) -> dict[str, str]:
        """Display names of the members a section mentions; ids alone without a bot."""
        if not ids or not ctx.bot:
            return {}
        return await ctx.call_bot(
            member_names,
            ctx.bot,
            server_id,
            sorted(int(i) for i in ids),
            what="names for the member space",
        )

    def shared_guilds(session: Session) -> list[dict[str, Any]]:
        bot_ids = {str(g.id) for g in ctx.bot.guilds} if ctx.bot else None
        return [
            g
            for g in session.guilds
            if isinstance(g, dict) and (bot_ids is None or str(g.get("id")) in bot_ids)
        ]

    @router.get("/api/me/servers")
    async def api_my_servers(request: Request):
        """The servers the user shares with Michel, each with its sections on."""
        session = ctx.require_session(request)
        servers = []
        for guild in shared_guilds(session):
            gid = str(guild.get("id"))
            if not await ctx.user_is_member(session, gid):
                continue
            servers.append(
                {
                    "id": gid,
                    "name": guild.get("name") or gid,
                    "icon": guild.get("icon"),
                    "sections": member_space.sections_on(gid),
                }
            )
        return JSONResponse({"servers": servers})

    @router.get("/api/me/servers/{server_id}")
    async def api_my_server(request: Request, server_id: str):
        session = await ctx.require_guild_member(request, server_id)
        out = await server_overview(server_id, session.user_id)
        ids = {line["to"] for g in out.get("tricount") or [] for line in g["owes"]}
        ids |= {line["from"] for g in out.get("tricount") or [] for line in g["owed"]}
        out["names"] = await names_of(server_id, ids)
        return JSONResponse(out)

    @router.get("/api/me/servers/{server_id}/agenda")
    async def api_my_agenda(request: Request, server_id: str):
        """The server's agenda, as /agenda shows it to a member (P25)."""
        await ctx.require_guild_member(request, server_id)
        require_section(server_id, "agenda")
        data = await ctx.call_bot(agenda_on_bot, ctx.bot, server_id, what=f"agenda of {server_id}")
        return JSONResponse(data)

    @router.get("/api/me/when")
    async def api_when(request: Request, q: str = ""):
        """« quand » read back as it is typed: "samedi 9h" → "samedi 21 novembre à 09:00"."""
        ctx.require_session(request)
        now = clock.now()
        try:
            found = parse_when(q[:60], now)
        except ValueError:
            # A hint, not the exception's text: saving says exactly what is wrong.
            return JSONResponse({"error": WHEN_HINT})
        return JSONResponse({"reading": reading(found, now), "at": found.isoformat()})

    # --- Reminders -------------------------------------------------------

    @router.post("/api/me/servers/{server_id}/reminders")
    async def api_add_reminder(request: Request, server_id: str, body: ReminderBody):
        session = await ctx.require_guild_member(request, server_id)
        require_section(server_id, "reminders")
        when = parse_reminder_time(body.when)
        repo = ReminderRepository(server_id)
        if await repo.count_for_user(session.user_id) >= MAX_REMINDERS_PER_MEMBER:
            raise HTTPException(
                status_code=409,
                detail=f"Tu as déjà {MAX_REMINDERS_PER_MEMBER} rappels sur ce serveur : "
                "supprimes-en un d'abord.",
            )
        reminder = Reminder(
            user_id=session.user_id,
            message=body.message.strip(),
            remind_time=when,
            frequency=body.frequency,
        )
        reminder_id = await repo.add(reminder)
        logger.info("Reminder added from the member space by %s on %s", session.user_id, server_id)
        return JSONResponse({"status": "ok", "id": reminder_id})

    @router.put("/api/me/servers/{server_id}/reminders/{reminder_id}")
    async def api_edit_reminder(
        request: Request, server_id: str, reminder_id: str, body: ReminderBody
    ):
        session = await ctx.require_guild_member(request, server_id)
        require_section(server_id, "reminders")
        when = parse_reminder_time(body.when)
        changed = await ReminderRepository(server_id).update_mine(
            session.user_id,
            reminder_id,
            message=body.message.strip(),
            remind_time=when,
            frequency=body.frequency,
        )
        if not changed:
            raise HTTPException(status_code=404, detail="Rappel introuvable.")
        return JSONResponse({"status": "ok"})

    @router.delete("/api/me/servers/{server_id}/reminders/{reminder_id}")
    async def api_delete_reminder(request: Request, server_id: str, reminder_id: str):
        session = await ctx.require_guild_member(request, server_id)
        require_section(server_id, "reminders")
        gone = await ReminderRepository(server_id).delete_mine(session.user_id, [reminder_id])
        if not gone:
            raise HTTPException(status_code=404, detail="Rappel introuvable.")
        return JSONResponse({"status": "ok"})

    # --- Birthday --------------------------------------------------------

    @router.put("/api/me/servers/{server_id}/birthday")
    async def api_set_birthday(request: Request, server_id: str, body: BirthdayBody):
        session = await ctx.require_guild_member(request, server_id)
        require_section(server_id, "birthday")
        try:
            day = parse_birth_date(body.date, today=clock.local_now().date())
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"📅 {e}") from e
        if body.timezone not in TIMEZONES:
            raise HTTPException(
                status_code=422,
                detail=f"Fuseau horaire inconnu : « {body.timezone} ». Exemple : Europe/Paris.",
            )
        entry = BirthdayEntry(
            user=int(session.user_id), date=day, timezone=body.timezone, hideyear=body.hideYear
        )
        await BirthdayRepository(server_id).set_own(entry)
        return JSONResponse({"status": "ok"})

    @router.delete("/api/me/servers/{server_id}/birthday")
    async def api_delete_birthday(request: Request, server_id: str):
        session = await ctx.require_guild_member(request, server_id)
        require_section(server_id, "birthday")
        await BirthdayRepository(server_id).delete(int(session.user_id))
        return JSONResponse({"status": "ok"})

    # --- Tricount --------------------------------------------------------

    @router.post("/api/me/servers/{server_id}/tricount/{group_id}/settle")
    async def api_settle(request: Request, server_id: str, group_id: str, body: SettleBody):
        """Record a repayment on a line of the group's plan, in full or in part (P16).

        The member may record what they paid back (``paid``), or what was paid
        back to them (``received``), never more than the line: either way it
        only lowers a debt the plan shows, so no one's balance turns against them.
        """
        session = await ctx.require_guild_member(request, server_id)
        require_section(server_id, "tricount")
        uid, other = int(session.user_id), int(body.to)
        debtor, creditor = (uid, other) if body.direction == "paid" else (other, uid)
        repo = TricountRepository(server_id)
        group = await repo.find_active_group_by_str(group_id)
        if group is None:
            raise HTTPException(status_code=404, detail="Groupe introuvable.")
        expenses = await repo.list_group_expenses(group["_id"])
        plan = settlement_plan(compute_balances(expenses, group["members"]))
        owed = next((n for d, c, n in plan if d == debtor and c == creditor), 0)
        if not owed or body.cents > owed:
            raise HTTPException(
                status_code=409,
                detail="Le bilan du groupe a changé depuis : recharge la page pour voir ce qui reste.",
            )
        await repo.add_expense(
            {
                "group_id": group["_id"],
                "group_name": group.get("name", ""),
                "kind": TRANSFER,
                "amount": round(body.cents / 100, 2),
                "description": "Remboursement",
                "payer": debtor,
                "recipient": creditor,
                "participants": [creditor],
                "added_by": uid,
                "date": clock.now(),
            }
        )
        logger.info(
            "Tricount reimbursement (%s) recorded from the member space by %s in group %s of %s",
            body.direction,
            session.user_id,
            group_id,
            server_id,
        )
        return JSONResponse({"status": "ok"})

    # --- Secret Santa ----------------------------------------------------

    @router.put("/api/me/servers/{server_id}/santa/wishlist")
    async def api_wishlist(request: Request, server_id: str, body: WishlistBody):
        session = await ctx.require_guild_member(request, server_id)
        require_section(server_id, "santa")
        repo = SecretSantaRepository()
        context_id = f"guild_{server_id}"
        santa = await repo.get_session(context_id)
        uid = int(session.user_id)
        if santa is None or uid not in santa.participants:
            raise HTTPException(
                status_code=404,
                detail="Tu ne participes pas au Père Noël Secret de ce serveur.",
            )
        if santa.is_drawn:
            raise HTTPException(
                status_code=409,
                detail="Le tirage est fait : ton Père Noël a déjà reçu ta liste.",
            )
        text = body.text.strip() or None
        await repo.set_wishlist(
            context_id, uid, text, participant_expiry(santa.exchange_day, santa.created)
        )
        return JSONResponse({"status": "ok"})

    @router.post("/api/me/servers/{server_id}/santa/join")
    async def api_santa_join(request: Request, server_id: str):
        """Join the server's Secret Santa, as its "Participer" button does (P16)."""
        session = await ctx.require_guild_member(request, server_id)
        require_section(server_id, "santa")
        repo = SecretSantaRepository()
        context_id = f"guild_{server_id}"
        updated = await repo.add_participant(context_id, int(session.user_id))
        if updated is None:
            current = await repo.get_session(context_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Pas de Père Noël Secret en cours ici.")
            if current.is_drawn:
                raise HTTPException(
                    status_code=409, detail="Le tirage est fait : les inscriptions sont closes."
                )
            return JSONResponse({"status": "ok"})  # already in
        await refresh_santa(server_id, updated)
        return JSONResponse({"status": "ok"})

    @router.post("/api/me/servers/{server_id}/santa/leave")
    async def api_santa_leave(request: Request, server_id: str):
        """Leave it, as "Se retirer" does: the wish list goes with it (P16)."""
        session = await ctx.require_guild_member(request, server_id)
        require_section(server_id, "santa")
        repo = SecretSantaRepository()
        context_id = f"guild_{server_id}"
        uid = int(session.user_id)
        updated = await repo.remove_participant(context_id, uid)
        if updated is None:
            current = await repo.get_session(context_id)
            if current is not None and current.is_drawn:
                raise HTTPException(
                    status_code=409, detail="Le tirage est fait : tu ne peux plus te retirer."
                )
            return JSONResponse({"status": "ok"})  # not in (or no session): nothing to do
        await repo.clear_wishlist(context_id, uid)
        await refresh_santa(server_id, updated)
        return JSONResponse({"status": "ok"})

    async def refresh_santa(server_id: str, santa: Any) -> None:
        """Show the new participants on the Discord announcement (best effort)."""
        if not ctx.bot:
            return
        try:
            await ctx.call_bot(
                refresh_announcement,
                ctx.bot,
                santa,
                what=f"Secret Santa announcement of {server_id}",
            )
        except Exception as e:  # noqa: BLE001 — joined all the same: the next change shows it
            logger.warning("Secret Santa announcement of %s not refreshed: %s", server_id, e)

    # --- My data (/mes-donnees) ------------------------------------------

    @router.post("/api/me/data/export")
    async def api_export(request: Request):
        """Everything the bot keeps about the user, on every server: ``/mes-donnees exporter``."""
        session = ctx.require_session(request)
        guild_ids = await mongo_manager.guild_ids_with_data()
        data = await UserDataRepository(session.user_id).export(guild_ids)
        payload = export_json(data)
        logger.info(
            "Data export from the dashboard for %s (%d bytes)", session.user_id, len(payload)
        )
        return Response(
            payload,
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="mes-donnees.json"'},
        )

    @router.post("/api/me/data/delete")
    async def api_forget(request: Request, body: DeleteBody):  # noqa: ARG001 — the confirmation
        """``/mes-donnees supprimer``: forget the user on every server, and say what was kept."""
        session = ctx.require_session(request)
        guild_ids = await mongo_manager.guild_ids_with_data()
        report = await UserDataRepository(session.user_id).forget(guild_ids)
        logger.info(
            "Data of %s deleted from the dashboard: %d deleted, %d anonymised, kept %s",
            session.user_id,
            sum(report.deleted.values()),
            sum(report.anonymised.values()),
            report.kept,
        )
        return JSONResponse(
            {
                "deleted": sum(report.deleted.values()),
                "anonymised": sum(report.anonymised.values()),
                "kept": kept_reasons(report),
                "notCovered": NOT_COVERED,
            }
        )

    return router
