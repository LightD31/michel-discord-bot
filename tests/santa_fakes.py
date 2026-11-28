"""An in-memory Secret Santa repository with the real one's rules.

Conditional updates answer like MongoDB's (``None`` / ``False`` when the filter
doesn't match); the real queries are checked in ``test_mongo_secretsanta.py``.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

from features.secretsanta import SantaParticipant, SecretSantaSession
from src.core import clock


class MemorySanta:
    def __init__(self, session: SecretSantaSession | None = None, banned=()):
        self.session = session
        self.banned = list(banned)
        self.results: list[tuple[int, int]] | None = None
        self.people: dict[int, SantaParticipant] = {}
        self.expiries: dict[int, datetime] = {}
        self.lose_race_to_draw = False
        self.saved: list[SecretSantaSession] = []

    # Sessions
    async def get_session(self, context_id):
        return dataclasses.replace(self.session) if self.session else None

    async def create_session(self, session):
        self.saved.append(session)
        self.session = session
        self.people.clear()

    async def add_participant(self, context_id, user_id):
        if self.lose_race_to_draw:
            self.session.is_drawn = True
        s = self.session
        if s is None or s.is_drawn or user_id in s.participants:
            return None
        s.participants = [*s.participants, user_id]
        return dataclasses.replace(s)

    async def remove_participant(self, context_id, user_id):
        s = self.session
        if s is None or s.is_drawn or user_id not in s.participants:
            return None
        s.participants = [p for p in s.participants if p != user_id]
        return dataclasses.replace(s)

    async def claim_draw(self, context_id):
        if self.session is None or self.session.is_drawn:
            return False
        self.session.is_drawn = True
        return True

    async def delete_session(self, context_id):
        self.session = None
        self.people.clear()
        return True

    async def update_settings(self, context_id, changes, expires_at):
        if self.session is None:
            return None
        for name, value in changes.items():
            setattr(self.session, name, value)
        if "exchange_date" in changes:
            self.session.reminded = False
        for uid in self.people:
            self.expiries[uid] = expires_at
        return dataclasses.replace(self.session)

    async def claim_reminder(self, context_id):
        s = self.session
        if s is None or not s.is_drawn or not s.shipping or s.reminded:
            return False
        s.reminded = True
        return True

    # Exclusions and results
    async def read_banned_pairs(self, context_id):
        return list(self.banned)

    async def exclude_pair(self, context_id, user1, user2):
        if any({a, b} == {user1, user2} for a, b in self.banned):
            return False
        self.banned.append((user1, user2))
        return True

    async def allow_pair(self, context_id, user1, user2):
        kept = [(a, b) for a, b in self.banned if {a, b} != {user1, user2}]
        changed = len(kept) != len(self.banned)
        self.banned = kept
        return changed

    async def save_draw_results(self, context_id, draw_results):
        self.results = list(draw_results)

    async def get_draw_results(self, context_id):
        return list(self.results) if self.results is not None else None

    # Participants' own entries
    def _person(self, context_id, user_id, expires_at):
        self.expiries[user_id] = expires_at
        return self.people.setdefault(user_id, SantaParticipant(context_id, user_id))

    async def set_wishlist(self, context_id, user_id, text, expires_at):
        self._person(context_id, user_id, expires_at).wishlist = text or None

    async def clear_wishlist(self, context_id, user_id):
        if user_id in self.people:
            self.people[user_id].wishlist = None

    async def get_participant(self, context_id, user_id):
        return self.people.get(user_id)

    async def wishlists(self, context_id):
        return {uid: p.wishlist for uid, p in self.people.items() if p.wishlist}

    async def mark_gift(self, context_id, user_id, mark, expires_at):
        person = self._person(context_id, user_id, expires_at)
        if getattr(person, mark) is not None:
            return False
        setattr(person, mark, clock.now())
        return True

    async def gift_marks(self, context_id):
        sent = {uid for uid, p in self.people.items() if p.sent_at}
        received = {uid for uid, p in self.people.items() if p.received_at}
        return sent, received
