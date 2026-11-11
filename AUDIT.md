# Audit du bot Discord « Michel » — 11/11/2026

Légende : **[V]** vérifié (lu dans le code, reproduit ou testé) · **[S]** supposé (déduit, non reproduit).
Gravité : critique / haute / moyenne / basse. Effort : S (< ½ j), M (½–2 j), L (> 2 j).

## Résumé exécutif

Le socle est d'un niveau rare pour un bot communautaire : architecture en couches testée, config validée,
dashboard durci, CI complète, Docker propre. Aucune faille critique n'a été trouvée ; les problèmes sont
dans la **logique des extensions** (27 k lignes, hors couverture de tests). Les 3 urgences :

1. **Le listener de réactions des sondages réécrit des messages qui ne sont pas des sondages** (B01, [V] reproduit) :
   il reconnaît un sondage à la seule couleur de l'embed, partagée par les posts RSS, menus de rôles,
   suggestions, giveaways, MDI, logs de modération. Une réaction suffit pour tronquer leur texte au premier
   `:` ; un menu de rôles à emojis personnalisés est réduit à `<`.
2. **Les tâches planifiées peuvent se chevaucher et les « réclamations » ne sont pas atomiques** (B02, [V]) :
   interactions.py lance chaque exécution sans attendre la précédente ; giveaways (double tirage possible avec
   `/giveaway end`), rappels, clôtures de sondages, dépenses récurrentes Tricount n'ont aucun verrou.
3. **Plusieurs vecteurs d'abus par le bot** (S01–S03, [V]) : `/pick` et `/shuffle` (commandes globales,
   tout membre) recrachent le texte saisi sans `allowed_mentions` → `@everyone` via le bot ; `/send`,
   `/delete`, `/lock`, `/slowmode` agissent sur un autre salon sans vérifier les droits de l'auteur
   dans ce salon ; `/reminder set` permet d'envoyer des DM quotidiens à 25 personnes arbitraires, sans
   opt-out possible.

Quick win à part : `/ban jours_messages:7` ne supprime que **7 heures** de messages (B03, bug de la librairie
sur un paramètre déprécié).

## 1. Prise de connaissance

**Stack** [V] : Python ≥ 3.12 (image Docker et CI en 3.15), `discord-py-interactions` **5.16.0**
(pas discord.py), MongoDB via **motor 3.7.1**, aiohttp 3.14.5, FastAPI 0.162 / Starlette 1.10 / uvicorn 0.54
(dashboard optionnel), Pydantic 2.14, Pillow 13, openai 3.41, spotipy, twitchAPI 4.5, ldap3, asyncssh,
prometheus-client, sentry-sdk. Dépendances déclarées en plages dans `pyproject.toml`, figées dans `uv.lock`.

**Lancement** : `uv run python main.py` ou `docker compose up -d` (image GHCR, volumes `config/ data/ logs/`,
Web UI liée à 127.0.0.1:8080). Configuration unique `config/config.json` (sections globales `config.*`, modules
par serveur `servers.<id>.module*`), aucune variable d'environnement hormis `MICHEL_DEBUG` et
`MICHEL_LOG_FORMAT`. `config.example.json` est généré par `scripts/generate_config_example.py`.

**Architecture** : `extensions/` (38 entrées, couche Discord, auto-découvertes par `main.py`) →
`features/` (domaine + `repository.py` MongoDB, sans import `interactions`) → `src/core` (config, db, http,
logs, erreurs), `src/discord_ext` (helpers UI), `src/integrations` (clients API), `src/webui` (FastAPI dans un
thread). Une base MongoDB par serveur (`guild_<id>`) + `global`. Respect des couches vérifié par
`tests/test_layering.py`.

**Flux d'une slash command** : gateway → interactions.py résout la commande (portée par serveur via
`guild_scopes(MODULE.guild_ids())`) → `global_pre_run_callback` (`src/discord_ext/interaction_context.py`)
lie guild/commande/correlation id au contexte de log → handler de l'extension (lit la config en direct via
`ModuleConfig`, appelle un repository `features/*`, construit l'embed) → `ctx.send`/`ctx.defer`. Exception non
gérée → `extensions/errors.py` répond en éphémère FR avec une référence et loggue la trace.
Tâches de fond : `@Task.create` démarrées dans `on_startup`, instrumentées par `src/discord_ext/task_health.py`.

**API dépréciées** [V] : `Guild.ban(delete_message_days=…)` (déprécié **et** mal converti par la lib, B03) ;
`motor` est déprécié par MongoDB au profit de l'API async native de PyMongo (T02) ; `pytz` (préférer
`zoneinfo`). `Guild.gateway_chunk` utilisé par lldap est l'API non dépréciée. Aucun `DeprecationWarning` émis
pendant la suite de tests (filtrés dans `pyproject.toml`, à surveiller).

