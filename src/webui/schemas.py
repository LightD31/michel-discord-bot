"""
Module configuration schemas.

Defines the expected configuration fields for each bot module,
both for per-server config and global config sections.
This is used by the Web UI to render proper forms instead of raw JSON editors.
"""

# Field types used in schemas
# "string"   — text input
# "number"   — numeric input
# "boolean"  — toggle switch
# "channel"  — Discord channel ID (rendered as text input with hint)
# "role"     — Discord role ID
# "message"  — Discord message ID
# "secret"   — sensitive string (masked in UI)
# "list"     — list of strings (comma-separated or multi-line)
# "list:number" — list of numbers
# "dict"     — nested object (raw JSON editor)
# "url"      — URL string


def _field(label: str, field_type: str = "string", required: bool = False,
           description: str = "", default=None, secret: bool = False):
    """Helper to create a field definition."""
    f = {
        "label": label,
        "type": field_type,
        "required": required,
    }
    if description:
        f["description"] = description
    if default is not None:
        f["default"] = default
    if secret:
        f["secret"] = True
    return f


# ── Per-server module schemas ────────────────────────────────────────
# Keys map to the module name used in load_config("moduleXxx")

MODULE_SCHEMAS: dict[str, dict] = {
    "moduleBirthday": {
        "label": "Anniversaires",
        "description": "Envoie des messages d'anniversaire automatiques.",
        "icon": "🎂",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "birthdayChannelId": _field(
                "Salon des anniversaires", "channel", required=True,
                description="Le salon où les messages d'anniversaire seront envoyés."
            ),
            "birthdayRoleId": _field(
                "Rôle anniversaire", "role",
                description="Rôle attribué le jour de l'anniversaire."
            ),
            "birthdayGuildLocale": _field(
                "Locale", "string", default="en_US",
                description="Locale pour le format de date (ex: fr_FR, en_US)."
            ),
            "birthdayMessageList": _field(
                "Messages d'anniversaire", "list",
                description="Liste de messages. Variables: {mention}, {age}.",
                default=["Joyeux anniversaire {mention} ! 🎉"]
            ),
            "birthdayMessageWeights": _field(
                "Poids des messages", "list:number",
                description="Poids de probabilité pour chaque message (même ordre)."
            ),
        },
    },

    "moduleColoc": {
        "label": "Colocation",
        "description": "Gestion de la colocation et notifications Zunivers.",
        "icon": "🏠",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "colocZuniversChannelId": _field(
                "Salon Zunivers", "channel",
                description="Salon pour les notifications Zunivers."
            ),
        },
    },

    "moduleIA": {
        "label": "Intelligence Artificielle",
        "description": "Comparaison de modèles IA via OpenRouter.",
        "icon": "🤖",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleConfrerie": {
        "label": "Confrérie",
        "description": "Intégration Notion pour la Confrérie des Traducteurs.",
        "icon": "📚",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "confrerieNotionDbOeuvresId": _field(
                "Notion DB Œuvres", "string", required=True,
                description="ID de la base de données Notion pour les œuvres."
            ),
            "confrerieNotionDbIdEditorsId": _field(
                "Notion DB Éditeurs", "string",
                description="ID de la base de données Notion pour les éditeurs."
            ),
            "confrerieRecapChannelId": _field(
                "Salon récap", "channel",
                description="Salon pour le message de récapitulatif."
            ),
            "confrerieRecapMessageId": _field(
                "Message récap", "message",
                description="ID du message de récap (mis à jour automatiquement)."
            ),
            "confrerieDefiChannelId": _field(
                "Salon défis", "channel",
                description="Salon pour les défis de traduction."
            ),
            "confrerieNewTextChannelId": _field(
                "Salon nouveaux textes", "channel",
                description="Salon pour les notifications de nouveaux textes."
            ),
            "confrerieOwnerId": _field(
                "ID propriétaire", "string",
                description="ID Discord du propriétaire de la confrérie."
            ),
        },
    },

    "moduleFeur": {
        "label": "Feur",
        "description": "Répond automatiquement « feur » aux messages se terminant par « quoi ».",
        "icon": "😏",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleLiquipedia": {
        "label": "Liquipedia",
        "description": "Planning des matchs esport depuis Liquipedia.",
        "icon": "🎮",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "liquipediaChannelId": _field(
                "Salon planning Valorant", "channel", required=True,
                description="Salon où le planning Valorant est publié."
            ),
            "liquipediaMessageId": _field(
                "Message planning Valorant", "message",
                description="ID du message de planning (rempli automatiquement)."
            ),
            "liquipediaWowChannelId": _field(
                "Salon planning WoW MDI", "channel",
                description="Salon pour le planning WoW MDI."
            ),
            "liquipediaWowMessageId": _field(
                "Message planning WoW", "message",
                description="ID du message WoW (rempli automatiquement)."
            ),
        },
    },

    "moduleOlympics": {
        "label": "Jeux Olympiques",
        "description": "Alertes médailles des Jeux Olympiques.",
        "icon": "🏅",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "olympicsChannelId": _field(
                "Salon alertes", "channel", required=True,
                description="Salon pour les alertes de médailles."
            ),
        },
    },

    "moduleSatisfactory": {
        "label": "Satisfactory",
        "description": "Statut et gestion du serveur Satisfactory.",
        "icon": "🏭",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "satisfactoryChannelId": _field(
                "Salon statut", "channel", required=True,
                description="Salon pour le message de statut du serveur."
            ),
            "satisfactoryMessageId": _field(
                "Message statut", "message",
                description="ID du message de statut (rempli automatiquement)."
            ),
            "satisfactoryServerIp": _field(
                "IP du serveur", "string", required=True,
                description="Adresse IP du serveur Satisfactory."
            ),
            "satisfactoryServerPort": _field(
                "Port du serveur", "string", default="7777",
                description="Port du serveur Satisfactory."
            ),
            "satisfactoryServerPassword": _field(
                "Mot de passe serveur", "secret",
                description="Mot de passe du serveur Satisfactory.", secret=True
            ),
            "satisfactoryServerToken": _field(
                "Token API serveur", "secret",
                description="Token d'authentification API du serveur.", secret=True
            ),
        },
    },

    "moduleSecretSanta": {
        "label": "Secret Santa",
        "description": "Organisation du Secret Santa.",
        "icon": "🎅",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleSpotify": {
        "label": "Spotify",
        "description": "Suivi des écoutes Spotify et playlists collaboratives.",
        "icon": "🎵",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "voteEnabled": _field(
                "Votes activés", "boolean", default=False,
                description="Activer les votes sur les morceaux ajoutés."
            ),
            "spotifyChannelId": _field(
                "Salon notifications", "channel", required=True,
                description="Salon pour les notifications d'écoute."
            ),
            "spotifyPlaylistId": _field(
                "Playlist principale", "string",
                description="ID de la playlist Spotify principale."
            ),
            "spotifyNewPlaylistId": _field(
                "Playlist découvertes", "string",
                description="ID de la playlist de découvertes."
            ),
            "spotifyRecapMessage": _field(
                "URL message récap", "url",
                description="URL du message de récap (webhook PATCH)."
            ),
            "spotifyRecapMessageId": _field(
                "ID message récap", "message",
                description="ID du message de récap dans le salon."
            ),
            "spotifyIdToName": _field(
                "Mapping Spotify → Nom", "dict",
                description="Objet JSON : clé = Spotify user ID, valeur = prénom."
            ),
            "spotifyIdToDiscordId": _field(
                "Mapping Spotify → Discord", "dict",
                description="Objet JSON : clé = Spotify user ID, valeur = Discord user ID."
            ),
        },
    },

    "moduleTricount": {
        "label": "Tricount",
        "description": "Gestion des dépenses partagées.",
        "icon": "💰",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleTwitch": {
        "label": "Twitch",
        "description": "Notifications de live et planning des streamers.",
        "icon": "📺",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "twitchStreamerList": _field(
                "Liste des streamers", "dict", required=True,
                description="Objet JSON. Chaque clé est un nom de streamer, avec les sous-clés : twitchPlanningChannelId, twitchPlanningMessageId, twitchNotificationChannelId."
            ),
        },
    },

    "moduleUptime": {
        "label": "Uptime Kuma",
        "description": "Intégration Uptime Kuma pour le monitoring.",
        "icon": "📡",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleUtils": {
        "label": "Utilitaires",
        "description": "Commandes utilitaires : ping, sondages, rappels, suppression de messages.",
        "icon": "🛠️",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleWelcome": {
        "label": "Bienvenue",
        "description": "Messages de bienvenue et de départ.",
        "icon": "👋",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "welcomeChannelId": _field(
                "Salon de bienvenue", "channel", required=True,
                description="Salon où les messages de bienvenue sont envoyés."
            ),
            "welcomeMessageList": _field(
                "Messages de bienvenue", "list",
                description="Liste de messages. Variable : {mention}.",
                default=["Bienvenue {mention} !"]
            ),
            "welcomeMessageWeights": _field(
                "Poids messages bienvenue", "list:number",
                description="Poids de probabilité pour chaque message de bienvenue."
            ),
            "leaveMessageList": _field(
                "Messages de départ", "list",
                description="Liste de messages de départ. Variable : {username}.",
                default=["{username} nous a quittés."]
            ),
            "leaveMessageWeights": _field(
                "Poids messages départ", "list:number",
                description="Poids de probabilité pour chaque message de départ."
            ),
        },
    },

    "moduleXp": {
        "label": "Système d'XP",
        "description": "Système de niveaux et d'expérience.",
        "icon": "⭐",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "xpChannelId": _field(
                "Salon leaderboard", "channel",
                description="Salon pour le leaderboard permanent."
            ),
            "xpMessageId": _field(
                "Message leaderboard", "message",
                description="ID du message de leaderboard (rempli automatiquement)."
            ),
            "levelUpMessageList": _field(
                "Messages de level-up", "list",
                description="Messages envoyés au level-up. Variables : {mention}, {lvl}.",
                default=["Bravo {mention}, tu as atteint le niveau {lvl} !"]
            ),
            "levelUpMessageWeights": _field(
                "Poids messages level-up", "list:number",
                description="Poids de probabilité pour chaque message."
            ),
        },
    },

    "moduleYoutube": {
        "label": "YouTube",
        "description": "Notifications de nouvelles vidéos YouTube.",
        "icon": "▶️",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "ChannelId": _field(
                "Salon notifications", "channel", required=True,
                description="Salon pour les notifications de nouvelles vidéos."
            ),
            "youtubeChannelList": _field(
                "Chaînes YouTube", "list", required=True,
                description="Liste des noms de chaînes YouTube à surveiller."
            ),
        },
    },

    "moduleMinecraft": {
        "label": "Minecraft",
        "description": "Statut et gestion du serveur Minecraft via RCON.",
        "icon": "⛏️",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "minecraftChannelId": _field(
                "Salon statut", "channel", required=True,
                description="Salon pour le message de statut du serveur."
            ),
            "minecraftMessageId": _field(
                "Message statut", "message",
                description="ID du message de statut (rempli automatiquement)."
            ),
            "minecraftUrl": _field(
                "URL publique", "string",
                description="Nom de domaine public du serveur Minecraft."
            ),
            "minecraftIp": _field(
                "IP du serveur", "string", required=True,
                description="Adresse IP du serveur Minecraft."
            ),
            "minecraftPort": _field(
                "Port du serveur", "string", default="25565",
                description="Port du serveur Minecraft."
            ),
            "minecraftRconHost": _field(
                "Hôte RCON", "string",
                description="Adresse IP pour la connexion RCON."
            ),
            "minecraftRconPort": _field(
                "Port RCON", "number", default=25575,
                description="Port RCON du serveur."
            ),
            "minecraftRconPassword": _field(
                "Mot de passe RCON", "secret",
                description="Mot de passe RCON du serveur.", secret=True
            ),
            "minecraftSftpsPassword": _field(
                "Mot de passe SFTP", "secret",
                description="Mot de passe SFTP pour l'accès aux fichiers.", secret=True
            ),
        },
    },

    "moduleGuildeux": {
        "label": "Guildeux",
        "description": "Gestion des liens de guilde.",
        "icon": "⚔️",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "lienChannelId": _field(
                "Salon des liens", "channel", required=True,
                description="Salon pour le message des liens de guilde."
            ),
            "lienMessageId": _field(
                "Message des liens", "message",
                description="ID du message contenant les liens."
            ),
        },
    },
}


