"""Try a message template before saving it: the bot DMs the admin the result (F11).

A mistake in a template (an unknown ``{variable}``, an unintended
``@everyone``) used to show only in production, in front of the members. The
dashboard's "Tester en MP" button sends the form's current texts here; each is
rendered by the function the bot itself uses, with sample values (the admin
stands in for the member), and DMed to the admin, with what the bot would do
differently from what they may expect listed as warnings.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from features.youtube import render_notification
from src.core.text import MESSAGE_LIMIT, clip, fill_message, template_names, template_problem

MAX_TEXTS = 10
SAMPLE_VIDEO_ID = "jNQXAC9IVRw"


@dataclass(frozen=True)
class TemplateTest:
    """How the bot renders one field, and the values it fills it with."""

    label: str
    variables: tuple[str, ...]
    render: Callable[[str, dict[str, Any]], str]
    # What happens to an unknown {name} in this field, said to the admin.
    unknown_note: str = "restera écrit tel quel dans le message"


def _fill(text: str, values: dict[str, Any]) -> str:
    return fill_message(text, **values)


def _youtube(text: str, values: dict[str, Any]) -> str:
    return render_notification(
        text, video_id=values["video_id"], handle=values["handle"], label=values["label"]
    )


def _as_is(text: str, values: dict[str, Any]) -> str:  # noqa: ARG001 — same signature
    return text


TESTS: dict[str, dict[str, TemplateTest]] = {
    "moduleWelcome": {
        "welcomeMessageList": TemplateTest("Message de bienvenue", ("mention",), _fill),
        "leaveMessageList": TemplateTest("Message de départ", ("username",), _fill),
    },
    "moduleXp": {
        "levelUpMessageList": TemplateTest("Passage de niveau", ("mention", "lvl"), _fill),
    },
    "moduleBirthday": {
        "birthdayMessageList": TemplateTest("Anniversaire", ("mention", "age"), _fill),
    },
    "moduleYoutube": {
        "youtubeNotificationTemplate": TemplateTest(
            "Notification YouTube",
            ("video_id", "handle", "label"),
            _youtube,
            unknown_note="fera envoyer le simple lien de la vidéo à la place du message",
        ),
    },
    "moduleCustomCommands": {
        "commands": TemplateTest("Réponse de commande personnalisée", (), _as_is),
    },
}


def catalogue() -> dict[str, dict[str, dict[str, Any]]]:
    """What the SPA may offer to test: module → field → its label and variables."""
    return {
        module: {
            field: {"label": test.label, "variables": list(test.variables)}
            for field, test in fields.items()
        }
        for module, fields in TESTS.items()
    }


def sample_values(user_id: str, username: str) -> dict[str, Any]:
    """The values a test fills: the admin stands in for the member."""
    return {
        "mention": f"<@{user_id}>",
        "username": username,
        "lvl": 5,
        "age": 30,
        "video_id": SAMPLE_VIDEO_ID,
        "handle": "@chaine",
        "label": "Chaîne de test",
    }


@dataclass
class Rendered:
    texts: list[str]
    warnings: list[str]


class TemplateRefused(ValueError):
    """A text the save route would refuse too (a format spec, an attribute…)."""


def render_test(test: TemplateTest, texts: list[str], values: dict[str, Any]) -> Rendered:
    """Render each text as the bot would; raise :class:`TemplateRefused` for a bad one."""
    rendered: list[str] = []
    warnings: list[str] = []
    for index, text in enumerate(texts[:MAX_TEXTS], start=1):
        problem = template_problem(text)
        if problem:
            raise TemplateRefused(f"Message {index} : {problem}")
        unknown = [name for name in template_names(text) if name not in test.variables]
        if unknown:
            names = ", ".join(f"{{{name}}}" for name in unknown)
            warnings.append(f"Message {index} : {names} n'existe pas ici et {test.unknown_note}.")
        if "@everyone" in text or "@here" in text:
            warnings.append(
                f"Message {index} : il contient @everyone ou @here, qui notifiera tout le serveur."
            )
        rendered.append(clip(test.render(text, values), MESSAGE_LIMIT))
    if len(texts) > MAX_TEXTS:
        warnings.append(f"Seuls les {MAX_TEXTS} premiers messages ont été testés.")
    return Rendered(rendered, warnings)


def preview_texts(
    test: TemplateTest, texts: list[str], values: dict[str, Any]
) -> list[dict[str, Any]]:
    """Each text as the bot would send it, or why the save would refuse it (C4).

    The live preview shows a refusal next to its message rather than failing
    the whole request, and an empty message as nothing.
    """
    out: list[dict[str, Any]] = []
    for text in texts[:MAX_TEXTS]:
        if not text.strip():
            out.append({"text": "", "warnings": []})
            continue
        try:
            rendered = render_test(test, [text], values)
        except TemplateRefused as e:
            out.append({"error": str(e).removeprefix("Message 1 : ")})
            continue
        warnings = [w.removeprefix("Message 1 : ") for w in rendered.warnings]
        out.append({"text": rendered.texts[0], "warnings": warnings})
    return out


def dm_text(test: TemplateTest, guild_name: str, rendered: str, index: int, total: int) -> str:
    counter = f" ({index}/{total})" if total > 1 else ""
    header = f"🧪 Test : {test.label}{counter} — serveur « {guild_name} »\n\n"
    return clip(header + rendered, MESSAGE_LIMIT)


__all__ = [
    "MAX_TEXTS",
    "TESTS",
    "Rendered",
    "TemplateRefused",
    "TemplateTest",
    "catalogue",
    "dm_text",
    "preview_texts",
    "render_test",
    "sample_values",
]
