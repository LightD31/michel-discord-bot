"""The server's incidents: its last failed interactions and tasks, for its admins (P21).

A member who hits an error is told "donne cette référence à un admin :
``ab12cd``", and only developers could read the logs. :class:`IncidentHandler`
keeps, per guild, the last :data:`PER_GUILD` ERROR records that carry a
``guild_id`` in their log context (:mod:`src.core.log_context`: every command,
button and modal binds it; a per-guild loop binds it around each guild): the
reference, what failed (a command, a button, a task), when, and a short French
reason worked out from the exception's type. Never the exception's text, the
traceback, a member id or a message: admins read these, not developers.

In memory only, bounded (:data:`MAX_GUILDS` guilds), gone at restart.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass

from src.core import logging as logutil

PER_GUILD = 50
MAX_GUILDS = 1000

# The first class name of an exception's MRO that says what happened → why, in French.
REASONS: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"DatabaseError"}), "La base de données ne répondait pas."),
    (frozenset({"Forbidden"}), "Discord a refusé : il manque une permission à Michel."),
    (
        frozenset({"NotFound"}),
        "Introuvable sur Discord : un salon, un message ou un rôle supprimé.",
    ),
    (frozenset({"TimeoutError"}), "Un service a mis trop de temps à répondre."),
    (frozenset({"HTTPException", "DiscordError"}), "Discord a renvoyé une erreur."),
    (frozenset({"IntegrationError", "HttpError"}), "Un service externe a échoué."),
    (frozenset({"ConfigError", "ValidationError"}), "Un réglage du module est invalide."),
)
UNEXPECTED = "Erreur inattendue dans le code de Michel : les développeurs ont les détails."
UNKNOWN = "Échec signalé, sans plus de détail."


@dataclass(frozen=True)
class Incident:
    at: float  # epoch seconds
    reference: str
    where: str  # "/agenda", "component:giveaway:…", "modal:…", or a task's name
    reason: str
    detailed: bool  # the reason came from an exception (a later record may be better)

    def to_dict(self) -> dict[str, object]:
        out = asdict(self)
        out.pop("detailed")
        return out


def reason_of(error: BaseException | None) -> str:
    if error is None:
        return UNKNOWN
    names = {cls.__name__ for cls in type(error).__mro__}
    for classes, reason in REASONS:
        if names & classes:
            return reason
    return UNEXPECTED


class IncidentHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self._guilds: OrderedDict[str, deque[Incident]] = OrderedDict()
        self._lock_guilds = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        context = getattr(record, "log_context", None)
        if not isinstance(context, dict) or not context.get("guild_id"):
            return
        error = getattr(record, "failure", None)
        if error is None and record.exc_info:
            error = record.exc_info[1]
        incident = Incident(
            at=record.created,
            reference=str(context.get("correlation_id") or "—"),
            where=str(context.get("command") or context.get("task") or record.name),
            reason=reason_of(error),
            detailed=error is not None,
        )
        self.add(str(context["guild_id"]), incident)

    def add(self, guild_id: str, incident: Incident) -> None:
        with self._lock_guilds:
            found = self._guilds.get(guild_id)
            if found is None:
                found = self._guilds[guild_id] = deque(maxlen=PER_GUILD)
                while len(self._guilds) > MAX_GUILDS:
                    self._guilds.popitem(last=False)
            self._guilds.move_to_end(guild_id)
            for index, known in enumerate(found):
                if known.reference == incident.reference and known.where == incident.where:
                    # One failure, several lines: keep one, the one that knows why.
                    if incident.detailed and not known.detailed:
                        found[index] = incident
                    return
            found.append(incident)

    def of(self, guild_id: str) -> list[Incident]:
        """The guild's incidents, newest first."""
        with self._lock_guilds:
            return list(reversed(self._guilds.get(str(guild_id), ())))


_handler: IncidentHandler | None = None


def install() -> IncidentHandler:
    """Attach the handler to every logger (once)."""
    global _handler
    if _handler is None:
        _handler = IncidentHandler()
        logutil.add_shared_handler(_handler)
    return _handler


def incidents_of(guild_id: str) -> list[Incident]:
    return _handler.of(guild_id) if _handler is not None else []


__all__ = [
    "MAX_GUILDS",
    "PER_GUILD",
    "Incident",
    "IncidentHandler",
    "incidents_of",
    "install",
    "reason_of",
]
