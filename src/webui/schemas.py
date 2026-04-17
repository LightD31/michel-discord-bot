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
# "messagelist" — list of messages with linked weight field (form rows)
# "embedlist" — list of embeds with title, color, and links (form rows)
# "keyvaluemap" — key-value pairs in a form (customizable key/value labels via key_label, value_label)
# "spotifymap" — list of Spotify user mappings (spotifyId, name, discordId)
# "streamermap" — dict keyed by Twitch login; each value has planning/notification channel and pin fields


def _field(label: str, field_type: str = "string", required: bool = False,
           description: str = "", default=None, secret: bool = False,
           weight_field: str = "", variables: str = "", key_label: str = "",
           value_label: str = "", hidden: bool = False, channel_field: str = ""):
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
    if weight_field:
        f["weightField"] = weight_field
    if variables:
        f["variables"] = variables
    if key_label:
        f["keyLabel"] = key_label
    if value_label:
        f["valueLabel"] = value_label
    if hidden:
        f["hidden"] = True
    if channel_field:
        f["channelField"] = channel_field
    return f


# ── Per-server module schemas ────────────────────────────────────────
# Keys map to the module name used in load_config("moduleXxx")

MODULE_SCHEMAS: dict[str, dict] = {
    "moduleBirthday": {
        "label": "Anniversaires",
        "description": "Envoie des messages d'anniversaire automatiques.",
        "icon": "🎂",
        "category": "Communauté",
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
                "Messages d'anniversaire", "messagelist",
                description="Liste de messages avec poids de probabilité.",
                default=["Joyeux anniversaire {mention} ! 🎉"],
                weight_field="birthdayMessageWeights",
                variables="{mention}, {age}"
            ),
        },
    },

    "moduleColoc": {
        "label": "Colocation",
        "description": "Gestion de la colocation et notifications Zunivers.",
        "icon": "🏠",
        "category": "Communauté",
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
        "category": "Outils",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleConfrerie": {
        "label": "Confrérie",
        "description": "Intégration Notion pour la Confrérie des Traducteurs.",
        "icon": "📚",
        "category": "Outils",
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
                description="Salon pour le message de récapitulatif (créé automatiquement)."
            ),
            "confrerieRecapPinMessage": _field(
                "Épingler le message récap", "boolean", default=False,
                description="Épingler automatiquement le message de récap."
            ),
            "confrerieRecapMessageId": _field(
                "Message récap", "message", hidden=True,
                channel_field="confrerieRecapChannelId",
                description="ID interne (géré automatiquement)."
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
        "category": "Communauté",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleVlrgg": {
        "label": "Esport Tracker (VLR.gg)",
        "description": "Suivi automatique des matchs d'équipes Valorant via VLR.gg.",
        "icon": "🎮",
        "category": "Esport & Jeux",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "notificationChannelId": _field(
                "Salon notifications", "channel",
                description="Salon pour les notifications de matchs en direct et résultats."
            ),
            "teams": _field(
                "Équipes suivies", "teams",
                description="Liste des équipes Valorant à suivre. Chaque équipe nécessite un nom et un ID VLR.gg."
            ),
        },
    },

    "moduleOlympics": {
        "label": "Jeux Olympiques",
        "description": "Alertes médailles des Jeux Olympiques.",
        "icon": "🏅",
        "category": "Événements",
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
        "category": "Esport & Jeux",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "satisfactoryChannelId": _field(
                "Salon statut", "channel", required=True,
                description="Salon pour le message de statut (créé automatiquement)."
            ),
            "satisfactoryPinMessage": _field(
                "Épingler le message de statut", "boolean", default=False,
                description="Épingler automatiquement le message de statut."
            ),
            "satisfactoryMessageId": _field(
                "Message statut", "message", hidden=True,
                channel_field="satisfactoryChannelId",
                description="ID interne (géré automatiquement)."
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
        "category": "Événements",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleSpotify": {
        "label": "Spotify",
        "description": "Suivi des écoutes Spotify et playlists collaboratives.",
        "icon": "🎵",
        "category": "Médias & Streaming",
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
            "spotifyRecapChannelId": _field(
                "Salon message récap", "channel",
                description="Salon où le message de récap est publié (créé automatiquement)."
            ),
            "spotifyRecapPinMessage": _field(
                "Épingler le message récap", "boolean", default=False,
                description="Épingler automatiquement le message de récap de la playlist."
            ),
            "spotifyRecapMessageId": _field(
                "ID message récap", "message", hidden=True,
                channel_field="spotifyRecapChannelId",
                description="ID interne (géré automatiquement)."
            ),
            "spotifyUsers": _field(
                "Mapping Spotify (ID, Nom, Discord)", "spotifymap",
                description="Liste des utilisateurs Spotify avec nom et ID Discord associés."
            ),
        },
    },

    "moduleTricount": {
        "label": "Tricount",
        "description": "Gestion des dépenses partagées.",
        "icon": "💰",
        "category": "Outils",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleTwitch": {
        "label": "Twitch",
        "description": "Notifications de live et planning des streamers.",
        "icon": "📺",
        "category": "Médias & Streaming",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "twitchStreamerList": _field(
                "Streamers suivis", "streamermap", required=True,
                description="Liste des streamers Twitch à suivre. Renseignez le login Twitch, le salon du planning et le salon des notifications. Le message de planning est créé automatiquement."
            ),
            "notifyStreamStart": _field(
                "Début de live", "boolean", default=False,
                description="Envoyer une notification quand un stream démarre."
            ),
            "notifyStreamUpdate": _field(
                "Changements de live", "boolean", default=False,
                description="Envoyer une notification quand le titre ou la catégorie du live change."
            ),
            "notifyStreamEnd": _field(
                "Résumé de fin de live", "boolean", default=False,
                description="Envoyer un résumé (durée, catégories jouées, VOD) à la fin du live."
            ),
            "notifyEmoteChanges": _field(
                "Changements d'emotes", "boolean", default=False,
                description="Notifier les ajouts, suppressions et remplacements d'emotes sur la chaîne."
            ),
            "manageDiscordEvents": _field(
                "Événements Discord", "boolean", default=False,
                description="Créer et mettre à jour automatiquement un événement Discord programmé pendant le live."
            ),
        },
    },

    "moduleUptime": {
        "label": "Uptime Kuma",
        "description": "Intégration Uptime Kuma pour le monitoring.",
        "icon": "📡",
        "category": "Outils",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleUtils": {
        "label": "Utilitaires",
        "description": "Commandes utilitaires : ping, sondages, rappels, suppression de messages.",
        "icon": "🛠️",
        "category": "Outils",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
        },
    },

    "moduleWelcome": {
        "label": "Bienvenue",
        "description": "Messages de bienvenue et de départ.",
        "icon": "👋",
        "category": "Communauté",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "welcomeChannelId": _field(
                "Salon de bienvenue", "channel", required=True,
                description="Salon où les messages de bienvenue sont envoyés."
            ),
            "welcomeMessageList": _field(
                "Messages de bienvenue", "messagelist",
                description="Liste de messages avec poids de probabilité.",
                default=["Bienvenue {mention} !"],
                weight_field="welcomeMessageWeights",
                variables="{mention}"
            ),
            "leaveMessageList": _field(
                "Messages de départ", "messagelist",
                description="Liste de messages de départ avec poids de probabilité.",
                default=["{username} nous a quittés."],
                weight_field="leaveMessageWeights",
                variables="{username}"
            ),
        },
    },

    "moduleXp": {
        "label": "Système d'XP",
        "description": "Système de niveaux et d'expérience.",
        "icon": "⭐",
        "category": "Communauté",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "xpChannelId": _field(
                "Salon leaderboard", "channel",
                description="Salon pour le leaderboard permanent (créé automatiquement)."
            ),
            "xpPinMessage": _field(
                "Épingler le leaderboard", "boolean", default=False,
                description="Épingler automatiquement le message du leaderboard."
            ),
            "xpMessageId": _field(
                "Message leaderboard", "message", hidden=True,
                channel_field="xpChannelId",
                description="ID interne (géré automatiquement)."
            ),
            "levelUpMessageList": _field(
                "Messages de level-up", "messagelist",
                description="Liste de messages avec poids de probabilité.",
                default=["Bravo {mention}, tu as atteint le niveau {lvl} !"],
                weight_field="levelUpMessageWeights",
                variables="{mention}, {lvl}"
            ),
        },
    },

    "moduleYoutube": {
        "label": "YouTube",
        "description": "Notifications de nouvelles vidéos YouTube.",
        "icon": "▶️",
        "category": "Médias & Streaming",
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
        "category": "Esport & Jeux",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "minecraftChannelId": _field(
                "Salon statut", "channel", required=True,
                description="Salon pour le message de statut (créé automatiquement)."
            ),
            "minecraftPinMessage": _field(
                "Épingler le message de statut", "boolean", default=False,
                description="Épingler automatiquement le message de statut."
            ),
            "minecraftMessageId": _field(
                "Message statut", "message", hidden=True,
                channel_field="minecraftChannelId",
                description="ID interne (géré automatiquement)."
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
            "minecraftSftpHost": _field(
                "Hôte SFTP", "string",
                description="Adresse IP du serveur SFTP (par défaut, même que l'IP du serveur)."
            ),
            "minecraftSftpPort": _field(
                "Port SFTP", "number", default=2225,
                description="Port du serveur SFTP."
            ),
            "minecraftSftpUsername": _field(
                "Utilisateur SFTP", "string", default="Discord",
                description="Nom d'utilisateur pour la connexion SFTP."
            ),
            "minecraftSftpsPassword": _field(
                "Mot de passe SFTP", "secret",
                description="Mot de passe SFTP pour l'accès aux fichiers.", secret=True
            ),
            "minecraftModpackName": _field(
                "Nom du modpack", "string",
                description="Nom du modpack Minecraft."
            ),
            "minecraftModpackUrl": _field(
                "URL du modpack", "string",
                description="Lien vers la page du modpack."
            ),
            "minecraftModpackVersion": _field(
                "Version du modpack", "string",
                description="Version actuelle du modpack."
            ),
            "minecraftStatusUrl": _field(
                "URL page de statut", "string",
                description="Lien vers la page de statut du serveur."
            ),
            "minecraftFooterText": _field(
                "Texte du footer", "string",
                description="Texte affiché en bas de l'embed en veille."
            ),
            "minecraftServerType": _field(
                "Type de serveur", "string",
                description="Type de serveur affiché dans le titre de l'embed (ex: Forge, Paper, Fabric). Laisser vide pour ne pas afficher."
            ),
        },
    },

    "moduleZevent": {
        "label": "Zevent",
        "description": "Suivi de l'événement Zevent en temps réel (dons, planning, streamers).",
        "icon": "🎉",
        "category": "Événements",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "zeventChannelId": _field(
                "Salon", "channel", required=True,
                description="Salon où le message de suivi est posté (créé automatiquement)."
            ),
            "zeventPinMessage": _field(
                "Épingler le message de suivi", "boolean", default=False,
                description="Épingler automatiquement le message de suivi."
            ),
            "zeventMessageId": _field(
                "Message", "message", hidden=True,
                channel_field="zeventChannelId",
                description="ID interne (géré automatiquement)."
            ),
            "zeventStreamlabsApiUrl": _field(
                "URL Streamlabs", "url",
                description="URL de l'API Streamlabs Charity pour les dons.",
                default="https://streamlabscharity.com/api/v1/teams/@zevent-2025/zevent-2025"
            ),
            "zeventEventStartDate": _field(
                "Début de l'événement", "string",
                description="Date/heure de début du concert pré-événement (ISO 8601, ex: 2025-09-04T17:55:00+00:00).",
                default="2025-09-04T17:55:00+00:00"
            ),
            "zeventMainEventStartDate": _field(
                "Début du Zevent", "string",
                description="Date/heure de début du Zevent principal (ISO 8601).",
                default="2025-09-05T16:00:00+00:00"
            ),
            "zeventUpdateInterval": _field(
                "Intervalle de mise à jour (secondes)", "number",
                description="Fréquence de mise à jour du message en secondes. Nécessite un redémarrage.",
                default=30
            ),
            "zeventMilestoneInterval": _field(
                "Intervalle des paliers (dons)", "number",
                description="Montant entre chaque notification de palier de dons.",
                default=100000
            ),
        },
    },

    "moduleSpeedons": {
        "label": "Speedons",
        "description": "Planning et suivi en temps réel de l'événement Speedons.",
        "icon": "🏃",
        "category": "Événements",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "speedonsChannelId": _field(
                "Salon", "channel", required=True,
                description="Salon contenant les messages du planning (créés automatiquement)."
            ),
            "speedonsPinMessages": _field(
                "Épingler les messages", "boolean", default=False,
                description="Épingler automatiquement les messages planning et live."
            ),
            "speedonsScheduleMessageId": _field(
                "Message planning", "message", hidden=True,
                channel_field="speedonsChannelId",
                description="ID interne (géré automatiquement)."
            ),
            "speedonsLiveMessageId": _field(
                "Message run en cours", "message", hidden=True,
                channel_field="speedonsChannelId",
                description="ID interne (géré automatiquement)."
            ),
            "speedonsApiUrl": _field(
                "URL API", "url",
                description="URL de base de l'API Speedons (inclut le slug de la campagne).",
                default="https://tracker.speedons.fr/api/campaigns?slug=2025"
            ),
            "speedonsIconUrl": _field(
                "URL de l'icône", "url",
                description="URL de l'icône affichée dans les embeds.",
                default="https://speedons.fr/static/b476f2d8ad4a19d2393eb4cff9486cc9/c6b81/icon.png"
            ),
        },
    },

    "moduleStreamlabsCharity": {
        "label": "Streamlabs Charity",
        "description": "Suivi d'une campagne Streamlabs Charity en direct.",
        "icon": "❤️",
        "category": "Événements",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "streamlabsChannelId": _field(
                "Salon", "channel", required=True,
                description="Salon pour le message de suivi (créé automatiquement)."
            ),
            "streamlabsPinMessage": _field(
                "Épingler le message", "boolean", default=False,
                description="Épingler automatiquement le message de suivi."
            ),
            "streamlabsTeamUrl": _field(
                "URL de la team", "url",
                default="https://streamlabscharity.com/teams/@streamers-4-palestinians/streamers-4-palestinians",
                description="URL publique de la team Streamlabs Charity."
            ),
            "streamlabsMessageId": _field(
                "Message suivi", "message", hidden=True,
                channel_field="streamlabsChannelId",
                description="ID interne (géré automatiquement)."
            ),
        },
    },

    "moduleEmbedManager": {
        "label": "Gestionnaire d'Embeds",
        "description": "Création et publication d'embeds personnalisés.",
        "icon": "📝",
        "category": "Outils",
        "fields": {
            "enabled": _field("Activé", "boolean", default=False),
            "channelId": _field(
                "Salon de publication", "channel", required=True,
                description="Salon pour publier les embeds (message créé automatiquement)."
            ),
            "pinMessage": _field(
                "Épingler le message", "boolean", default=False,
                description="Épingler automatiquement le message publié."
            ),
            "messageId": _field(
                "Message cible", "message", hidden=True,
                channel_field="channelId",
                description="ID interne (géré automatiquement)."
            ),
            "embeds": _field(
                "Embeds", "embedlist",
                description="Créez des embeds avec un titre, couleur et liens.",
                default=[],
            ),
        },
    },

    "discord2name": {
        "label": "Discord → Prénoms",
        "description": "Mapping des IDs Discord vers des prénoms pour ce serveur.",
        "icon": "👤",
        "category": "Outils",
        "noToggle": True,
        "directValue": True,
        "fields": {
            "discord2name": _field(
                "Membres", "discord2name",
                description="Associez un prénom à chaque ID Discord."
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



    "OpenRouter": {
        "label": "OpenRouter",
        "icon": "🤖",
        "fields": {
            "openrouterApiKey": _field("Clé API", "secret", required=True,
                                       description="Clé API OpenRouter.", secret=True),
            "modelsToCompare": _field("Modèles à comparer", "number", default=3,
                                      description="Nombre de modèles IA à comparer par question."),
            "models": _field(
                "Modèles IA", "models",
                description="Liste des modèles IA disponibles pour la comparaison. "
                            "Chaque modèle nécessite un identifiant provider, un model_id OpenRouter et un nom d'affichage.",
            ),
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

    "backup": {
        "label": "Sauvegarde BDD",
        "icon": "💾",
        "fields": {
            "enabled": _field("Activé", "boolean", default=True,
                              description="Activer la sauvegarde automatique quotidienne."),
            "backupDir": _field("Dossier de sauvegarde", "string", default="data/backups",
                                 description="Chemin du dossier où les sauvegardes sont stockées."),
            "maxBackups": _field("Nombre de sauvegardes", "number", default=7,
                                  description="Nombre de sauvegardes à conserver (les plus anciennes sont supprimées)."),
            "backupHour": _field("Heure de sauvegarde", "number", default=4,
                                  description="Heure locale à laquelle la sauvegarde quotidienne est effectuée (0-23)."),
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
            "developerUserIds": _field("Developer User IDs", "list", required=False,
                                       description="IDs Discord autorisés à accéder aux pages Extensions et Logs (réservées au développeur)."),
        },
    },
}
