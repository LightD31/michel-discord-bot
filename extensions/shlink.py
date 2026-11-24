"""Shlink: the server's own link shortener (P37).

Only settings: the links the server's modules post (RSS entries, videos,
recaps…) are shortened on its instance by ``features.links``, and its admins
manage the short links from the module's page (``src/webui/routes/shlink.py``).
A server without one posts its links as they are.
"""

from interactions import Client, Extension

from src.webui.schemas import ModuleHelp, SchemaBase, register_module, secret_field, ui


@register_module("moduleShlink")
class ShlinkConfig(SchemaBase):
    __label__ = "Liens courts"
    __description__ = "Raccourcit les liens publiés par le bot avec l'instance Shlink du serveur."
    __icon__ = "🔗"
    __category__ = "Outils"
    __help__ = ModuleHelp(
        "Les liens que Michel publie ici passent par le raccourcisseur Shlink du serveur.",
        (),
        "Les liens des flux RSS, vidéos, récapitulatifs… sont raccourcis avant d'être publiés.",
    )

    enabled: bool = ui(
        "Raccourcir automatiquement",
        "boolean",
        default=False,
        description=(
            "Raccourcit les liens externes publiés par le bot sur ce serveur (entrées de "
            "flux RSS, notifications YouTube…). Les liens Discord, les images et les liens "
            "déjà raccourcis ne sont jamais touchés. Les liens courts se gèrent plus bas "
            "même si cette option est désactivée."
        ),
    )
    shlinkBaseUrl: str = ui(
        "URL de l'instance",
        "url",
        required=True,
        description="URL de l'instance Shlink du serveur, par exemple https://exemple.link.",
    )
    shlinkApiKey: str = secret_field(
        "Clé API",
        required=True,
        description="Clé API Shlink pour raccourcir les URLs.",
        sent_to=("shlinkBaseUrl",),
    )
    shlinkDomain: str | None = ui(
        "Domaine court",
        "string",
        description=(
            "Domaine à utiliser pour les liens créés, si l'instance en gère "
            "plusieurs. Vide = domaine par défaut de l'instance."
        ),
    )
    shlinkTags: list[str] = ui(
        "Tags par défaut",
        "list",
        default=["michel-bot"],
        description="Tags appliqués à chaque lien court créé par le bot.",
    )
    shlinkExcludedDomains: list[str] = ui(
        "Domaines exclus",
        "list",
        description=(
            "Domaines dont les liens ne doivent jamais être raccourcis "
            "(sous-domaines inclus), par exemple twitch.tv."
        ),
    )


class ShlinkExtension(Extension):
    """Holds the module's settings; the shortening itself is ``features.links``."""

    MODULE_KEYS = ("moduleShlink",)

    def __init__(self, bot: Client) -> None:
        self.bot = bot


def setup(bot: Client) -> None:
    ShlinkExtension(bot)
