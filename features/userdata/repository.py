"""Everything the bot stores about one member: exported, or forgotten (F10).

``/mes-donnees`` lets a member see and delete what the bot keeps about them,
on every server at once, instead of e-mailing the Operator (``Privacy.md`` §9);
the same deletion runs for one server 30 days after they leave it
(:mod:`features.userdata.departures`). What happens to each store:

* **deleted** — what is the member's alone: XP and its log, birthday,
  reminders, ``!request`` reminders, "feur" counter and its dated events, directory entry, AI quotas,
  Secret Santa wish lists and gift marks, their entry in a giveaway, who
  greeted them on arrival and their own greetings of others;
* **anonymised** — what other members share: their votes stay counted under a
  key that names nobody, and where they are the author, creator, asker or
  winner, the id becomes :data:`NOBODY` (polls, suggestions, Spotify, AI
  comparisons, giveaways, starboard, role menus, book club drafts, XP season
  podiums);
* **kept** — moderation records (keeping servers safe: infractions, and the
  reports aimed at the member's messages; a member who *reported* a message
  is anonymised there, their reason kept), Secret Santa draws
  already made (other people's gifts), and Tricount while the member owes or is
  owed money in a group (the others' balances depend on it). Once settled,
  Tricount is anonymised too.

Ids are stored as strings in some collections and as integers in others; each
store below names its own. A member id is only interpolated into a field path
(``votes.<id>``) once :func:`user_id_of` has checked it is all digits.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from features.tricount.balances import compute_balances
from src.core.db import GLOBAL_DB_NAME, mongo_manager, translates_db_errors

# Who an anonymised author, asker or winner becomes: no Discord id starts with 0.
NOBODY = "0"
NOBODY_INT = 0


def user_id_of(raw: object) -> str:
    """*raw* as a Discord id string; ``ValueError`` unless it is all digits."""
    text = str(raw).strip()
    if not text.isdigit() or text.startswith("0"):
        raise ValueError(f"not a Discord id: {raw!r}")
    return text


def export_json(data: dict[str, Any]) -> bytes:
    """*data* (from :meth:`UserDataRepository.export`) as readable JSON, BSON types included."""
    from bson import json_util

    options = json_util.JSONOptions(json_mode=json_util.JSONMode.RELAXED)
    return json_util.dumps(data, json_options=options, indent=2, ensure_ascii=False).encode()


def anonymous_key() -> str:
    """A vote key that names nobody: digits, like an id, but starting with 0."""
    return "0" + "".join(secrets.choice("0123456789") for _ in range(17))


@dataclass
class Report:
    """What a deletion did, per store (``"<database>.<collection>"``)."""

    deleted: dict[str, int] = field(default_factory=dict)
    anonymised: dict[str, int] = field(default_factory=dict)
    kept: list[str] = field(default_factory=list)

    def add(self, bucket: dict[str, int], where: str, count: int) -> None:
        if count:
            bucket[where] = bucket.get(where, 0) + count

    @property
    def total(self) -> int:
        return sum(self.deleted.values()) + sum(self.anonymised.values())


# Personal stores of a guild database: (collection, filter for the member).
def _personal_filters(uid: str) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("xp", {"_id": uid}),
        ("xp_events", {"user_id": uid}),
        ("xp_monthly", {"_id.user_id": uid}),
        ("birthday", {"user": int(uid)}),
        ("task_reminders", {"user_id": uid}),
        ("zerator247_reminders", {"_id": uid}),
        ("feur_stats", {"_id": f"user_{uid}"}),
        ("feur_events", {"user_id": uid}),
        ("users", {"_id": uid}),
        # Secret Santa wish lists and gift marks: the member's own entries.
        ("secret_santa_participants", {"user_id": int(uid)}),
        # Who wished them welcome when they arrived.
        ("welcome_greetings", {"member_id": uid}),
    ]


# Shared stores whose author-like field becomes NOBODY: (collection, field, as int?).
_AUTHOR_FIELDS: list[tuple[str, str, bool]] = [
    ("polls", "author_id", False),
    ("suggestions", "author_id", False),
    ("playlistItemsFull", "added_by", False),
    ("votes", "added_by", False),
    ("ai_comparisons", "asker_id", False),
    ("starboard", "author_id", False),
    ("role_menus", "created_by", False),
    ("confrerie_drafts", "submitter_id", True),
]
# Shared stores with a ``votes`` map keyed by member id.
_VOTE_MAPS = ["polls", "suggestions", "votes", "addwithvotes", "ai_comparisons"]

# Hooks for extensions holding a member's data in memory (ZUnivers reminders),
# which a database write alone would see overwritten at their next save.
ForgetHook = Callable[[str, str], Awaitable[int]]  # (guild_id, user_id) -> entries removed
_forget_hooks: dict[str, ForgetHook] = {}


def register_forget_hook(name: str, hook: ForgetHook) -> None:
    """Have :meth:`UserDataRepository.forget` call *hook* for each guild (by *name*, once)."""
    _forget_hooks[name] = hook


def unregister_forget_hook(name: str) -> None:
    _forget_hooks.pop(name, None)


def _in_pair(field_name: str, uid: int) -> dict[str, Any]:
    """Documents whose *field_name*, a list of ``[a, b]`` pairs, has one holding *uid*."""
    return {field_name: {"$elemMatch": {"$elemMatch": {"$eq": uid}}}}


def _db_name(guild_id: str | None) -> str:
    return GLOBAL_DB_NAME if guild_id is None else f"guild_{guild_id}"


class UserDataRepository:
    """Export or forget one member's data in the guild databases given (and the global one)."""

    def __init__(self, user_id: object) -> None:
        self.uid = user_id_of(user_id)

    def _db(self, guild_id: str | None) -> Any:
        return mongo_manager.global_db if guild_id is None else mongo_manager.get_guild_db(guild_id)

    # --- Export ---------------------------------------------------------------

    @translates_db_errors
    async def export(self, guild_ids: list[str]) -> dict[str, Any]:
        """``{"guilds": {id: {collection: [docs]}}, "global": {...}}``, empty stores left out."""
        uid = self.uid
        guilds: dict[str, Any] = {}
        for guild_id in guild_ids:
            db = self._db(guild_id)
            found: dict[str, list[dict]] = {}
            queries: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = [
                (name, query, None) for name, query in _personal_filters(uid)
            ]
            queries += [
                # Being the author of a poll doesn't show the others' votes.
                (name, {fld: int(uid) if as_int else uid}, {"votes": 0})
                for name, fld, as_int in _AUTHOR_FIELDS
            ]
            queries += [
                (name, {f"votes.{uid}": {"$exists": True}}, {f"votes.{uid}": 1})
                for name in _VOTE_MAPS
            ]
            queries += [
                ("giveaways", {"winners": uid}, {"winners": 0}),
                # The giveaways they entered, never who else did.
                ("giveaways", {"entrants": uid}, {"entrants": 0, "winners": 0}),
                ("infractions", {"user_id": uid}, None),
                # Season podiums: the member's own line.
                (
                    "xp_seasons",
                    {"top.user_id": uid},
                    {"label": 1, "closed_at": 1, "top": {"$elemMatch": {"user_id": uid}}},
                ),
                # Reports of the member's messages, never who reported them or why.
                ("reports", {"author_id": uid}, {"reporters": 0}),
                # The member's own reports: their entry alone.
                (
                    "reports",
                    {"reporters.user_id": uid},
                    {
                        "channel_id": 1,
                        "status": 1,
                        "created_at": 1,
                        "reporters": {"$elemMatch": {"user_id": uid}},
                    },
                ),
                ("tricount_groups", {"members": int(uid)}, {"name": 1, "is_active": 1}),
                (
                    "tricount_expenses",
                    {"$or": [{"payer": int(uid)}, {"participants": int(uid)}]},
                    None,
                ),
                ("secret_santa_sessions", {"participants": int(uid)}, {"participants": 0}),
                # The newcomers they greeted: the message, never who else did.
                ("welcome_greetings", {"greeters": uid}, {"expires_at": 1}),
            ]
            for name, query, projection in queries:
                docs = await db[name].find(query, projection).to_list()
                if docs:
                    found.setdefault(name, []).extend(docs)
            draws = await self._own_draws(db)
            if draws:
                found["secret_santa_draw_results"] = draws
            if found:
                guilds[guild_id] = found
        global_db = self._db(None)
        global_found: dict[str, list[dict]] = {}
        usage = await global_db["ai_usage"].find({"_id": {"$regex": f":user:{uid}$"}}).to_list()
        if usage:
            global_found["ai_usage"] = usage
        dm_sessions = (
            await global_db["secret_santa_sessions"]
            .find({"participants": int(uid)}, {"participants": 0})
            .to_list()
        )
        if dm_sessions:
            global_found["secret_santa_sessions"] = dm_sessions
        dm_entries = (
            await global_db["secret_santa_participants"].find({"user_id": int(uid)}).to_list()
        )
        if dm_entries:
            global_found["secret_santa_participants"] = dm_entries
        return {"user_id": uid, "guilds": guilds, "global": global_found}

    async def _own_draws(self, db: Any) -> list[dict]:
        """The member's own pairs of each draw (who they give to, who gives to them)."""
        uid = int(self.uid)
        docs = await db["secret_santa_draw_results"].find(_in_pair("results", uid)).to_list()
        return [
            {**doc, "results": [pair for pair in doc.get("results", []) if uid in pair]}
            for doc in docs
        ]

    # --- Forget ---------------------------------------------------------------

    @translates_db_errors
    async def forget(self, guild_ids: list[str], *, include_global: bool = True) -> Report:
        report = Report()
        for guild_id in guild_ids:
            await self._forget_in(guild_id, report)
            for name, hook in list(_forget_hooks.items()):
                report.add(
                    report.deleted, f"{_db_name(guild_id)}.{name}", await hook(guild_id, self.uid)
                )
        if include_global:
            await self._forget_global(report)
        return report

    async def _forget_in(self, guild_id: str, report: Report) -> None:
        uid, db, where = self.uid, self._db(guild_id), _db_name(guild_id)
        for name, query in _personal_filters(uid):
            result = await db[name].delete_many(query)
            report.add(report.deleted, f"{where}.{name}", result.deleted_count)
        for name, fld, as_int in _AUTHOR_FIELDS:
            mine, nobody = (int(uid), NOBODY_INT) if as_int else (uid, NOBODY)
            result = await db[name].update_many({fld: mine}, {"$set": {fld: nobody}})
            report.add(report.anonymised, f"{where}.{name}", result.modified_count)
        for name in _VOTE_MAPS:
            path = f"votes.{uid}"
            result = await db[name].update_many(
                {path: {"$exists": True}}, {"$rename": {path: f"votes.{anonymous_key()}"}}
            )
            report.add(report.anonymised, f"{where}.{name}", result.modified_count)
        result = await db["giveaways"].update_many(
            {"winners": uid}, {"$set": {"winners.$[me]": NOBODY}}, array_filters=[{"me": uid}]
        )
        report.add(report.anonymised, f"{where}.giveaways", result.modified_count)
        # Entering is theirs alone: they are withdrawn (a drawn giveaway keeps its winners).
        result = await db["giveaways"].update_many({"entrants": uid}, {"$pull": {"entrants": uid}})
        report.add(report.deleted, f"{where}.giveaways.entrants", result.modified_count)
        if await db["infractions"].count_documents({"user_id": uid}, limit=1):
            report.kept.append(f"{where}.infractions")
        result = await db["xp_seasons"].update_many(
            {"top.user_id": uid},
            {"$set": {"top.$[me].user_id": NOBODY}},
            array_filters=[{"me.user_id": uid}],
        )
        report.add(report.anonymised, f"{where}.xp_seasons", result.modified_count)
        result = await db["reports"].update_many(
            {"reporters.user_id": uid},
            {"$set": {"reporters.$[me].user_id": NOBODY}},
            array_filters=[{"me.user_id": uid}],
        )
        report.add(report.anonymised, f"{where}.reports", result.modified_count)
        if await db["reports"].count_documents({"author_id": uid}, limit=1):
            report.kept.append(f"{where}.reports")
        # Greeting someone is theirs alone: they leave the count.
        result = await db["welcome_greetings"].update_many(
            {"greeters": uid}, {"$pull": {"greeters": uid}}
        )
        report.add(report.deleted, f"{where}.welcome_greetings.greeters", result.modified_count)
        await self._forget_secret_santa(db, where, report)
        await self._forget_tricount(db, where, report)

    async def _forget_secret_santa(self, db: Any, where: str, report: Report) -> None:
        uid = int(self.uid)
        result = await db["secret_santa_sessions"].update_many(
            {"participants": uid, "is_drawn": {"$ne": True}}, {"$pull": {"participants": uid}}
        )
        report.add(report.deleted, f"{where}.secret_santa_sessions", result.modified_count)
        drawn = [
            doc["_id"]
            async for doc in db["secret_santa_sessions"].find({"is_drawn": True}, {"_id": 1})
        ]
        result = await db["secret_santa_banned_pairs"].update_many(
            {"_id": {"$nin": drawn}, **_in_pair("pairs", uid)},
            {"$pull": {"pairs": {"$elemMatch": {"$eq": uid}}}},
        )
        report.add(report.deleted, f"{where}.secret_santa_banned_pairs", result.modified_count)
        if await db["secret_santa_draw_results"].count_documents(_in_pair("results", uid), limit=1):
            report.kept.append(f"{where}.secret_santa_draw_results")

    async def _forget_tricount(self, db: Any, where: str, report: Report) -> None:
        """Anonymised once every balance of the member is zero, kept while one isn't."""
        uid = int(self.uid)
        groups = await db["tricount_groups"].find({"members": uid}, {"_id": 1}).to_list()
        group_ids = [g["_id"] for g in groups]
        involved = {"$or": [{"payer": uid}, {"participants": uid}, {"added_by": uid}]}
        expense_groups = await db["tricount_expenses"].distinct("group_id", involved)
        all_groups = list(dict.fromkeys([*group_ids, *expense_groups]))
        for group_id in all_groups:
            expenses = await db["tricount_expenses"].find({"group_id": group_id}).to_list()
            if compute_balances(expenses, []).get(uid, 0) != 0:
                report.kept.append(f"{where}.tricount")
                return
        if not all_groups:
            return
        result = await db["tricount_groups"].update_many(
            {"members": uid}, {"$pull": {"members": uid}}
        )
        count = result.modified_count
        for fld in ("payer", "added_by"):
            result = await db["tricount_expenses"].update_many(
                {fld: uid}, {"$set": {fld: NOBODY_INT}}
            )
            count += result.modified_count
        result = await db["tricount_expenses"].update_many(
            {"participants": uid},
            {"$set": {"participants.$[me]": NOBODY_INT}},
            array_filters=[{"me": uid}],
        )
        report.add(report.anonymised, f"{where}.tricount", count + result.modified_count)

    async def _forget_global(self, report: Report) -> None:
        db = self._db(None)
        result = await db["ai_usage"].delete_many({"_id": {"$regex": f":user:{self.uid}$"}})
        report.add(report.deleted, f"{GLOBAL_DB_NAME}.ai_usage", result.deleted_count)
        result = await db["secret_santa_participants"].delete_many({"user_id": int(self.uid)})
        report.add(
            report.deleted, f"{GLOBAL_DB_NAME}.secret_santa_participants", result.deleted_count
        )
        await self._forget_secret_santa(db, GLOBAL_DB_NAME, report)


__all__ = [
    "NOBODY",
    "Report",
    "UserDataRepository",
    "anonymous_key",
    "export_json",
    "register_forget_hook",
    "unregister_forget_hook",
    "user_id_of",
]
