"""Pure leveling helpers — curve math and rank-to-display conversion."""

from features.xp.constants import RANK_MEDALS


def calculate_level(xp: int) -> tuple[int, int, int]:
    """Resolve a total XP amount into ``(level, xp_in_level, xp_to_next)``.

    Each level ``x`` costs ``5x² + 50x + 100`` XP to clear.
    """
    level = 0
    remaining_xp = xp
    while True:
        xp_for_level = (5 * (level**2)) + (50 * level) + 100
        if remaining_xp < xp_for_level:
            break
        remaining_xp -= xp_for_level
        level += 1
    xp_max = (5 * (level**2)) + (50 * level) + 100
    return level, remaining_xp, xp_max


def get_rank_display(rank: int) -> str:
    """Medal emoji for top-3 ranks, ``"<n> -"`` otherwise. ``rank`` is 0-indexed."""
    if rank < len(RANK_MEDALS):
        return RANK_MEDALS[rank]
    return f"{rank + 1} -"


def create_progress_bar(xp_in_level: int, xp_max: int, length: int = 10) -> str:
    """Render a blue-square progress bar of ``length`` cells."""
    filled = int(round((xp_in_level / xp_max) * length))
    return ":blue_square:" * filled + ":white_large_square:" * (length - filled)


__all__ = ["calculate_level", "create_progress_bar", "get_rank_display"]
