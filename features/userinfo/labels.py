"""How a member is named on read-only stats surfaces — no DB, no Discord."""

from collections.abc import Callable, Mapping
from typing import Any


def member_label_resolver(
    prenoms: Any,
    names: Mapping[str, Mapping[str, str]],
    fallback: str,
) -> Callable[[str], str]:
    """``label_of(user_id)``: configured first name, then display name, then username.

    ``prenoms`` is the guild's ``discord2name`` mapping (ignored unless it is a
    dict); ``names`` comes from ``UserInfoRepository.get_names``. A member
    known to neither gets ``fallback``.
    """
    first_names: Mapping[str, Any] = prenoms if isinstance(prenoms, dict) else {}

    def label_of(user_id: str) -> str:
        info = names.get(user_id, {})
        return str(
            first_names.get(user_id) or info.get("display_name") or info.get("username") or fallback
        )

    return label_of
