"""The giveaways of one guild in memory, for the command and route tests."""

from features.giveaway import Giveaway


class MemoryGiveaways:
    """The giveaways of one guild, kept in memory with the repository's semantics."""

    def __init__(self):
        self.items: dict[str, Giveaway] = {}

    async def add(self, giveaway):
        gid = f"{len(self.items) + 1:024x}"
        self.items[gid] = giveaway.model_copy(update={"id": gid})
        return gid

    async def get_by_message(self, message_id):
        return next((g for g in self.items.values() if g.message_id == str(message_id)), None)

    async def mark_cancelled(self, giveaway_id):
        g = self.items[giveaway_id]
        if g.drawn or g.cancelled:
            return False
        g.cancelled = True
        return True

    async def claim_draw(self, giveaway_id):
        g = self.items[giveaway_id]
        if g.drawn or g.cancelled:
            return None
        g.drawn = True
        return g.model_copy(deep=True)

    async def _change(self, message_id, now, change):
        g = await self.get_by_message(message_id)
        if g is None or g.drawn or g.cancelled or g.ends_at <= now:
            return None
        before = g.model_copy(deep=True)
        change(g.entrants)
        return before

    async def add_entrant(self, message_id, user_id, now):
        return await self._change(
            message_id, now, lambda e: e.append(user_id) if user_id not in e else None
        )

    async def remove_entrant(self, message_id, user_id, now):
        return await self._change(
            message_id, now, lambda e: e.remove(user_id) if user_id in e else None
        )

    async def release_draw(self, giveaway_id):
        self.items[giveaway_id].drawn = False

    async def update_winners(self, giveaway_id, winners):
        self.items[giveaway_id].winners = list(winners)

    async def list_active(self):
        return [g for g in self.items.values() if not g.drawn and not g.cancelled]

    async def list_drawn(self, limit):
        drawn = [g for g in self.items.values() if g.drawn]
        return sorted(drawn, key=lambda g: g.ends_at, reverse=True)[:limit]


__all__ = ["MemoryGiveaways"]