**Exécutions réalisées** [V] :
| Outil | Résultat |
|---|---|
| `uv sync` (lock) | OK |
| `pytest --cov` (Python 3.13 local) | **1518 passés, 43 ignorés** (tests MongoDB, pas de `MONGO_TEST_URL`), couverture 78,0 % (seuil 77 %) |
| `ruff check` / `ruff format --check` | 0 erreur / 495 fichiers conformes |
| `mypy src features` | 0 erreur (228 fichiers) |
| `pip-audit` (deps runtime exportées du lock) | aucune vulnérabilité connue |
| Démarrage réel, `config.example.json` + faux token, répertoire jetable | 38 extensions chargées sans erreur, arrêt attendu sur `401 Unauthorized`, sortie code 1 (relance par le superviseur) |
| Scan d'historique git (clone superficiel, 136 commits) | aucun motif de token Discord/OpenAI/GitHub/Google trouvé |
| Balayage AST des ~110 handlers (slash, sous-commandes, boutons, modals) : `await` avant le premier acquittement | appels réseau avant réponse seulement dans `/delete`, `/addsong`, `/songinfo`, `/addwithvote`, `/feurstats`, `/leaderboard` (B08, B09) ; `/confrerie demande` borné à 2 s ; les autres ne font qu'une lecture MongoDB |
| Balayage AST des routes Web UI | toutes les routes `/api/*` passent par `require_guild_admin`/`require_developer`/`require_admin` ; seules `/auth/*`, `/health` et le front sont publics (attendu) ; pages publiques protégées par jeton |
| Vérification dans la lib installée | `Task._task_loop` lance chaque exécution sans attendre la précédente (B02) ; `Guild.ban(delete_message_days)` → `× 3600` (B03) |

Tests, lint, mypy, pip-audit et démarrage d'essai ont été lancés sur le commit `7b22e13` (HEAD actuel).

**Limites** : extensions lues en détail : polls, giveaway, reminders, moderation, admin, xp, spotify
(votes/playlist), rss, random_, feur, welcome, birthday, starboard, lldap, secretsanta, userinfo, errors,
tricount (récurrences), twitch (cycle de vie EventSub), zunivers (rappels) ; survolées seulement : zevent,
confrerie, mdi, vlrgg, olympics, streamlabs, satisfactory, minecraft, uptime, compareai, embedmanager.
Les tests MongoDB n'ont pas tourné (pas de `MONGO_TEST_URL`).

## 2. Tableau des constats

