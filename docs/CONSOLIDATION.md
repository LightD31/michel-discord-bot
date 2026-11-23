# Consolidation round (P27–P42)

What P1–P26 left half-done, duplicated or dead, and what the docs and guards
say that the code no longer does. Since the first draft, four lots answer your
review: a Spotify vote switch that works, Twitch connected from the browser, a
module's settings copied from another server, and service accounts per server.

Each lot is one PR. The consolidation lots are ranked by value: the first ones
make the suite trustworthy and take out what misleads (switches that do
nothing, rules nothing checks). The new features are marked **new**; they add
code and can be picked in any order.

Line counts are rough: `−` is what goes, `+` what comes. "Risk" is the chance
of breaking something a member or an admin would notice. No lot changes what
members see, except where it says so.

Measured on `master` at `1c1ecc1` (P26):

- `uv run pytest` (with Chromium): **2726 passed, 164 skipped in 84 s, but exit
  code 1** (see P27). Without Chromium: 2684 passed in 38 s.
- Coverage without MongoDB: **71 %** overall (floor 69), **82 %** for `src` +
  `features` (CI floor 81). That second margin is thin: each lot below either
  removes untested code or keeps its tests, and none lowers a floor.
- Every non-browser test file passes run on its own (247 files, in parallel),
  so the suite has no order dependence. The one P10 reported
  (`test_webui_hardening.py`) is fixed.

| Lot | What | − | + | Risk |
|---|---|---|---|---|
| P27 | Tests: no leaked event loop, one SPA harness, faster guards | 140 | 70 | low |
| P28 | Settings that do nothing: the Spotify vote switch wired, dead settings, a dead route, dead code and CSS | 300 | 40 | low (votes: member-visible) |
| P29 | **new** Twitch connected from the browser | 40 | 230 | medium |
| P30 | Finish the `guild_id_of()` migration, and guard it | 120 | 50 | low |
| P31 | Dashboard: one answer for a database outage | 110 | 30 | low |
| P32 | Repositories: one base class, one error style | 220 | 45 | low |
| P33 | Helpers written twice | 130 | 45 | low |
| P34 | Config: keys renamed, derived links, Zevent read live, compatibility shims gone | 70 | 40 | medium |
| P35 | **new** Copy a module's settings from another server | 0 | 180 | medium |
| P36 | **new** Spotify: each server connects its own account | 80 | 250 | high |
| P37 | **new** The other service accounts per server | 70 | 150 | medium |
| P38 | Docs and rules that match the code | 260 | 70 | none |
| P39 | Test fakes in one place | 550 | 130 | low |
| P40 | Components V2 for the last persistent cards | 20 | 40 | medium |
| P41 | The server page's cards on one loader | 160 | 60 | low |
| P42 | **new** Features without a dashboard UI (Uptime first) | 60 | 260 | medium |
| | **Consolidation lots** (P27, P28, P30–P34, P38–P41) | **≈ 2080** | **≈ 620** | net ≈ −1460 |
| | **New features** (P29, P35–P37, P42) | ≈ 250 | ≈ 1070 | net ≈ +820 |

---

## P27 — Tests: no leaked event loop, one SPA harness, faster guards

**1. A full local run fails, and the failing test is random.**
`tests/test_spa_pickers.py:96`, fixture `dashboard`, starts a bot loop with
`asyncio.new_event_loop()` on a thread and at teardown only calls
`loop.call_soon_threadsafe(loop.stop)`. It never calls `close()`. With
`filterwarnings = ["error"]` (`pyproject.toml`), each of the 11 leaked loops
becomes a `ResourceWarning` raised when the garbage collector meets it:

- `uv run pytest` with `PLAYWRIGHT_CHROMIUM_PATH` set: 2726 passed, then
  `ExceptionGroup: multiple unraisable exception warnings` at session end, exit 1.
- With `-x`: an unrelated test fails where the GC happened to run
  (`test_webui_public_routes.py::test_link_endpoints_require_guild_admin` here).

I traced it with a plugin recording every loop that was garbage-collected
while still open: all 11 come from `test_spa_pickers.py:96`. CI never sees it
because the `browser` job runs `-m browser` alone, and the main job has no
Chromium. Run either half alone and it's clean. Only a developer running the
whole suite, as `CLAUDE.md` suggests, gets the red run.
`tools/dashboard_demo.py:880` has the same open loop in `DemoServer.stop()`.