# ── Global config sections schemas ───────────────────────────────────
# These describe the top-level config sections (not per-server).

GLOBAL_CONFIG_SCHEMAS: dict[str, dict] = {
    "discord": {
        "label": "Discord",
        "icon": "💬",
        "fields": {
            "botId": _field("Bot ID", "string",
                            description="ID de l'application / bot Discord."),
            "botToken": _field("Token du bot", "secret", required=True,
                               description="Token du bot Discord.", secret=True),
            "ownerId": _field("ID propriétaire", "string",
                              description="ID Discord du propriétaire du bot."),
            "devGuildId": _field("Serveur de développement", "string",
                                 description="ID du serveur de développement."),
            "devGuildChannelId": _field("Salon de dev", "channel",
                                        description="ID du salon de développement."),
        },
    },

    "mongodb": {
        "label": "MongoDB",
        "icon": "🗃️",
        "fields": {
            "url": _field("URL de connexion", "secret", required=True,
                          description="URL de connexion MongoDB (mongodb://...).", secret=True),
        },
    },

    "spotify": {
        "label": "Spotify",
        "icon": "🎵",
        "fields": {
            "spotifyClientId": _field("Client ID", "string", required=True),
            "spotifyClientSecret": _field("Client Secret", "secret", required=True, secret=True),
            "spotifyRedirectUri": _field("Redirect URI", "url",
                                         description="URI de redirection OAuth Spotify."),
        },
    },

    "twitch": {
        "label": "Twitch",
        "icon": "📺",
        "fields": {
            "twitchClientId": _field("Client ID", "string", required=True),
            "twitchClientSecret": _field("Client Secret", "secret", required=True, secret=True),
        },
    },

    "youtube": {
        "label": "YouTube",
        "icon": "▶️",
        "fields": {
            "youtubeApiKey": _field("Clé API", "secret", required=True,
                                    description="Clé API YouTube Data v3.", secret=True),
        },
    },

    "notion": {
        "label": "Notion",
        "icon": "📝",
        "fields": {
            "notionSecret": _field("Token secret", "secret", required=True,
                                    description="Token d'intégration Notion.", secret=True),
        },
    },

    "liquipedia": {
        "label": "Liquipedia",
        "icon": "🎮",
        "fields": {
            "liquipediaApiKey": _field("Clé API", "secret", required=True,
                                       description="Clé API Liquipedia.", secret=True),
        },
    },

    "OpenRouter": {
        "label": "OpenRouter",
        "icon": "🤖",
        "fields": {
            "openrouterApiKey": _field("Clé API", "secret", required=True,
                                       description="Clé API OpenRouter.", secret=True),
        },
    },

    "uptimeKuma": {
        "label": "Uptime Kuma",
        "icon": "📡",
        "fields": {
            "uptimeKumaUrl": _field("URL", "url", required=True,
                                     description="URL de l'instance Uptime Kuma."),
            "uptimeKumaUsername": _field("Nom d'utilisateur", "string", required=True),
            "uptimeKumaPassword": _field("Mot de passe", "secret", required=True, secret=True),
            "uptimeKuma2FA": _field("Code 2FA", "string",
                                    description="Code 2FA si activé (optionnel)."),
            "uptimeKumaToken": _field("Token push", "secret",
                                      description="Token push pour le statut du bot.", secret=True),
            "uptimeKumaApiKey": _field("Clé API", "secret",
                                       description="Clé API Uptime Kuma.", secret=True),
        },
    },

    "misc": {
        "label": "Divers",
        "icon": "⚙️",
        "fields": {
            "dataFolder": _field("Dossier de données", "string", default="data",
                                  description="Chemin du dossier de données local."),
        },
    },

    "discord2name": {
        "label": "Discord → Prénoms",
        "icon": "👤",
        "fields": {
            "__info": _field(
                "Info", "string",
                description="Mapping par serveur : ID Discord → prénom. Modifier en JSON."
            ),
        },
        "rawJson": True,
    },

    "shlink": {
        "label": "Shlink",
        "icon": "🔗",
        "fields": {
            "shlinkApiKey": _field("Clé API", "secret", required=True,
                                   description="Clé API Shlink pour raccourcir les URLs.", secret=True),
        },
    },

    "random": {
        "label": "Random.org",
        "icon": "🎲",
        "fields": {
            "randomOrgApiKey": _field("Clé API", "secret", required=True,
                                      description="Clé API Random.org.", secret=True),
        },
    },

    "SecretSanta": {
        "label": "Secret Santa (global)",
        "icon": "🎅",
        "fields": {
            "secretSantaFile": _field("Fichier de données", "string",
                                      description="Chemin du fichier JSON des données Secret Santa.",
                                      default="data/secretsanta.json"),
            "secretSantaKey": _field("Clé de chiffrement", "secret",
                                     description="Clé utilisée pour le chiffrement des assignations.",
                                     secret=True),
        },
    },

    "webui": {
        "label": "Dashboard Web",
        "icon": "🌐",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "host": _field("Hôte", "string", default="0.0.0.0",
                           description="Adresse de liaison du serveur web."),
            "port": _field("Port", "number", default=8080),
            "baseUrl": _field("URL de base", "url", required=True,
                              description="URL publique du dashboard (ex: http://monserveur:8080)."),
            "clientId": _field("Client ID Discord", "string", required=True),
            "clientSecret": _field("Client Secret Discord", "secret", required=True, secret=True),
            "adminUserIds": _field("Admin User IDs", "list", required=True,
                                    description="Liste des IDs Discord autorisés à accéder au dashboard."),
        },
    },
}