| ID | Cat. | Gravité | Effort | Fichier:ligne | Description |
|---|---|---|---|---|---|
| B01 | bug | haute | S | extensions/polls/__init__.py:440,457 ; src/discord_ext/paginator.py:128 | Toute réaction sur un embed couleur `Colors.UTIL` (RSS, menus de rôles, suggestions, giveaways…) le réécrit comme un sondage [V] |
| B02 | bug | haute | M | extensions/giveaway/__init__.py:569,597 ; features/giveaway/repository.py:69 (+ reminders, polls, tricount) | Exécutions de `Task` concurrentes + marquage non atomique → doubles tirages / envois [V] |
| B03 | bug | moyenne | S | extensions/moderation/commands.py:331 | `/ban jours_messages:N` supprime N **heures** (param. déprécié, lib : jours × 3600) [V] |
| B04 | bug | moyenne | S | extensions/spotify/votes.py:427,448 | Un membre aux DM fermés bloque les rappels de vote de tout le serveur, chaque minute [V] |
| B05 | bug | moyenne | S | extensions/spotify/votes.py:476-517 ; extensions/zunivers/reminders.py:225-262 | Suppression de rappel : le clic d'un membre supprime aussi le rappel d'un autre (même `custom_id`) ; `UnboundLocalError` au timeout ; `ActionRow` vide ou > 5 boutons [V] |
| B06 | bug | moyenne | S | extensions/reminders/__init__.py:315-339 ; extensions/tricount/recurring.py:240-265 | Effet de bord puis reprogrammation : un échec d'écriture renvoie le rappel chaque minute / recrée la dépense récurrente toutes les 5 min [V] |
| B07 | bug | moyenne | S | extensions/giveaway/__init__.py:604,624,392,225 | Échec transitoire de fetch → giveaway clos sans gagnant ; pied « Lancé par ID:… »/par l'annulateur ; `prize` sans `max_length` [V] |
| B08 | bug | moyenne | S | extensions/admin/__init__.py:117-133 | `/delete` sans `defer` (purge longue → interaction expirée), sans `max_value` ni `channel_types` [V] |
| B09 | bug | moyenne | S | extensions/spotify/playlist.py:69,323 ; spotify/votes.py:531 ; extensions/feur.py:110 ; extensions/xp/commands.py:156 | `/addsong`, `/songinfo`, `/addwithvote`, `/feurstats`, `/leaderboard` font des appels réseau avant tout acquittement (risque > 3 s) [V lecture / S délai] |
| B10 | bug | moyenne | M | extensions/giveaway/__init__.py:518 ; extensions/polls/__init__.py:440 | Chaque réaction (tous serveurs) → requête MongoDB + relecture de tous les réacteurs ; verrous globaux [V] |
| B11 | bug | basse | S | extensions/rss/__init__.py:129,401-406 | Template RSS invalide (`ValueError`/`AttributeError`) ou envoi refusé → entrée retentée indéfiniment [V] |
| B12 | bug | basse | S | extensions/xp/leveling.py:51,65 ; features/xp/repository.py:65 | `list_collection_names()` à chaque message XP ; cooldown non atomique [V] |
| B13 | bug | basse | S | config.example.json ; main.py:164 | Les 4 extensions « désactivées par défaut » (minecraft, olympics, speedons, zevent) se chargent avec l'exemple [V démarrage] |
| B14 | bug | basse | S | extensions/userinfo.py:26-34 | Snapshot des membres au `on_startup` sur des listes possiblement incomplètes, pour tous les serveurs [S] |
| B15 | bug | basse | S | extensions/polls/__init__.py:240-252 | Double mécanisme de clôture des sondages à réactions (tâche mémoire + tâche planifiée) [V] |
| B16 | bug | basse | S | extensions/moderation/commands.py:223,~300 | `/untimeout` sans contrôle de hiérarchie ; DM « tu as été banni » envoyé avant un ban qui peut échouer (raison > 512 car.) [V] |
| B17 | bug | basse | S | extensions/twitch/eventsub.py:149 ; extensions/twitch/__init__.py:301-311 | Twitch installe son propre handler SIGTERM qui appelle `bot.stop()` en parallèle de `GracefulShutdown` → sortie possible avant la fermeture des autres extensions, de MongoDB et du HTTP [V lecture / S chronologie] |
| B18 | bug | basse | S | extensions/random_.py:45,98 | `/pick`, `/shuffle` sans `max_length` : une liste longue dépasse 2000 caractères → 400, le membre reçoit l'erreur générique [V] |
| B19 | bug | basse | S | extensions/moderation/__init__.py:27 ; extensions/confrerie/requests.py:96, editors.py:157 | Caches jamais purgés : une `deque` anti-spam par membre ayant écrit ; saisies de modal abandonnées gardées à vie [V] |
| S01 | sécurité | haute* | S | extensions/random_.py:52,107 ; extensions/rss/__init__.py:401 ; main.py:79 | Aucune politique `allowed_mentions` : `/pick @everyone` (global, tout membre) et titres RSS tiers peuvent pinger [V code / S effet] |
| S02 | sécurité | moyenne | S | extensions/admin/__init__.py:127,183,~273,~319 | `/delete`, `/send`, `/lock`, `/slowmode` agissent sur un salon où l'auteur n'a pas forcément de droits [V] |
| S03 | sécurité | moyenne | S | extensions/reminders/__init__.py:137,54 ; features/reminders/repository.py:65 | DM récurrents vers 25 destinataires arbitraires, sans consentement, sans quota, sans désinscription [V] |
| S04 | sécurité | moyenne | S | src/core/text.py:170 ; extensions/rss/__init__.py:117 ; features/youtube/videos.py:53 | `{mention:>2000000000}` dans un message configuré → allocation géante → OOM du bot pour tous les serveurs [V 200 Mo/0,12 s] |
| S05 | sécurité | moyenne | S | extensions/secretsanta/draws.py:103 | `/secretsanta draw` lançable par n'importe qui (cancel/reveal sont protégés) ; deux `draw` simultanés tirent deux fois [V] |
| S06 | sécurité | moyenne | S | extensions/starboard/listeners.py:87 | Un message d'un salon NSFW est recopié dans le starboard (SFW) [V absence de contrôle] |
| S07 | sécurité | basse | S | src/webui/app.py:142 | `/openapi.json` reste exposé (docs désactivées mais pas le schéma) [V] |
| S08 | sécurité | basse | S | .gitignore ; src/core/db.py:216 | `data/` (sauvegardes MongoDB) n'est pas ignoré par git [V] |
| S09 | sécurité | basse | S | moderation/commands.py:185, admin `/slowmode`, lldap, zunivers/corporation.py:60 | Texte brut d'exception montré aux utilisateurs (public pour zunivers) [V] |
| S10 | sécurité | basse | S | pyproject.toml | Dépendances inutilisées : `beautifulsoup4`, `rdoclient`, `python-dotenv` (surface inutile) [V] |
| S11 | sécurité | info | S | historique git | Clone superficiel : scan complet (gitleaks/trufflehog sur clone complet) recommandé [V partiel] |
| S12 | sécurité | basse | S | main.py:55 | Hors Docker, la Web UI écoute par défaut sur `0.0.0.0` (le compose la publie sur 127.0.0.1, mais pas un lancement `uv run`) [V] |
| T01 | structure | haute | S | src/discord_ext/task_health.py | Pas de garde anti-chevauchement générique des `Task` (seuls rss, metrics, twitch en ont) [V] |
| T02 | structure | moyenne | L | src/core/db.py + repositories | Motor déprécié → migrer vers `pymongo.AsyncMongoClient` [V version / S calendrier] |
| T03 | structure | moyenne | S | — | Pas de helper d'envoi sûr (`allowed_mentions`, troncature 2000/4096/256) ; chaque extension improvise [V] |
| T04 | structure | moyenne | L | tests/, pyproject.toml | Couverture mesurée sur `src`+`features` seulement ; les 27 k lignes d'`extensions/` (où sont B01–B16) n'ont pas de tests de handlers [V] |
| T05 | structure | basse | M | 267 appels `logger.error(..., e)` | Erreurs sans `exc_info` → ni trace ni remontée Sentry [V] |
| T06 | structure | basse | S | main.py:35-45 | Au démarrage seul le token est vérifié ; la validation de schéma (`scripts/check_config.py`) n'est pas exécutée [V] |
| T07 | structure | basse | M | 73 `datetime.now()` naïfs + pytz | Planification en heure locale naïve (changements d'heure), `pytz` au lieu de `zoneinfo` [S] |
| T08 | structure | basse | M | extensions/* | Identification de « nos » messages par couleur/pied d'embed ; `_repo()` cache recopié dans chaque extension [V] |
| T09 | structure | basse | L | src/webui/static/index.html | Pas de CSP (handlers inline), déjà noté dans CLAUDE.md [V] |
| F01 | fonctionnel | moyenne | M | giveaway, polls (`message_id` en texte) | Désigner un giveaway/sondage par ID brut → autocomplétion des actifs ou commandes de menu contextuel |
| F02 | fonctionnel | moyenne | M | extensions/giveaway | Participation par bouton (atomique, sans relecture des réactions) plutôt que par réaction |
| F03 | fonctionnel | basse | S | extensions/reminders | Pas de `/reminder list` ; destinataires sans désinscription ; préfixe « (snooze) » qui s'empile |
| F04 | fonctionnel | basse | S | — | Aucune commande `/aide` ; 5 cooldowns seulement (aucun sur `/pick`, `/roll`, `/shuffle`, `/feurstats`) |
| F05 | fonctionnel | basse | M | — | Interface 100 % FR alors que `/pick`, `/roll`, `/shuffle`, `/ask` sont installables par n'importe quel utilisateur : localisations Discord |

\* S01 est **haute** si le rôle du bot a « Mentionner @everyone » ou Administrateur sur un serveur, moyenne sinon.

## 3. Détail des constats et correctifs

### B01 — Le listener des sondages corrompt d'autres messages [V, reproduit]
`on_message_reaction_add/remove` (polls/__init__.py:440-471) traite **tout** message dont le premier embed a
la couleur `0x3489EB` (`Colors.UTIL`) comme un sondage à réactions, sans vérifier l'auteur, le module du
serveur ni l'existence du sondage en base. `format_poll` découpe la description sur `\n\n`, coupe chaque bloc au
premier `:` et retire les `**`. Couleur partagée par : posts RSS (couleur par défaut), menus de rôles
(`src/discord_ext/rolemenus.py:32`), suggestions en attente, giveaways actifs, MDI, logs de modération.
Reproduction (script jetable sur la vraie fonction) :
- `"**Nouvel article : Sortie de la v2**\n\nLire la suite : https://…"` → `"Nouvel article \n\nLire la suite "`
- menu de rôles `"<:pepe:123456> <@&42> — Joueurs\n…"` → `"<"`

Scénario : un membre ajoute 👍 sous un menu de rôles ou un post RSS → le bot édite le message et le casse.
Correctif : n'agir que sur un sondage connu, et seulement là où le module est actif.
```python
async def _reaction_poll(self, message) -> Poll | None:
    if message.guild is None or not UTILS.enabled(message.guild.id):
        return None
    if message.author.id != self.bot.user.id:
        return None
    poll = await self._poll_repo(message.guild.id).get_by_message(str(message.id))
    return poll if poll and poll.mode == "reaction" and not poll.closed else None
```
(Les anciens sondages non stockés peuvent être reconnus par `parse_poll_author_id(footer)` en repli.)
Ajouter un test de non-régression avec un embed RSS et un menu de rôles.

### B02 / T01 — Tâches concurrentes et réclamations non atomiques [V]
`Task._task_loop` (lib, `models/internal/tasks/task.py`) crée une `asyncio.Task` par déclenchement sans attendre
la précédente : une exécution plus longue que l'intervalle tourne en parallèle de la suivante. Aucun verrou
dans giveaway (30 s), polls (30 s), reminders (1 min), spotify `reminder_check` (1 min), tricount `recurring_tick`
(5 min). En plus, `mark_drawn` est un `update_one` sans condition, et `/giveaway end` peut tirer en même temps
que le planificateur. Scénarios : tirage lent (beaucoup de participants à paginer) → deux annonces avec des
gagnants différents ; `/giveaway end` à l'échéance → idem ; dépense récurrente créée deux fois.
Correctifs :
1. Réclamation atomique avant tout effet de bord :
```python
async def claim_for_draw(self, giveaway_id: str) -> bool:
    res = await self._col().update_one(
        {"_id": ObjectId(giveaway_id), "drawn": False, "cancelled": False},
        {"$set": {"drawn": True, "drawing_at": datetime.now()}},
    )
    return res.modified_count == 1
```
   (même principe : `find_one_and_update` pour rappels, sondages, récurrences Tricount).
2. Garde générique dans `task_health.install()` : ignorer un déclenchement quand l'exécution précédente de la
   même `Task` n'est pas finie (dictionnaire `Task → asyncio.Lock`, `if lock.locked(): skip + compteur`).

### B03 — `/ban` supprime des heures, pas des jours [V]
`ctx.guild.ban(member, delete_message_days=delete_days, …)` ; la lib (guild.py) émet un `DeprecationWarning` et
calcule `delete_message_seconds = delete_message_days * 3600`. `/ban jours_messages:7` sur un raider ne supprime
que 7 h de messages. Correctif : `delete_message_seconds=delete_days * 86400`.

### B04 — Rappels de vote Spotify bloqués par un DM fermé [V]
`_reminder_check_for_server` (spotify/votes.py:437-468) : `await user.send(...)` n'est pas protégé. Un membre
aux DM fermés lève `Forbidden` → boucle interrompue avant `user_ids.remove` et `save_reminders` → il reste « dû »,
re-échoue chaque minute, et les membres traités après lui ne reçoivent jamais leur rappel.
Correctif : `try/except` autour de l'envoi (log + on avance quand même), sauvegarde dans un `finally`.

### B05 — Suppression de rappels Spotify et ZUnivers [V]
- `custom_id=str(remind_time.timestamp())` est le même pour tous les membres qui ont un rappel à la même heure,
  et `wait_for_component` n'est filtré ni par message ni par auteur. Scénario : A et B ont un rappel à 20:00 et
  lancent `/setreminder remove` ; B clique sur son bouton → **les deux** attentes se résolvent, le handler de A
  retire A de 20:00 sans que A ait cliqué, puis échoue sur `edit_origin` (interaction de B déjà acquittée).
  Même motif dans `zunivers/reminders.py:225-262` (`custom_id=f"{timestamp}_{type}"`).
- Spotify : `except TimeoutError:` utilise `button_ctx` jamais assigné → `UnboundLocalError` (ligne 517).
- Spotify : `ActionRow(*buttons)` : 0 rappel → rangée vide refusée par Discord ; > 5 rappels → 400.
Correctif : message d'erreur si aucun rappel, `StringSelectMenu` (≤ 25 options) avec un `custom_id` préfixé
par l'ID de l'utilisateur, `wait_for_component(messages=msg, check=lambda c: c.ctx.author.id == ctx.author.id)`
(le modèle correct existe déjà dans `birthday.py:270`, qui passe `messages=`).

### B06 — Rappels : ordre envoi → reprogrammation [V]
`check_reminders` envoie les DM puis reprogramme ; si la mise à jour MongoDB échoue (exception loggée), le rappel
reste dû et est renvoyé **chaque minute** jusqu'à ce que l'écriture réussisse. Avec B02, une exécution lente peut
aussi le doubler. Même ordre dans `tricount/recurring.py:_materialise_recurring` : `add_expense` puis
`reschedule_recurring` ; si la seconde échoue, la dépense est recréée toutes les 5 minutes et fausse les bilans.
Correctif : reprogrammer/supprimer d'abord via `find_one_and_update` conditionnel
(`remind_time == ancienne valeur`, `next_run == ancienne valeur`), n'agir que si la réclamation a réussi.

### B07 — Giveaway : fins silencieuses et affichage [V]
- `_draw` : si `_fetch_message` échoue pour une raison transitoire (5xx, timeout), le giveaway est marqué
  `drawn` avec 0 gagnant, définitivement. Ne clore sans gagnant que sur 404 (`NotFound`) ; sinon réessayer au
  prochain cycle (compteur d'échecs, abandon après N essais + message à l'hôte).
- Après tirage le pied devient « Lancé par ID:123… » (ligne 624), après annulation « Lancé par <annulateur> »
  (ligne 392) : réutiliser le pied existant comme le fait `_refresh_participants_embed`.
- `prize` sans `max_length` : titre `🎁 GIVEAWAY — {prize}` > 256 car. → 400. Ajouter `max_length=200`
  (et 1000 sur `description`).

### B08 — `/delete` [V]
`channel.purge(...)` avant toute réponse : une purge de centaines de messages (ou de messages > 14 jours,
supprimés un par un) dépasse 3 s → `ctx.send` final échoue (« Unknown interaction ») alors que la purge a eu lieu.
Pas de `max_value` sur `nombre`, pas de `channel_types` (une catégorie ou un salon vocal → `AttributeError`).
Correctif : `await ctx.defer(ephemeral=True)` en tête, `max_value=1000`, `channel_types=[GUILD_TEXT, GUILD_NEWS,
threads]`, renvoyer le nombre réellement supprimé. Voir aussi S02.

### B09 — Commandes lentes sans acquittement [V lecture, S délai]
`/addsong` (Spotify : `track` + `playlist_add_items` + MongoDB + rendu d'image, sur un worker unique qui peut
dormir sur un 429), `/songinfo` (Spotify + image), `/addwithvote` (Spotify `track`), `/feurstats` (agrégats + un
`fetch_member` séquentiel par membre parti jusqu'à en trouver 5), `/leaderboard` (un `fetch_user` séquentiel
pour **chaque** membre classé absent du cache, sans limite du nombre d'entrées : sur un serveur ancien avec
beaucoup de départs, le premier appel après redémarrage dépasse 3 s).
Correctif : `await ctx.defer()` en tête (ou `@auto_defer()`, déjà utilisé par `/ask`) ; pour `/leaderboard`,
limiter aux N premiers et afficher « membre parti » sans `fetch_user`.
Balayage systématique fait (cf. tableau des exécutions) : aucun autre handler ne fait d'appel réseau avant de
répondre.

### B10 — Coût des réactions [V]
Pour **chaque** réaction de **chaque** serveur : giveaway fait une requête MongoDB (`get_by_message`, même si le
module est inactif), polls (B01) relit tous les réacteurs de toutes les réactions, et la lib refait un `GET` du
message s'il n'est pas en cache. Un giveaway à 1 000 participants relit ~10 pages de réacteurs à chaque nouvelle
réaction (O(n²) appels → limites de débit). Les deux extensions sérialisent tout via un verrou global.
Correctif : filtrer d'abord sur le module actif + emoji + cache mémoire des IDs de messages suivis ; debounce de
la mise à jour d'embed (ex. 5 s par message) ; à terme F02 (boutons).

### B11 — RSS : entrées « poison » [V]
`_format_template` n'attrape que `KeyError/IndexError` ; `{title:d}` (`ValueError`) ou `{title.x}`
(`AttributeError`) fait échouer l'envoi, l'entrée reste non vue et est retentée à chaque cycle, pour toujours ;
idem pour un refus permanent de Discord (salon sans permission, embed trop gros). Correctif : attraper
`ValueError/AttributeError` (comme `features/youtube/videos.py:54`), valider le template à l'enregistrement,
et marquer vue une entrée après N échecs d'envoi.

### B12 — XP par message [V]
`on_message` → `ensure_collection` exécute `list_collection_names()` à chaque message éligible (4 allers-retours
MongoDB par message). Le cooldown est lu puis écrit : deux messages quasi simultanés gagnent tous deux de l'XP.
Correctif : `set` des guildes déjà initialisées ; mettre le cooldown dans le filtre de `award_xp`
(`{"_id": uid, "time": {"$lte": ts - XP_COOLDOWN_SECONDS}}`).

### B13 — Extensions « désactivées par défaut » chargées [V, démarrage d'essai]
README et CLAUDE.md affirment que minecraft, olympics, speedons, zevent sont livrées désactivées dans
`config.example.json` ; l'exemple n'a aucune section `config.extensions` et les 4 se chargent. Correctif :
générer `"extensions": {"extensions.minecraft": false, …}` dans `scripts/generate_config_example.py`.

### B14 — `userinfo.on_startup` [S]
Parcourt `guild.members` de tous les serveurs au démarrage : avec un cache de membres pas encore rempli, le
snapshot est partiel ; écrit dans la base de chaque serveur, même sans usage. À confirmer en prod (taille du cache
au `Startup`) ; sinon `await guild.http_chunk()` ou suppression du snapshot initial.

### B15 — Double clôture des sondages à réactions [V]
Un `asyncio.create_task(sleep(seconds))` en mémoire (polls:240-252) **et** `check_closing_polls` en base
clôturent le même sondage. Inoffensif mais redondant ; garder la tâche planifiée seule.

### B16 — Modération [V]
`/untimeout` n'appelle pas `can_moderate` (un modérateur lève l'exclusion posée par un rang supérieur).
`/kick`/`/ban` envoient le DM avant l'action : si elle échoue (raison > 512 caractères, permission), la personne
a été notifiée à tort. Ajouter `max_length=512` sur `raison` et envoyer le DM après succès (en gardant la
possibilité de DM avant pour le ban, en le reformulant si échec).

### B17 — Deux gestionnaires de SIGTERM [V lecture, S chronologie]
`_start_eventsub` appelle `signal.signal(SIGTERM, self.stop_on_signal)` (eventsub.py:149) alors que `main.py`
a déjà installé `GracefulShutdown` via `loop.add_signal_handler`. Les deux s'exécutent au `docker compose stop` :
`GracefulShutdown` libère les extensions une par une puis ferme HTTP/MongoDB/Sentry, pendant que la tâche
`cleanup()` de Twitch ferme EventSub puis appelle `bot.stop()`. Si la gateway s'arrête d'abord, `client.start()`
rend la main et `main.py` fait `os._exit(0)` (`shutdown.requested` est vrai) avant la fin de l'arrêt ordonné.
Correctif : supprimer `signal.signal(...)`, `stop_on_signal` et `cleanup()` ; `async_drop` est déjà appelé
par `GracefulShutdown`.

### B18 — `/pick`, `/shuffle` : sortie > 2000 caractères [V]
Options texte sans `max_length` (Discord accepte jusqu'à 6000) ; 100 éléments numérotés dépassent 2000
caractères → 400. Ajouter `max_length=1500` (ou tronquer la sortie) ; voir aussi S01 pour les mentions.

### B19 — Caches mémoire sans purge [V]
`ModerationExtension._spam` garde une `deque` par (serveur, membre) ayant écrit depuis le démarrage, même vide ;
`confrerie._pending_demandes/_pending_editors` gardent la saisie d'un modal jamais soumis. Croissance lente
(quelques Ko par millier de membres), mais sans borne. Supprimer la clé quand la deque est vide ; expirer les
saisies en attente (horodatage + purge à l'ouverture d'un nouveau modal).

### S01 — Mentions non neutralisées [V code, S effet]
interactions.py 5.16 n'a pas de `allowed_mentions` par défaut au niveau client et aucun n'est configuré
(seuls compareai, zunivers/ninja et confrerie/review en passent). `/pick choix:@everyone;@here` et `/shuffle`
sont **globales** et ouvertes à tout membre ; le bot renvoie le texte tel quel. En mode texte, un flux RSS tiers
dont le titre contient `@everyone` pinge le serveur. L'effet dépend de la permission « Mentionner @everyone »
du bot dans le salon.
Correctif : `allowed_mentions=AllowedMentions.none()` sur toute sortie reprenant du texte d'utilisateur ou d'un
tiers (random_, rss texte, reminders, suggestions, tricount…), via un helper commun (T03).

### S02 — Actions sur un autre salon sans vérifier l'auteur [V]
`/delete`, `/send`, `/lock`, `/slowmode` acceptent un salon cible. `default_member_permissions` n'est évalué
que dans le salon où la commande est lancée, et peut être réattribué par les admins (Intégrations). Un membre
autorisé dans un salon peut ainsi purger/écrire/verrouiller un salon qu'il ne voit pas, avec les droits du bot.
Note [S] : `ADMINISTRATOR | MANAGE_MESSAGES` exige les deux bits, donc réserve de fait la commande aux admins.
Correctif :
```python
perms = ctx.author.channel_permissions(target)
if Permissions.MANAGE_MESSAGES not in perms:  # SEND_MESSAGES pour /send, MANAGE_CHANNELS pour /lock
    return await send_error(ctx, "Tu n'as pas ce droit dans ce salon.")
```
et `allowed_mentions` restreint pour `/send` si l'auteur n'a pas `MENTION_EVERYONE`.

### S03 — Rappels utilisables pour harceler [V]
`/reminder set destinataires:` accepte 25 IDs quelconques (ni membres du serveur, ni consentement), récurrence
quotidienne, nombre de rappels illimité ; le destinataire ne peut ni voir ni supprimer le rappel
(`list_for_user` filtre sur le créateur). Correctif : restreindre aux membres du serveur, quota par créateur
(ex. 20 rappels), bouton « Ne plus recevoir » dans le DM qui retire le destinataire, `/reminder list`.

### S04 — Amplification par spécification de format [V]
`pick_weighted_message` (`format_map`), `_format_template` RSS et `render_notification` YouTube acceptent les
spécifications de format. Un admin d'**un** serveur qui saisit `{mention:>2000000000}` dans un message de
bienvenue/niveau/anniversaire provoque une allocation de ~2 Go à l'événement suivant → OOM-kill du conteneur pour
**tous** les serveurs (mesuré : `>200000000` = 190 Mo en 0,12 s). Correctif : un `string.Formatter` restreint
(refus de `format_spec`, de la conversion `!r` et des accès `.`/`[]`), partagé par les trois sites, + validation
au moment de la sauvegarde.

### S05 — `/secretsanta draw` sans contrôle [V]
`cancel` et `reveal` exigent créateur/admin, pas `draw` : n'importe quel membre peut lancer le tirage avant que
tout le monde ait rejoint (session close, DM envoyés). De plus, `is_drawn` est lu puis le tirage est fait et les
DM envoyés avant l'enregistrement : deux `draw` simultanés envoient deux attributions différentes.
Correctif : même garde `is_creator or is_admin`, et réservation atomique de la session (`drawn: False → True`)
avant d'envoyer les DM.

### S06 — Starboard et NSFW [V absence de contrôle, S effet]
Aucune référence à `nsfw` dans le code : un message d'un salon NSFW atteignant le seuil est recopié dans le
salon starboard. Correctif : ignorer si `message.channel.nsfw and not starboard_channel.nsfw`.

### S07 à S11 [V]
- S07 : `FastAPI(..., openapi_url=None)` pour ne plus publier la carte de l'API.
- S08 : ajouter `data/` à `.gitignore` (les sauvegardes `data/backups/` contiennent des données personnelles).
- S09 : remplacer `{e}` par un message générique + la référence d'erreur (comme `extensions/errors.py`) ;
  `zunivers/corporation.py:60` l'écrit dans un salon public.
- S10 : retirer `beautifulsoup4`, `rdoclient`, `python-dotenv` (aucun import), puis `uv lock`.
- S11 : le clone est superficiel ; lancer `gitleaks detect` sur un clone complet. `.gitignore` couvre
  `config/*`, `.env*`, `logs/` ; `.dockerignore` exclut `config`, `data`, `logs` (image GHCR sans secret).
- S12 : `WEBUI_HOST` vaut `0.0.0.0` par défaut (`main.py:55`) : nécessaire dans le conteneur, mais un lancement
  direct expose le dashboard sur toutes les interfaces. Défaut `127.0.0.1`, et `0.0.0.0` posé explicitement
  dans l'image Docker (variable ou config).

### T02 — Motor déprécié [V version, S calendrier]
MongoDB a déprécié Motor au profit de l'API async native de PyMongo (`AsyncMongoClient`, PyMongo ≥ 4.10) ;
le support se limite aux correctifs critiques avant fin de vie. Tout passe par `src/core/db.py` et les
repositories : migration mécanique (imports, `to_list(length=None)` → `to_list()`, un client par boucle reste
nécessaire), validée par les tests MongoDB existants.

### T03 — Helper d'envoi sûr [V]
Centraliser dans `src/discord_ext/messages.py` : `safe_content(text, limit=2000)`, `AllowedMentions` par défaut
(`users` seulement), troncatures titres (256) / descriptions (4096) / champs (1024) — déjà partiellement présentes
(`[:4000]` dispersés). Option plus radicale : envelopper `process_allowed_mentions` pour appliquer un défaut quand
l'appelant n'en donne pas.

### T04 — Tests des extensions [V]
Tous les bugs B01–B16 sont dans `extensions/`, non mesurée par la couverture. Proposer un petit harnais
(`FakeSlashContext`, `FakeMessage`, réactions factices) et des tests par handler sensible (polls, giveaway,
reminders, admin, moderation), puis inclure `extensions` dans `[tool.coverage.run] source` avec un seuil propre.

### T05 à T09
- T05 : 267/270 `logger.error` sans `exc_info` → ni trace ni Sentry ; utiliser `logger.exception` dans les
  `except` inattendus (garder `error` simple pour les échecs attendus).
- T06 : exécuter la validation de `scripts/check_config.py` au démarrage (avertissements, pas d'arrêt).
- T07 : remplacer `pytz` par `zoneinfo`, stocker les échéances en UTC aware, convertir à l'affichage.
- T08 : factoriser `_repo()` (un `RepoCache[T]` générique) et reconnaître les messages du bot par référence en
  base (`bot_messages`, déjà utilisé pour les messages persistants) plutôt que par couleur.
- T09 : CSP stricte après retrait des handlers inline de la SPA.

Points forts constatés (à préserver) : config atomique sous verrou, validation Pydantic `extra=forbid`,
secrets masqués + `sent_to`, `fetch_untrusted()` anti-SSRF, sessions hachées, contrôle d'admin de guilde en
direct, rate limiting, arrêt propre SIGTERM, heartbeat + watchdog + healthcheck, image non-root épinglée par
digest, Actions épinglées par SHA, pip-audit hebdomadaire, détection de secrets en pre-commit.

### F01 à F05 — Améliorations fonctionnelles
- F01 : `/giveaway end|cancel|reroll` et `/editpoll` demandent un ID de message à copier : autocomplétion sur
  les giveaways/sondages actifs du serveur, ou commandes de menu contextuel « Clore le giveaway » sur le message.
- F02 : bouton « Participer » (`$addToSet` atomique, compteur exact, pas de relecture des réactions), bouton
  « Se retirer » ; corrige B10 au passage.
- F03 : `/reminder list`, désinscription des destinataires (S03), ne pas empiler « ⏰ (snooze) ».
- F04 : `/aide` générée depuis les commandes chargées pour le serveur (les scopes sont connus), cooldowns sur
  les commandes globales et lourdes.
- F05 : `name_localizations`/`description_localizations` (fr par défaut, en) pour les commandes installables
  par tous.

## 4. Questions ouvertes
1. Les destinataires de `/reminder set` doivent-ils pouvoir être n'importe qui, ou seulement des membres du
   serveur qui acceptent (S03) ?
2. Le rôle du bot a-t-il Administrateur ou « Mentionner @everyone » sur vos serveurs (gravité de S01/S02) ?
3. Le bot est-il destiné à d'autres communautés que les vôtres (priorité de F05, des quotas, des cooldowns) ?

## 5. Plan d'action par lots

**Lot 1 — Quick wins (≈ 1 j, S)** : B01, B03, B04, B05, B08, B09, B13, B16, B17, B18, S01 (random_ + RSS texte),
S05, S06, S07, S08, S10.
**Lot 2 — Concurrence et fiabilité des tâches (≈ 2 j, M)** : T01 garde anti-chevauchement, B02 réclamations
atomiques (giveaway, reminders, polls, tricount), B06, B07, B11, B12, B15.
**Lot 3 — Abus et durcissement (≈ 1–2 j)** : S02, S03 (+F03), S04 (formatter restreint), S09, S12, T03 helper
d'envoi sûr, B10.
**Lot 4 — Socle et tests (≈ 3–5 j, L)** : T04 harnais de tests d'extensions + couverture, T05, T06, T07, T08, B19.
**Lot 5 — Évolutions (à la demande)** : T02 migration PyMongo async, F01, F02, F04, F05, T09 CSP.