**2. The browser harness is written twice.** `free_port()`, `browser()` and the
uvicorn-on-a-thread runner exist in both `test_spa_smoke.py:50-131` and
`test_spa_pickers.py:49-121`. `test_spa_save_bar.py` and
`test_spa_server_page.py` borrow fixtures by importing another test module
(`import test_spa_smoke as smoke`), and `test_spa_pickers.py:21` imports
`script_error` the same way. `browser` is module-scoped, so Chromium is
launched four times.

**3. Fixed waits.** `page.wait_for_timeout(200|300|500|800)`
(`test_spa_pickers.py:254,280`, `test_spa_smoke.py:503,823`): 1.8 s of sleep,
and a flake on a slow runner.

**4. Guards re-parse the whole tree.** `tests/source_scan.py:121` `parse()` has no
cache. About fifteen guards each re-read ~500 files: `test_module_registry`
takes 4.6 s, `test_look` 1.9 s, `test_no_hardcoded_urls` 1.7 s and so on.
`test_incidents.py:183` and `test_task_start_guard.py:46` walk
`extensions/` with their own `rglob` instead of `source_scan.python_files()`,
which `testing.md` says every guard uses.

**Fix.** `tests/spa_harness.py` holds `free_port`, a session-scoped `browser`,
`serve(app, bot_loop)` (stops and **closes** the loop) and `script_error`. The
four SPA files take their fixtures from it. Replace the fixed waits with
`expect(...)`. Add `functools.cache` on `parse()` (the trees are read-only).
Put the two stray scans on `python_files()`, and close the demo's loop.

**Lines** −140 / +70. **Risk** low: tests only, plus one line in the demo.

---

## P28 — Settings that do nothing

**The Spotify vote switch, wired.** `moduleSpotify.voteEnabled` "Votes activés"
(`extensions/spotify/_common.py:62`, default off) is read nowhere. The vote
features all run on every Spotify server whatever it says:

- the daily vote (`randomvote`, `votes.py:80`)
- its reminders (`reminder_check`, `votes.py:475`) and `/musique activer` /
  `/musique retirer`
- the add-with-vote of `/musique proposer` (`votes.py:597`) and its hourly
  close (`check_for_end`, `votes.py:826`)

When the switch is off, none of these run: the tasks skip the server, and the
three commands answer with a `module_off()`-style refusal that names the switch
("🗳️ Les votes sont désactivés sur ce serveur : un administrateur peut les
activer dans le tableau de bord").

**Member-visible**, which is the point. **Deploy:** set
`servers.<id>.moduleSpotify.voteEnabled: true` on every server that uses the
votes today. The default stays off, as the dashboard already shows it, so a
server where it is unset stops voting until it is switched on.

Settings an admin can change that nothing reads (searched for every field of
every registered schema; history checked back to the import, `18663a6`):

| Setting | Evidence | Fix |
|---|---|---|
| `discord.devGuildChannelId` (`src/webui/schemas.py:634`) | Read nowhere. P24 still built a picker for it (`test_spa_pickers.py:356`) | remove the field, the picker case and its test |
| `discord.botId` (`schemas.py:626`) | Read nowhere (`ownerId` is read, by the Confrérie) | remove |
| global section `random`, `randomOrgApiKey` (`schemas.py:921`) | Read nowhere. `extensions/random_.py` uses Python's `random`, though `README.md:48` says "powered by Random.org" and `RUNBOOK.md:161` asks for the key | remove the section, fix both docs |

Code nothing calls:

- `POST /api/reload/{ext}` (`src/webui/routes/extensions.py:225`) and its SPA
  function `reloadExtension` (`js/pages/extensions.js:117`): no button calls
  them, so they're the only route and the only exported JS function unused.
  Remove both, keeping the `run_extension_op` tests that go through
  `/api/reload`. The one reason to reload a single extension, Zevent's global
  settings, goes away in P34.
- Functions with no caller (an AST pass over every name, checked by hand):
  `MdiApi._persist_match` (`extensions/mdi/api.py:195`, 27 lines),
  `_scoreboard` (`mdi/embeds.py:366`, 18), `get_total_viewers_from_twitch`
  (`zevent/streams.py:114`, 21), `_fetch_all_medallists` / `_fetch_event_medals`
  (`olympics/tasks.py:134,143`), `session_settings`
  (`secretsanta/_common.py:60`), `first_url` (`features/confrerie/drafts.py:146`),
  `involves_team_id` (`features/mdi/client.py:196`), `MongoManager.get_collection`
  (`src/core/db.py:195`), `get_logger` (`src/core/logging.py:193`). About 100 lines.
- `src/discord_ext/embeds.py`: a 14-line module holding one constant
  (`SPACER_FIELD`, for VLR.gg and ZUnivers). Move it into `look.py` and drop
  the module and its line in `test_layering.py`.
- CSS: 9 of 596 selectors are used nowhere: `.config-editor`, `.config-field*`,
  `.log-stats`, `.nav-item-sub`, `.schema-badge` / `.no-schema` (`app.css:197,
  473-490, 1171-1180, 1335`). About 60 lines.

**Config changes** (one deployment, no migration): `voteEnabled: true` where
the votes are wanted. Drop `config.discord.botId`,
`config.discord.devGuildChannelId` and `config.random`. Regenerate
`config.example.json`.

**Lines** −300 / +40. **Risk** low; the votes change is deliberate. Coverage
goes up, since the removed code is mostly untested.

---

## P29 — new: Twitch connected from the browser

**Today the bot can't connect to Twitch by itself.** `extensions/twitch/eventsub.py:114`
and `extensions/streamlabs.py:156` call twitchAPI's
`UserAuthenticationStorageHelper(storage_path="./data/twitchcreds.json").bind()`.
When that file is missing or its refresh token is dead, `bind()` falls back to
`UserAuthenticator.authenticate()`. That starts a login server on the host's
`localhost:17563` and asks the host to open a browser. In the read-only
container nobody can reach it, so EventSub never starts, and Streamlabs' live
status stays off, until someone copies a token file in by hand.
(Zevent only uses the app token, `Twitch(id, secret)`, and isn't affected.)

**Fix**, built like Spotify's flow (`src/webui/routes/spotify.py`):

- In the global settings, the Twitch section gets a panel with the state
  (connected as whom, scopes, refresh ok or not) and a "🔌 Connecter Twitch"
  button. Developers only, like Spotify's.
- `GET /twitch/auth/start` redirects to Twitch's authorize page with a CSRF
  state cookie. The scope is `user:read:subscriptions`, as now.
  `GET /twitch/auth/callback` checks the state, exchanges the code, and writes
  `{token, refresh}` to `data/twitchcreds.json`, the format `bind()` already
  reads. It then reloads `twitch` and `streamlabs` on the bot loop
  (`run_extension_op`), and records `twitch.connect` in the audit log.
- Twitch's URLs come from twitchAPI's own constants, not literals
  (`test_no_hardcoded_urls`). The callback URL is built from `webui.baseUrl`,
  as Spotify's is. Twitch's developer console must list
  `{webui.baseUrl}/twitch/auth/callback`.
- Both `bind()` calls pass an `auth_generator_func` that raises
  `TwitchNotConnected` instead of opening a browser. The extension then logs
  one line, and the health card says "Twitch n'est pas connecté : un
  développeur peut le connecter depuis les réglages globaux".
- The state cookie, callback URL and "back to the dashboard" page now exist
  twice (Spotify, Twitch), so they move to `src/webui/oauth_flow.py`, shared
  by both. That is the −40.

Per server or for the whole bot: see the table in P37. My recommendation is to
keep Twitch bot-wide. Its token only reads public live status. It is tied to
one EventSub websocket, and Twitch caps subscriptions per token. A token per
server would mean a websocket per server.

**Lines** −40 / +230 (tests extra). **Risk** medium: an OAuth flow, so the
state check, the developer tier and the token file mode (`0600`) get their
own tests. No member-visible change, except that live notifications come back
once it is connected.

---

## P30 — Finish the `guild_id_of()` migration, and guard it

P12 added `guild_id_of` / `guild_of` / `member_of` / `message_of`, and
`CLAUDE.md` says to use them, "enforced" by `tests/test_command_context.py`. That
file only unit-tests the four helpers: nothing checks that they're used, and
the migration stopped about halfway.

- `require_guild()` (`src/discord_ext/messages.py:144`) is called **37 times**
  (birthday, feur, giveaway ×6…). Every caller is a guild-scoped command, which
  Discord never runs in a DM, so the branch is dead. In `birthday.py:243,292,355`
  it is written `if not await require_guild(ctx) or ctx.guild is None`, only
  to narrow the type for mypy.
- Raw reads in `extensions/`: **84 `ctx.guild_id`** (giveaway 13, suggestions
  10, confrerie/browse 7…) and **64 `ctx.guild`**, against 60 `guild_id_of(` and
  44 `guild_of(`. 18 `if not ctx.guild` / `ctx.guild_id is None` checks guard
  guild-only callbacks (`admin/__init__.py:341,390`, `twitch/__init__.py:341,369`,
  `giveaway/__init__.py:699,712,735`…).

**Fix.** Delete `require_guild` and the dead checks. Use the helpers. Add a
source-scan guard (in `test_command_context.py`, so the rule's pointer becomes
true) that fails on `ctx.guild_id` / `ctx.guild` in `extensions/`. The
allowlist names the callbacks that do run in a DM: `/mes-donnees`, `random_`,
the Secret Santa DM buttons.

**Lines** −120 / +50. **Risk** low. The risky part is a component callback that
can fire in a DM; the allowlist names each one and keeps its check. No
member-visible change.

---

## P31 — Dashboard: one answer for a database outage

- **17** route handlers catch `DatabaseError` by hand (`routes/moderation.py:123,
  141,164`, `public.py:241,260,278`, `secretsanta.py:152,168,215`, `gazette.py:36,62`,
  `achievements.py:47`, `xp.py:103`, `spotify.py:95`, `health.py:70`, `audit.py:31`,
  `members.py:99`).
- The message is defined three times: `DB_UNAVAILABLE` in `moderation.py:29`,
  `gazette.py:30` and `secretsanta.py:45`. Two other wordings ("Statistiques
  momentanément indisponibles.", "Lecture des statistiques Spotify impossible.")
  are used elsewhere.
- The status differs for the same outage: Spotify answers **500**, others 503.
  `ctx.call_bot` (`src/webui/context.py`) turns a `DatabaseError` raised on the
  bot loop into a **500** "Erreur interne", so the agenda or the Gazette preview
  answer 500 during an outage where the achievements card answers 503.
- Six more blocks catch `Exception` only to log it and raise the same French
  500 that `call_bot` already raises.

**Fix.** Register two exception handlers in `src/webui/app.py`:
`DatabaseError` → 503 with one French message (logged as a warning with the
route), and `Exception` → the generic French 500. `call_bot` lets
`DatabaseError` through. The 23 blocks go.

**Lines** −110 / +30. **Risk** low. Visible change: during an outage an admin
reads the same sentence on every card, and the Spotify stats answer 503. The
public pages keep their own wording if you want; I'd unify it.

---

## P32 — Repositories: one base class, one error style

- All 31 `features/*/repository.py` repeat `__init__(self, guild_id)` and
  `_col()` → `mongo_manager.get_guild_collection(self._guild_id, COLLECTION)`.
- 30 of them translate errors with `@translates_db_errors`. **Giveaway doesn't**
  (`features/giveaway/repository.py`): it predates the decorator, has 7
  `except Exception` / 6 `raise DatabaseError(f"…{e}")` (driver text in the
  message), and leaves several reads untranslated (`get_by_message`, l. 57).
- `ensure_indexes()` wraps `create_index` in a `try` / `log_failure` in about 10
  repositories, each spelled differently.
- `architecture.md` points at `features/moderation/repository.py` as the example
  of raising `DatabaseError` by hand, but moderation now uses the decorator too
  (0 `raise DatabaseError`).

**Fix.** Put a `GuildRepository` in `src/core/db.py` (holds the guild id,
`_col(name=COLLECTION)`, and `create_indexes(specs)` that logs a failure once).
Move the repositories onto it, and giveaway onto the decorator. The docs
example then points at the decorator only.

**Lines** −220 / +45. **Risk** low. The `mongo` tests in CI cover every
repository; the local run without MongoDB only loses lines both sides execute.

---

## P33 — Helpers written twice

| Twice | Where | Into |
|---|---|---|
| A duration parser | `features/moderation/duration.py:23` and `features/polls/tally.py:90` (giveaway borrows the polls one). Different grammars: polls takes `3j` and spaces, moderation doesn't | one parser in `src/core/text.py` taking both forms (moderation then also accepts `3j`, the one visible change) |
| Avatar download | `_fetch_avatar_bytes`, `extensions/xp/commands.py` and `extensions/welcome/__init__.py` (15 lines each) | `src/discord_ext` |
| Circular crop and font path | `_circular_avatar` in `features/xp/card.py:26` and `features/welcome/card.py:23`; `_FONT_PATH` ×3 (with `tricount/charts.py:10`) | `src/core/images.py` |
| RSS poll interval | `_poll_minutes`, `extensions/rss/__init__.py:194` and `src/webui/guild_health.py:138` | `features/rss/scheduling.py` |
| Euros | `format_euros`, `extensions/zevent/embeds.py:55` and `features/zevent/history.py:234` | the feature |
| Big numbers | `format_number`, `features/minecraft/stats_sftp.py:340` and `src/core/text.py:41` (different rounding) | one, with a precision argument |
| One-line wrappers | `get_guild_settings` in `moderation`, `suggestions` and `starboard/_common.py` (`return MODULE.get(gid) or None`) | inline `MODULE.get()` |
| A hardcore season, two formats | `features/coloc/models.py:246` and `extensions/zunivers/embeds.py:132` both accept `beginDate` and `begin_date` | read once through `HardcoreSeason.from_dict` |

**Lines** −130 / +45. **Risk** low.

---

## P34 — Config: keys renamed, derived links, Zevent read live, compatibility shims gone

Out of this lot since your review: moving settings to global sections (the
rule that asked for it is dropped, see P38 and P37), and the ZUnivers journa
links, which stay typed URLs because they point into a server the bot isn't in.

| Setting | Why | Becomes |
|---|---|---|
| `moduleSpotify.spotifyPlaylistUrl`, `spotifyNewPlaylistUrl` | `open.spotify.com/playlist/{spotifyPlaylistId}` is derivable, and `open.spotify.com` is already in `DERIVED_HOSTS` | derived; two fields go |
| `moduleRss.ChannelId`, `moduleYoutube.ChannelId` | the only keys without the module prefix, with a capital C | `rssChannelId`, `youtubeChannelId` |

**Zevent reads its global section when the extension loads**
(`extensions/zevent/_common.py:304`: `_cfg` and 20 constants). It's the only
global section read that way; `config.md` says to read them at use time. The
note "reload it after a change" leads nowhere, because the dashboard has no
per-extension reload (see P28). Read it through an accessor at use time.

**Compatibility shims**, against "no backward compatibility, rename directly":

- `LEGACY_DEFAULT_PERSONAS` (`features/ai/prompt.py:50`, `settings.py:83`): reset
  the stored copies in `config.json`, then drop the tuple.
- An AI model's key is stored as `provider` "for backward compatibility"
  (`features/ai/models.py:21,82`): rename the key in `config.json`.
- `$ifNull: ["$source", "message"]` for XP events from before `source`
  existed (`features/xp/repository.py:16`): one `update_many` in the PR notes,
  then drop the expression.

`spotifyDashboardUrl` overrides the built-in public page per server; keep it
or drop it, **your call**.

**Config and database changes** are listed in the PR. **Lines** −70 / +40.
**Risk** medium: the PR's list of `config.json` and database edits must be
applied at deploy.

---

## P35 — new: Copy a module's settings from another server

An admin of several servers sets a module up once and copies it to the others.
It matters most once accounts are per server (P36, P37): the same Spotify app,
AI key or Notion integration doesn't have to be typed again.

- On a module page: "📋 Copier depuis un autre serveur", with a list of the
  servers where the editor is an admin, live (`require_guild_admin` on both),
  that have settings for this module.
- `POST /api/servers/{id}/modules/{module}/copy-from` with `{source}` reads the
  source's settings **on the server**, so a secret never reaches the browser.
- What names one guild's objects is left out: `channel`, `role`, `member` and
  `forum` fields, the lists of them, and the per-entry keys of
  `NESTED_CHANNEL_KEYS` / `NESTED_ROLE_KEYS`. The form then shows them empty,
  to pick here.
- The rest goes through `save_module_settings()`, the path every save takes:
  schema, size, channel and role checks, the history, and the audit as
  `module.copy`, naming the source by its name.
- A secret is copied together with the fields its `sent_to` names, so the
  "a placeholder doesn't follow a new host" rule still holds.
- Account tokens (P29's Twitch, P36's Spotify) are not copied: the admin
  connects once more, one click.

**Decision for you: who may copy secrets out of a server.** Today a
`MANAGE_GUILD` co-admin of server A can't read A's secrets; with a copy, they
could use them on a server B they run. My recommendation: copying a module
that holds a secret requires the source's owner or `ADMINISTRATOR` there, read
live, as the Secret Santa reveal does. Anything else copies without its
secrets.

**Lines** +180 (tests extra). **Risk** medium: a new path that writes config,
so a `mongo` test and the guild-boundary tests cover it.

---

## P36 — new: Spotify, each server connects its own account

**Today one Spotify account serves every server.** The app's credentials are a
global section (`spotify.spotifyClientId`, `spotifyClientSecret`,
`spotifyRedirectUri`). The account token is one file (`data/.cache`,
`src/integrations/spotify.py:35`), refreshed by a developer from the global
settings (`routes/spotify.py:102`). So every server's playlist reads, adds,
removals and duplicate cleanup go through your account: one `spotipy` client
(`_LazySpotifyClient`) behind one worker thread, 27 call sites in
`extensions/spotify/`.

**Fix:**

- The app's credentials move into `moduleSpotify` as secret fields. A server
  can bring its own Spotify app, which also sidesteps Spotify's limit of 25
  allowed users for an app in development mode. With P35, you copy yours to
  your own servers.
- The account connection is per server: "🔌 Connecter Spotify" on the module
  page, for that server's admins (`require_guild_admin`). Its state cookie
  names the guild, and the shared `oauth_flow.py` from P29 runs it.
- The token is kept in `data/spotify/<guild>.json` (`0600`), one cache file per
  guild.
- `spotify_api` becomes `spotify_for(guild_id)`: one client per guild, built
  lazily, all still on the single worker thread (ordered calls, race-free
  refreshes, as `async-io.md` asks). A server with no connection gets
  `module_off()`-style refusals and its tasks skip it, with a health-card line.
- The developer-only token panel goes; the stats route stays.

**Deploy:** enter the app's credentials for each server that uses Spotify
(or copy them with P35), then connect once per server. Until then, that
server's Spotify features are paused.

**Lines** −80 / +250. **Risk** high: the client refactor touches every
Spotify command and task. `test_spotify_async.py` and the vote tests carry
over; per-guild isolation gets its own test.

---

## P37 — new: The other service accounts per server

The rule "a third-party API or site the whole bot shares goes in a global
section named after the service" (`config.md`) is dropped. In its place: **an
account or key a service gives (credentials, a token, a paid quota) belongs to
a server, never to a global section, so no server spends or reads another's
account.** Global sections keep the bot's own infrastructure (`discord`,
`mongodb`, `webui`, `backup`, `metrics`, `errorReporting`) and public endpoints
that need no account (`zunivers`, `raiderio`, `olympics`, `vlrgg`, `zevent`);
nothing moves there.

Each global section holding an account, what a server gets from it today, and
the proposal:

| Service (global today) | What every server uses of it | Proposal |
|---|---|---|
| Spotify | your account | P36 |
| AI, `OpenRouter.openrouterApiKey`, `NanoGPT.apiKey` | `/ask`, mentions, `/rattrapage` and the Gazette intro spend your credits, within the daily limits of `AI` | keys per server in `moduleIA`. The kill switch `AI.paused` and the model list stay global |
| Notion, `notion.notionSecret` | the Confrérie reads and writes through your integration | per server in `moduleConfrerie` |
| Uptime Kuma, `uptimeKuma.*` | any server with Uptime on lists and alerts on **your** monitors | per server in `moduleUptime` (URL + key, `sent_to` the URL) |
| Shlink, `shlink.*` | every server's links are shortened on your instance and domain | per server in a `moduleShlink`; unset = links posted as they are |
| YouTube, `youtube.youtubeApiKey` | every server's channel checks spend your API quota | per server in `moduleYoutube` |
| Twitch, `twitch.*` and P29's token | reads public live status only | **stays bot-wide** (see P29). Your call |

Every new per-server secret declares its `sent_to` (`test_module_registry.py`
already requires it). The dashboard's "Vérifier la connexion" (`CHECKS`) reads
the server's own settings, as it does for LLDAP.

**Decision for you:** which rows to take. All of them except Twitch is my
recommendation. They can also come one PR at a time.

**Lines** −70 / +150 (all six rows). **Risk** medium: each service needs its
settings entered (or copied) per server at deploy. The PR lists them.

---

## P38 — Docs and rules that match the code

`CLAUDE.md` names a guard for rules that none checks:

- "`guild_id_of` … — `tests/test_command_context.py`": unit tests of the helpers
  only. P30 adds the guard.
- "Edit through `edit_message_if_changed()` — `tests/test_message_edit.py`":
  tests of the function, nothing scans for a direct `Message.edit`. One is left,
  `extensions/confrerie/review.py:407`. Move it, and add a short guard
  (`.edit(` on a fetched message outside `messages.py`).
- "Mentions" (`test_mention_policy.py`), "`fetch_untrusted()`"
  (`test_core_http_untrusted.py`): tests of the helpers, which can't be checked
  by scanning. Point those rules at their convention file instead.

`docs/conventions/` paragraphs that no longer match:

- `config.md`: delete "A third-party API or site the whole bot shares goes in a
  global section named after the service…". Its replacement comes with P37, or
  here if P37 isn't taken.
- `architecture.md`: "~38k lines" (71.5k now), "39 entries" (46), and the
  moderation example of P32.
- `extension-lifecycle.md`: "Six exist" (right-click menus). There are seven,
  since 📊 Profil.
- `dashboard-frontend.md`, "Tester" buttons: "a module gets one … LLDAP today".
  `CHECKS` (`routes/checks.py:178`) holds five, as `features.md` (P16) says,
  so the two paragraphs disagree.
- `testing.md`: a list of what "real tests exist" for from before the P-lots.
  Coverage "over `src`, `features`, `extensions` and `scripts`" against
  `CLAUDE.md`'s "`src, features, extensions`".
- `README.md` of the conventions: 100 of its 117 lines map each paragraph of
  the pre-P12 `CLAUDE.md` to its new file. That's history; keep the index. Every
  file still opens with "Moved verbatim from `CLAUDE.md` (P12, D5)", which
  stopped being true when they were edited.
- `features.md` is ordered by lot ("Round 8 Discord follow-ups (P14)", "P15",
  "P16", "P25 follow-ups"). Fold each follow-up into its feature's paragraph,
  and move the dashboard notes to `dashboard-*.md`.
- 248 references to idea codes, `(P22, B11)`, `(C9)`, in comments and
  docstrings point at the deleted `docs/IDEAS.md`. Drop the letter codes and
  keep the P-numbers, which name PRs.

**Lines** −260 / +70 (docs; the guard is about +25). **Risk** none.

---

## P39 — Test fakes in one place

`tests/discord_fakes.py` exists (`FakeCtx`, `FakeGuild`, `FakeMember`,
`FakeChannel`, `FakeBot`, `run()`, `bare_extension()`), yet test files carry
**1,752 lines** of their own `Fake*` classes:

- `FakeCtx` in 16 files (280 lines), `FakeRepo` in 10 (202), `FakeChannel` in 10
  (141), `FakeClient` in 8, `FakeBot` in 8. Examples: `test_admin_channel_rights.py`,
  `test_admin_delete.py`, `test_guild_messages.py`, `test_confrerie_review.py`,
  `test_reactionroles_permissions.py`, `test_zevent_*.py`.
- A fake `WebUIContext` (the `require_*` tiers, `mutate_config`, `call_bot`) is
  rebuilt in about 8 dashboard tests: `test_webui_audit.py:47`,
  `test_mongo_member_space.py:33`, `test_mongo_config_history.py`,
  `test_webui_onboarding.py`, `test_webui_guild_boundary.py`…
- Files named after the lot rather than the subject (`test_small_cleanups.py`:
  "Phase 5b cleanups", the staff check and the RSS presets). Fold them into
  their topic's file.

**Fix.** Extend `discord_fakes.py` with what the copies add. Add
`tests/webui_fakes.py` with a `FakeWebUI(tier=…, config=…)`. Move tests onto
them, file by file; a test whose fake does something special keeps a
subclass.

**Lines** −550 / +130. **Risk** low (tests only), but it touches about 40
files. Easiest to review commit by commit, by area.

---

## P40 — Components V2 for the last persistent cards

P22 and P26 moved six slots: the agenda, Satisfactory, Minecraft, Streamlabs,
the Zevent and the XP leaderboard. These persistent cards still post embeds:

- Twitch planning, `extensions/twitch/schedule.py:49`
- the MDI schedule, `extensions/mdi/notifications.py:336`
- VLR.gg's team cards, `extensions/vlrgg/notifications.py:68`
- the Confrérie recap, `extensions/confrerie/__init__.py:53`
- Speedons (archived; a `CustomPaginator`, which has `.v2` since P26),
  `extensions/speedons.py:226,235`

The Spotify recap is plain text and stays. Replies, the Embed manager
(admin-designed, `custom()`) and the paginated replies stay on embeds, as P26
decided.

**Fix.** Use `embeds_to_v2()` and `v2=True` on each slot. Once they're all V2,
make `v2=True` the default of `fetch_or_create_persistent_message` /
`GuildMessages`, with the Embed manager the one `v2=False`.

**Lines** −20 / +40. **Risk** medium, and **member-visible by design**: the
cards look like the other V2 ones, and each message is reposted once (Discord
can't turn a message into V2 by an edit). Check each on a live server first,
as P22 advised: MDI's per-match fields and VLR's images.

---

## P41 — The server page's cards on one loader

`js/pages/incidents.js`, `audit.js`, `bot-messages.js`, `health.js`, `agenda.js`,
`overview.js`, `js/features/achievements.js` and `gazette.js` each hand-roll the
same card: header markup, spinner, `try { api(...) } catch`, then an empty or
unavailable state. The states differ for no reason: the class is `me-empty` in
one card and `u-c-text-muted` in another; "Liste indisponible." here, "… sont
indisponibles pour le moment." there. The spinner markup appears 31 times.

**Fix.** Add `js/ui/card.js`: `cardHtml({ id, title, subtitle, tools })` and
`loadCard(el, url, render, { empty, unavailable })`, and move the eight cards
onto it.

**Lines** −160 / +60. **Risk** low; before/after screenshots with
`tools/dashboard_demo.py --shots`. The empty and unavailable texts become one
style (admin-visible, minor).

---

## P42 — new: Features without a dashboard UI

This lot adds code; it's last, and optional.

- **Uptime alerts** (the clear case). They're set only through `/uptime
  configurer | supprimer | liste` (`extensions/uptime/commands.py`), and stored
  as a *config* document in MongoDB (`features/uptime/repository.py`:
  `uptime_monitors`, `_id: "config"`, with the monitor's `last_status` mixed in).
  The module's dashboard page has nothing but its switch. Move the alerts to
  `moduleUptime.alerts`: a list widget with a monitor picker (a route listing
  the server's Uptime Kuma monitors, per P37), a `channel` field and a
  `choice` for the mode. That puts them in `config.json`, checked by the save
  route, audited and in the history. `last_status` stays in MongoDB.
- **Secret Santa exclusions**: only `/secretsanta exclure-paire |
  autoriser-paire | paires-exclues`. The dashboard card draws and reveals but
  doesn't show them.
- **Running giveaways**: `/concours liste | annuler | tirer` only. The agenda
  shows when they end, nothing more.

**Lines** −60 / +260 (Uptime alone about +180). **Risk** medium (a new widget,
a picker, a config move).

---

## Areas checked and fine

- **Layers**: `test_layering.py` is clean, and every module under `src/` and
  `features/` is imported by something.
- **Incidents per guild**: P24 finished what P21 left (every `Task` loop binds
  its guild).
- **`ok()` / `oops()`**: the literal ✅ / ❌ left are titles and status labels
  (`"✅ Groupe créé"`, `"❌ Révoqué"`), which is fine.
- **The SPA's markup**: P17 finished the move to `html`, the inline styles and
  the form split. The routes the SPA calls all exist; the only uncalled one is
  in P28. Actions: every registered one is used.
- **Tests**: every test asserts something. The skips are MongoDB (163) and
  Chromium (2) only. No order dependence. Timing-based tests
  (`test_debounce.py`, `test_observability.py`, `test_core_config_mutate.py`)
  sleep 10–200 ms and passed in every run here; they aren't worth a rewrite yet.
- **Not worth the churn**: the eleven spellings of "pin this message"
  (`xpPinMessage`, `pinSchedule`, `agendaPin`…), which mean renaming eleven
  config keys for nothing a reader gains; and the mix of `asyncio.run()` (31
  files) and `async def` tests, which is harmless.
