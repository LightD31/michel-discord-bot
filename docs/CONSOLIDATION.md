# Consolidation round (P43–P62)

P27–P42 are merged (the plan they followed is in this file's history, up to
`ab9f4c3`). This round has two halves:

- **Putting more on the dashboard** (your review: "emphasize on putting more
  things on the WebUI"). Ten lots, P46–P55, bring to the Web UI what only a
  slash command does today. On the admin side: the suggestions board, the
  whole Secret Santa session, moderation sanctions, giveaway re-draws, RSS
  feeds, LLDAP, backups, and the modules whose page is only their settings. On
  the members' side, « Mon espace » gains what is theirs in five more modules.
- **Consolidation**, P43–P45 and P56–P62:
  - what P27–P42 left, and what an earlier round missed: a copy of the
    Secret Santa draw that nothing deletes, ids still typed into slash options,
    and the one extension that listens to every button;
  - a few modules with almost no tests.

Each lot is one PR, based on `master`. P43–P45 come first:

- **P43** is decided.
- **P44** changes the commands the dashboard lots sit next to.
- **P45** gives the module page the card registry every dashboard lot adds to.

The dashboard lots can then be picked in any order; they are ranked by how
much they take off Discord. The consolidation lots after them are ranked by
value.

Every dashboard lot follows the rules P2–P42 set:

- **Tiers**: the route checks its tier, and a route that makes the bot act
  applies the rule the user would face in Discord (`dashboard-security.md`).
- **Shared code**: it calls the same code as the command, moved to
  `src/discord_ext/` when the route needs it (as `run_draw` and the giveaway
  draw were).
- **Audit and tests**: it is audited, and tested with `FakeWebUI`.
- **Screenshots**: before/after, desktop and phone, with `tools/dashboard_demo.py`.

Line counts are rough: `−` is what goes, `+` what comes. "Risk" is the chance
of breaking something a member or an admin would notice. No lot changes what
members see in Discord, except where it says so.

Measured on `master` at `ab9f4c3` (P42c):

- `uv run pytest` with Chromium: **2840 passed, 167 skipped in 87 s**, exit 0.
  Without Chromium: 2798 passed in 55 s. The skips are MongoDB (166) and one
  `tools/dashboard_demo.py` test that needs it too.
- Coverage without MongoDB: **72 %** overall (floor 69), **83 %** for `src` +
  `features` (CI floor 81).
- The least-tested files are Discord-layer modules that parse an API's payload
  themselves: `extensions/olympics/medals.py` 9 %, `mdi/embeds.py` 11 %,
  `twitch/schedule.py` 16 %, `zevent/api.py` 18 %, `vlrgg/commands.py` 18 %,
  `spotify/playlist.py` 18 % (see P60).
- What the dashboard covers today, against the 115 slash commands:
  - **Admins**: settings for every module, plus action cards for role menus,
    moderation (list and revoke), Secret Santa (draw, reveal, exclusions), the
    Gazette, giveaways (draw, cancel), Uptime alerts, Spotify and short links.
  - **Members** (« Mon espace »): XP, reminders, birthday, Tricount balances,
    Secret Santa, badges and the agenda.
  - **Command-only**: the rest, listed lot by lot below.

| Lot | What | − | + | Risk |
|---|---|---|---|---|
| P43 | The Secret Santa draw file: no longer written *(decided)* | 45 | 10 | low |
| P44 | No more ids typed into slash options | 20 | 140 | low (admins: autocomplete) |
| P45 | The module page's cards in one registry (before the dashboard lots) | 170 | 70 | low |
| | **The dashboard** | | | |
| P46 | **new** Suggestions: the staff's board on the module page | 60 | 380 | low |
| P47 | **new** Secret Santa: create, change and cancel the session from its card | 40 | 320 | medium |
| P48 | **new** Moderation: sanction a member from the dashboard | 150 | 450 | high |
| P49 | **new** « Mon espace »: my ZUnivers and Spotify reminders, my suggestions, my giveaways | 0 | 380 | low |
| P50 | **new** « Mon espace »: Tricount expenses, not only balances | 30 | 380 | medium |
| P51 | **new** Module pages that show the module: birthdays, AI ranking and usage, polls, the Zevent recap | 0 | 420 | low |
| P52 | **new** Giveaways: recent draws and a new draw | 0 | 150 | low |
| P53 | **new** RSS: each feed's state, and its reset | 0 | 170 | low |
| P54 | **new** LLDAP: sync now, the linked accounts, and « Créer mon compte » | 0 | 260 | medium |
| P55 | **new** Backups on the global page | 0 | 220 | low |
| | **Consolidation** | | | |
| P56 | Spotify votes: their own button callbacks, split, tested | 150 | 260 | medium |
| P57 | Zevent's logo: a setting, not a file uploaded every 30 s | 10 | 20 | low |
| P58 | Helpers written twice, round two, and the last dead code | 230 | 70 | low |
| P59 | Test fakes, round two | 620 | 120 | low |
| P60 | The event trackers' parsing in `features/`, tested | 150 | 520 | low |
| P61 | README and command names in the docs, checked | 40 | 80 | none |
| P62 | *(optional)* Global sections' schemas next to their extension | 360 | 340 | medium |
| | **Dashboard lots** (P46–P55) | ≈ 280 | ≈ 3130 | net ≈ +2850 |
| | **Consolidation lots** (P43–P45, P56–P61) | **≈ 1435** | **≈ 1290** | net ≈ −145, coverage up |

P60 adds more than it removes, on purpose: it moves code into `features/` and
tests it. Without it, the consolidation lots remove about 1285 lines and add
about 770.

---

## P43 — The Secret Santa draw file: no longer written *(decided)*

**Finding.** Every draw writes a plain-text copy of who gives to whom, with each
member's display name and username, to
`data/secret_santa/draw_<context>_<date>.txt`
(`save_human_readable_draw`, `src/discord_ext/secretsanta.py:298`, called at
`:252`). Nothing ever deletes these files:

- `Privacy.md` §3 lists the draw results "stored unencrypted, readable by the
  Operator", meaning the MongoDB collection; the file isn't mentioned, and §7
  gives the draw no retention at all.
- `/mes-donnees supprimer` and the 30-day purge (`features/userdata/repository.py`)
  forget the MongoDB results, but not the file.
- The rule "Retention in `Privacy.md` §7 matches the code"
  (`data-and-retention.md`) doesn't hold.

The file adds nothing an admin needs: the results are in MongoDB, and the
server's administrators read them with `/secretsanta reveler` or the
dashboard's « Révéler » (P15), which records who looked.

**Fix (decided: drop it).** Delete `save_human_readable_draw` and `DATA_DIR`.
A test checks that a draw writes nothing under `data/`. The PR gives the
command that removes the files already on the volume
(`rm data/secret_santa/draw_*.txt`), for you to run once.

**Lines** −45 / +10. **Risk** low.

---

## P44 — No more ids typed into slash options

**Finding.** `discord-messages.md` (P7) says "a member never types a list, an
id or a long text into an option". Six options still ask for one:

| Command | Option | Where the id comes from |
|---|---|---|
| `/menu-roles modifier`, `/menu-roles supprimer` | `menu`: "ID du menu (visible via /menu-roles liste)" | `reactionroles/commands.py:361,396` |
| `/rss reinitialiser` | `flux`: "Identifiant interne du flux (visible via /rss liste)" | `rss/__init__.py:467` |
| `/tricount recurrente arreter` | `recurrence-id` | `tricount/recurring.py:227` |
| `/debannir` | `utilisateur-id`: a Discord user id | `moderation/commands.py:452` |
| `/infraction voir`, `/infraction retirer` | `id`: the case number | `moderation/commands.py:543,564` |

**Fix.** An autocomplete for each one, as `/concours tirer` has had since P7:

- role menus by title and channel;
- feeds by name;
- the member's recurring expenses by label and amount;
- the server's ban list by name (`guild.fetch_bans()`);
- the cases, newest first, by number, member and type.

The value stays the id, so pasting one still works. A guard in
`test_command_names.py`: an option whose description says "ID" or "identifiant"
is a user, role or channel option, or has an autocomplete.

`/effacer avant|apres` (a message id, `admin/__init__.py:142,149`) stays as it
is: no autocomplete lists messages. The guard names it as the one exception.

**Lines** −20 / +140. **Risk** low.

**Member-visible**: admins and treasurers get suggestions where they typed an id.

---

## P45 — The module page's cards in one registry

**Finding.** P41 put the server page's cards on `loadCard()`, but not the
module page's:

- **Frames**: `js/pages/module.js` builds five card frames by hand inside
  `raw(\`…\`)`: role menus, Secret Santa, moderation (two), Spotify and XP
  (`module.js:109-280`, 186 lines). Achievements, the Gazette and the giveaways
  use `cardHtml()`.
- **Loaders**: `js/core/render.js` starts their loaders with 8 branches of
  `if (state.currentPage === 'module' && state.currentModule === '…')`.
- **Spinners**: 25 literal `<div class="loading"><div class="spinner">…` remain
  across 11 modules, where `SPINNER` exists (`js/ui/card.js`).

**Fix.** A registry, `registerModuleCard('moduleXp', { html: sid => …, load: sid => … })`,
called by each feature module. `module.js` draws the cards registered for the
module, and `render.js` loads them in one loop. The five hand-built frames move
onto `cardHtml()`, and the literal spinners become `SPINNER`. Adding a card
then touches one file, not three.

**Why before the dashboard lots.** P46–P55 add about a dozen cards and
sections. The module page's cards among them would each add one more branch to
`module.js` and one more to `render.js`.

**Lines** −170 / +70. **Risk** low; before/after screenshots of the eight
module pages with `tools/dashboard_demo.py --shots`.

---

## P46 — new: Suggestions, the staff's board on the module page

**Today.** A suggestion is decided in Discord only, through ✅ / ❌ / 🛠️ on its
post or `/suggestion approuver | refuser | implementer`. The Suggestions module
page has its settings and nothing else. Seeing what is still pending means
scrolling the channel.

**Add.**

- **Card**: « Suggestions » on the module page, filtered by status (en attente,
  approuvées, refusées, réalisées). Each row shows its number, title, author,
  👍 / 👎 counts, age and a link to its post and its « Discussion » thread.
- **Decision**: three buttons, each asking for an optional reason (the same form
  as Discord's), call the same decision as the buttons. That decision moves to
  `src/discord_ext/suggestions.py`: `SuggestionsRepository.decide` with
  `DECISIONS_FROM` (one conditional update, so a click in Discord at the same
  time decides once), then the post edited, the line in the thread and the DM
  to the author.
- **Routes**: `GET /api/servers/{id}/suggestions?status=` and
  `POST …/suggestions/{number}/decide`.

**Rules**: the staff check of Discord, `is_staff()` with the module's
`staffRoleId`, read live, on top of the admin tier. An admin who isn't staff
sees the board but no buttons. Votes are counts only, never who voted.

**Lines** −60 (moved) / +380. **Risk** low.

---

## P47 — new: Secret Santa, create, change and cancel the session from its card

**Today.** The card draws, reveals and (P42b) sets the exclusions. The session
itself is command-only:

- `/secretsanta creer budget date colis` posts the announcement in the channel
  where it is typed;
- `/secretsanta modifier` and `/secretsanta annuler`.

An admin preparing December has to do it from Discord, in the right channel.

**Add.**

- **No session**: the card's empty state becomes a form with a channel picker,
  the budget, the exchange date (a date field) and « Par colis ».
  « Créer la session » posts the same announcement as the command.
- **Before the draw**: « Modifier » (budget, date) and « Annuler la session »,
  in a danger dialog.
- **Shared code**: the session's creation and cancellation move from
  `extensions/secretsanta/sessions.py` to `src/discord_ext/secretsanta.py`,
  where the draw already is.
- **Routes**: `POST /api/servers/{id}/secretsanta/session`, `PUT` and `DELETE`
  on the same path.

**Rules**: creating is open to any member in Discord. Here it is the admin
tier, and the dashboard user can write in the chosen channel (`CAN_SEND`, read
live, as for the Gazette). Changing or cancelling is for the session's creator
or an administrator, as in Discord and as the draw already is.

**Lines** −40 (moved) / +320. **Risk** medium: the announcement is
member-visible, and it is the same message as today's.

---

## P48 — new: Moderation, sanction a member from the dashboard

**Today.** The moderation page lists the infractions and the reports, and can
revoke an infraction. Every sanction is Discord-only: `/avertir`, `/exclure`,
`/lever-exclusion`, `/expulser`, `/bannir`, `/debannir`. A report seen on the
dashboard can't be acted on there.

**Add.**

- **Member actions**: a « Sanctionner » menu on a member, from the infraction
  list, a report's row and a member picker. It offers « Avertir »,
  « Exclure (durée) », « Expulser » and « Bannir » with a reason.
- **Lift**: « Lever l'exclusion » and « Débannir » on the matching cases.
- **Shared code**: `warn_member`, `timeout_member` and the others are already
  split from their commands (`extensions/moderation/commands.py:160,241…`,
  "the caller has checked `can_moderate`"). They move to
  `src/discord_ext/moderation.py`, so the route records the case, DMs the
  member and logs to the modlog exactly as the command does.

**Rules**:

- **Permission**: Discord's own for each action, read live on the dashboard
  user. `MODERATE_MEMBERS` to warn and time out, `KICK_MEMBERS`,
  `BAN_MEMBERS`; MANAGE_GUILD is not enough.
- **Hierarchy**: `can_moderate(author, target, bot)`, the same as the command.
  No sanction on someone at or above you, or above the bot.
- **Audit**: in the audit log, besides the case.

**Lines** −150 (moved) / +450. **Risk** high: a ban is member-visible and hard
to undo. The lot ships with a test per permission and per hierarchy refusal.

---

## P49 — new: « Mon espace », my ZUnivers and Spotify reminders, my suggestions, my giveaways

**Today.** « Mon espace » has seven sections (`SECTIONS`,
`src/webui/member_space.py:31`): XP, reminders, birthday, Tricount, Secret
Santa, badges, agenda. What a member sets or does in five other modules is only
reachable by command.

**Add**, each only where its module is on, as the others:

| Section | Today, by command | In « Mon espace » |
|---|---|---|
| ZUnivers | `/zunivers journa activer \| retirer` (with Ninja and its strategy), `/zunivers 247 activer \| retirer \| statut` | both reminders on or off, Ninja and strategy, the 24/7 hours; when the next 24/7 window opens |
| Spotify | `/musique rappel activer \| retirer` (the daily vote reminder) | the reminder's time, on or off |
| Suggestions | nothing: a member finds their own by scrolling | their suggestions, with status, votes (counts) and the staff's reason |
| Giveaways | nothing | the open giveaways they entered (with « Me retirer ») and the ones they won |

**Routes**: member tier (`require_guild_member`) and the session's own user id
only, as the other sections (`require_section`). Each writes through the same
repository call as its command; the giveaway withdrawal is
`GiveawayRepository.remove_entrant`, one conditional update. The suggestions
and giveaways are read-only apart from that.

**Lines** +380. **Risk** low.

---

## P50 — new: « Mon espace », Tricount expenses, not only balances

**Today.** The Tricount section shows each group's balances and « J'ai
remboursé » (`POST …/tricount/{group}/settle`). Everything else is 14
commands: `/tricount depense`, `depenses`, `modifier-depense`,
`supprimer-depense`, `recurrente ajouter | liste | arreter`, `graphique`… An
expense is typed into option fields on a phone.

**Add**, in each group of the section:

- **Expenses**: the latest expenses, then « Voir tout ».
- **Add**: « Ajouter une dépense », a form with amount, description, category,
  who paid and the members who share it. It uses the group's members as a
  picker, not ids.
- **Change**: « Modifier » and « Supprimer » on the member's own expenses, as
  the commands allow.
- **Recurring**: the group's recurring expenses, and « Arrêter » on the
  member's own.

**Routes**: member tier, and the member must be in the group, as the commands
check. Writes go through `TricountRepository`
(`add_expense`, `update_expense_fields`, `delete_expense`, `stop_recurring`),
filtered on the group and the author, as the commands are.

**Lines** −30 / +380. **Risk** medium: balances are money the members owe each
other; tests per refusal (not in the group, not the author).

---

## P51 — new: Module pages that show the module

**Today.** Several module pages show only their settings, while the module
keeps data an admin would look at:

| Module | Command-only today | On its page |
|---|---|---|
| Birthdays | `/anniversaire liste` | the next birthdays (name, day, age where the year is known, as P7's rule says), and how many members set one |
| AI Compare | `/classement-ia` (the models' ranking from the votes) | the ranking, and today's calls against the global and per-member limits (`features/ai/usage.py`), counts only |
| Polls | nothing: running polls are only in their channels | the running anonymous and ranked polls: question, author, votes cast (counts), closing time, link |
| Zevent | `/zevent-fin` (freeze the tracker with the final recap) | « Publier le récapitulatif final », same code, `MANAGE_GUILD` read live as the command does |

**Routes**: admin tier, read on the bot loop. The Zevent recap goes through
`src/discord_ext/` like the Gazette's « Publier maintenant ». Nothing here
shows who voted or what a member asked the AI.

**Lines** +420. **Risk** low.

---

## P52 — new: Giveaways, recent draws and a new draw

P42c's card lists the open giveaways, with « Tirer maintenant » and « Annuler ».
`/concours nouveau-tirage` (new winners, excluding the previous ones) is still
command-only.

**Add.**

- **Card**: a « Derniers tirages » list under the open ones. It shows the 10
  most recently drawn giveaways (`GiveawayRepository.list_drawn`, already
  there), with their winners' names and a « 🎲 Nouveau tirage » button that
  asks how many winners.
- **Route**: `POST /api/servers/{id}/giveaways/{message id}/reroll` calls the
  same code as the command, moved to `src/discord_ext/giveaway.py` like the draw
  in P42c. Guild admin and module on, as for the draw, and audited.

**Lines** +150. **Risk** low.

---

## P53 — new: RSS, each feed's state and its reset

`/rss liste` (each feed's last fetch and status) and `/rss reinitialiser`
(forget what was posted; the next cycle re-seeds without posting) are
command-only. The RSS widget sets the feeds and can test one (`POST …/rss/test`),
but says nothing about how they are doing.

**Add.**

- **Widget**: each feed's row shows its last fetch and its last error, read from
  the feed's state in MongoDB (`last_poll_at`, `last_error`, `failures`).
- **Reset**: a « Réinitialiser » button calls the same reset as the command.
  `POST /api/servers/{id}/rss/{feed id}/reset`, guild admin, audited.

**Lines** +170. **Risk** low.

---

## P54 — new: LLDAP, sync now, the linked accounts, and « Créer mon compte »

**Today.** `/lldap synchroniser` (ADMINISTRATOR) and `/inscription <email>
[username]` (a member creates their account) are command-only. The module page
has its settings and nothing else: an admin can't see which members have an
account, or what the last sync changed.

**Add.**

- **Admin card**: the members linked to an LLDAP account (Discord name, LLDAP
  username, groups), read from the server's own instance (P37) on the bot loop
  (`list_users`, `list_groups`). « Synchroniser maintenant » runs `run_sync` and
  shows its report (`describe_report`).
- **« Mon espace »**: a section for members holding a mapped role. It shows
  their account (username, groups), or « Créer mon compte » (email, username),
  the same code as `/inscription`. The temporary password is shown once on the
  page, never stored or logged.

**Rules**:

- **Admin card**: the command's, ADMINISTRATOR or the owner read live
  (`ctx.guild_access`), not the dashboard's MANAGE_GUILD.
- **« Mon espace »**: the member tier, plus the mapped role read live.
- **Secrets**: the LLDAP admin password never reaches the browser.

**Lines** +260. **Risk** medium: a live call to the server's LLDAP, with its
timeout.

---

## P55 — new: Backups on the global page

**Today.**

- **Settings only**: the `backup` section of the global page holds the
  schedule and the retention.
- **Command-only**: a manual backup is `/sauvegarde`, registered on the
  developers' guild only (`extensions/backup.py:51`).
- **Nowhere**: nothing shows which backups exist. The health card shows
  the backup task's state, and `/metrics` the last one's time.

**Add**, in the global page's « Sauvegardes » section:

- **List**: the backups on the volume (date, size, databases), newest first.
- **Run now**: « Sauvegarder maintenant », the same run as `/sauvegarde`, on the
  bot loop. It shows its progress until it ends, then the new line.

Restoring stays a command-line step (`scripts/restore_backup.py`, RUNBOOK):
a restore from a browser is too easy to click.

**Rules**: developer tier (`require_developer`), as the global page. Nothing
in a backup is readable from the page, and nothing can be downloaded.

**Lines** +220. **Risk** low.

---

## P56 — Spotify votes: their own button callbacks, split, tested

**Finding.** `extensions/spotify/votes.py` is the largest extension module (897
lines, 41 % covered). It holds the daily vote, the vote on `/musique proposer`,
the reminders and `/nextvote`. It is also the only module that listens to every
button click the bot receives:

- `@listen(Component)` at `votes.py:381` and `:722` runs on every click on any
  module's buttons, and filters by hand.
- The daily vote's buttons have bare ids, `conserver`, `supprimer`, `menfou` and
  `annuler` (`votes.py:334-352`). Any other module's button named `annuler`
  would reach the Spotify vote.
- The add-with-vote buttons parse `addwithvote_<track>_<vote>` themselves.

Every other module uses `@component_callback` with a prefixed id
(`giveaway:enter`, the polls' `VOTE_PREFIX`).

**Fix.**

- **Callbacks**: `spotify_vote:<choice>` and `spotify_add:<track>:<vote>`, through
  `@component_callback` with a regex. The vote message that is open when the PR
  deploys still carries the old ids: at startup, its buttons are edited once to
  the new ones (its message id is in `vote_infos`).
- **Split** `votes.py` into `daily.py` (the daily vote), `proposals.py`
  (`/musique proposer` and its vote) and `reminders.py`.
- **Tests**: one per path with `tests/discord_fakes.py` and the in-memory
  Spotify fake. A vote, a change of vote, the 5 s cooldown, the close and its
  outcome, a vote reminder.

**Lines** −150 / +260 (tests ~200). **Risk** medium: the daily vote is
member-facing. Before/after screenshots of the vote message (Discord) in the PR.

---

## P57 — Zevent's logo: a setting, not a file uploaded every 30 s

**Finding.** During a Zevent, each guild's live message is edited with
`files=[File("data/Zevent_logo.png")]` (`extensions/zevent/tasks.py:113`):

- **Not in the repository.** The PNG is read from the operator's volume, and
  neither `README.md` nor `RUNBOOK.md` mentions it. Without it, every update
  fails, and is logged once per guild every 30 s.
- **Uploaded at every update.** `edit_message_if_changed()` can't compare a
  file, so `files` counts as a change (`_OPAQUE_KEYS`,
  `src/discord_ext/messages.py:239`). The message is therefore edited and the
  logo uploaded at every update (30 s, `zeventUpdateInterval`) on every server,
  even when no number changed.
- **The only one.** No other extension passes a file to an edit.

**Fix.** A `zeventLogoUrl` field in the `zevent` global section, next to
`zeventEventCoverUrl` (`_common.py:344`). The thumbnail becomes that URL, the
edit drops `files`, and an unchanged message is no longer edited. Empty means
no thumbnail, as for the cover.

**Lines** −10 / +20. **Risk** low; `config.example.json` regenerated. The
dashboard's global page gains one field (screenshot).

---

## P58 — Helpers written twice, round two, and the last dead code

| Twice | Where | Into |
|---|---|---|
| A number from the config, clamped | 7 copies: `_bounded` (`extensions/gazette.py:106`, `features/moderation/spam.py:28`), `_int` (`features/ai/settings.py:30`), `bounded_days` (`features/agenda/entries.py:43`), `announces_level` (`features/xp/leveling.py:51`), `clamp_months` (`features/xp/seasons.py:47`), and Zevent's three parsers (`extensions/zevent/_common.py:347,368,390`). Six of them need a `type: ignore` | one `clamp_int(value, default, low, high)` (and a float twin) in `src/core/text.py` |
| A document to its model | `_doc_to_giveaway`, `_doc_to_infraction`, `_doc_to_poll`, `_doc_to_menu`, `_doc_to_reminder`, `_doc_to_entry`, `_doc_to_suggestion`: the same 5 lines in seven repositories | `GuildRepository._model(doc, Model)` (`src/core/db.py`) |
| A module's settings "even while it is off" | `app_credentials` (`src/integrations/spotify.py:59`), `get_settings` (`shlink.py:101`), `server_key` (`llm.py:260`), `_saved_settings` (`src/webui/routes/uptime.py:22`), each reading `servers.<id>.<key>` by hand | `ModuleConfig.saved(guild_id)` next to `get()` |
| `ModuleConfig`, re-written | `extensions/compareai/_common.py:155-185` (`_module_raw`, `module_enabled`, `enabled_guild_ids`) | `ModuleConfig("moduleIA")` |
| A server's settings in a route | `ctx.get_full_config().get("servers", {}).get(server_id)` in 8 more places (`servers.py:97,134`, `overview.py:51`, `health.py:68`, `checks.py:225`, `onboarding.py:59`, `module_copy.py:31`, `config_history.py:31`) | `ctx.server_settings(server_id)` |
| A counter | `next_case_id` (`features/moderation/repository.py:38`) and `next_id` (`features/suggestions/repository.py:42`) | `GuildRepository.next_number(name)` |
| A French date | `french_date` (`features/ai/prompt.py:69`) and `format_exchange_date` (`features/secretsanta/dates.py:105`) | `src/core/clock.py` |
| `"data/backups"` | `extensions/backup.py:45`, `extensions/metrics.py:69`, `src/core/db.py:245`, `src/webui/schemas.py:830` | one constant |
| Two autocompletes | `ajouterediteur_autocomplete` (`confrerie/editors.py:206`) and `demande_autocomplete` (`confrerie/requests.py:130`), identical | one |

**HTTP and I/O left over.** The Twitch emote cache (`extensions/twitch/emotes.py:38-44`)
calls `session.get()` itself, so the download has none of `fetch()`'s timeout
and retries. It also writes the PNG on the event loop. Move it to `fetch()`, and
write the file in a thread.

**Dead code.** It's what a scan of every definition against the whole
repository (code, tests, the SPA) finds:

- **Unused helpers**: `remove_punctuation`, `search_dict_by_sentence` and
  `extract_answer` (`src/core/text.py:147-171`), plus the empty « Weighted
  random message picker » section header after them.
- **Unused constants**: `COUNTRY_NAME` (`olympics/_common.py:113`),
  `COL_UPDATE` (`features/confrerie/stats.py:29`), `_ATOM_NS`
  (`features/rss/parser.py:26`) and `RULESETS`
  (`features/zunivers_ninja/client.py:25`).
- **Test-only**: `guild_task_names` (`src/webui/guild_health.py:283`), used by
  one test and nothing else.

**Lines** −230 / +70. **Risk** low; each helper keeps its callers' tests.

---

## P59 — Test fakes, round two

P39 put the Discord fakes in `tests/discord_fakes.py` and the dashboard's in
`tests/webui_fakes.py`. The test files still define 116 local fakes (2000 lines).
Most are specific to one test and stay. These copy a shared one, or each other:

| Fake | Local copies | Lines |
|---|---|---|
| `FakeChannel` | 10 (`test_guild_messages.py`, `test_admin_delete.py`, `test_zevent_record.py`, `test_webui_onboarding.py`, …) | 141 |
| `FakeBot` | 8 | 107 |
| `FakeCtx` | 5 | 67 |
| `FakeGuild` | 4 | 71 |
| `FakeMember`, `FakeRole`, `FakeUser` | 2 each | ≈ 60 |
| `MemoryXp`, `FakeXpRepo` | 3 + 2 (`test_profile.py`, `test_xp_seasons.py`, `test_xp_leveling.py`…) | 130 |
| `MemoryTricount` | 2 (`test_tricount_recurring.py`, `test_tricount_permissions.py`) | 92 |

**Fix.** Use `discord_fakes`, extending it where a copy needs something it
lacks; `tests/xp_fakes.py` and `tests/tricount_fakes.py` as
`santa_fakes.py` / `giveaway_fakes.py` did. A guard like P39's: a test file
doesn't define a class named like one of the shared fakes.

**Lines** −620 / +120. **Risk** low (tests only).

---

## P60 — The event trackers' parsing in `features/`, tested

**Finding.** The six least-tested files (9–18 %, listed above) have one thing
in common. Each parses an API payload or builds its embed inside an extension
mixin, so testing them means building the extension. `architecture.md` asks
for domain logic in `features/` ("Domain code returns plain data and lets the
extension build Discord objects"):

- **Zevent**: `ApiMixin` (`extensions/zevent/api.py`) fetches and parses the
  stats API, the planning and the reference curve.
- **Olympics**: `MedalsMixin._fetch_france_medals`, `_fetch_medal_standings`
  and `_medal_key` (`olympics/medals.py:31-72`) parse the medal tables.
- **Streamlabs and Speedons**: they build their API URLs and walk the pagination
  in the extension (`streamlabs.py:97-221`, `speedons.py:79-89`).
- **MDI**: the schedule and match lines (`mdi/embeds.py:59-238`) are pure text
  built from `MatchSnapshot`.
- **Twitch schedule**: `add_field_to_embed` and the segment grouping
  (`twitch/schedule.py:61-160`).
- **VLR.gg**: the roster and summary helpers (`vlrgg/commands.py:237-260`,
  `:319`).

**Fix.** Move each parser and text builder to its feature (`features/zevent/stats.py`,
`features/olympics/medals.py`, `features/streamlabs/`, `features/mdi/lines.py`…).
Test them against recorded payloads (`tests/data/<feature>/*.json`, trimmed),
as `test_rss_parser.py` does. The mixins keep the fetching, the slots and the
embeds. Afterwards, raise both coverage floors by what the PR measures (expected
about +3 points), keeping them below the figures without MongoDB.

**Lines** −150 / +520 (≈ 380 of it tests and fixtures). **Risk** low: the
extensions call the same code through a module path.

---

## P61 — README and command names in the docs, checked

**Finding.** The README predates P27–P42 in places:

- "The SPA: `src/webui/static/index.html` and its script `app.js`": `app.js`
  is gone (the SPA is `js/`, since P17).
- "Custom views" lists role menus, moderation, Spotify statistics and short
  links. It leaves out the Secret Santa card (draw, reveal, exclusions), the
  Gazette, Achievements, the Uptime alerts, the giveaways, the Spotify and
  Twitch connections, and copying a module from another server.
- "Stable short links — Config-driven links (Spotify dashboard, …)":
  `spotifyDashboardUrl` was dropped in P34.
- `/zunivers journa activer|remove`: it is `retirer`.
- It names commands by their English names (`/ask`, `/pick`, `/roll`, `/coin`,
  `/shuffle`, `/ask-stats`). A French Discord client shows them as `/demander`,
  `/choisir`… (`en_fr_name`, `src/discord_ext/locale.py:18`), and the bot's
  members are French. `Privacy.md` and `ToS.md` say `/ask` too.

Elsewhere:

- The `zuniversNinja` section's description, shown on the dashboard, says
  "`/zunivers journa set ninja:True`" (`src/webui/schemas.py:870`): it is
  `activer`.
- `async-io.md` names `_build_api()` in `extensions/satisfactory.py`: it is
  `_api()`.

**Fix.** Correct them: commands are written by their French name with the
English one in brackets, `/demander (/ask)`. A guard, `tests/test_docs_commands.py`:
every `` `/command sub` `` in `README.md`, `Privacy.md`, `ToS.md`, `docs/` and the
schemas' descriptions names a command or subcommand the extensions register,
in either language. Paths (`/api`, `/metrics`, `/tmp`) are told apart by the
leading word.

**Lines** −40 / +80. **Risk** none.

---

## P62 — *(optional)* Global sections' schemas next to their extension

**Finding.** Module schemas live with their extension (`_common.py`). Global
sections don't: `src/webui/schemas.py` (984 lines, the largest file in `src/`)
holds the schema framework and 13 sections. Eight of them belong to one
extension:

- `vlrgg` → `vlrgg`
- `zuniversNinja` → `zunivers`
- `uptimeKuma` → `uptime`
- `metrics` → `metrics`
- `backup` → `backup`
- `AI`, `OpenRouter`, `NanoGPT` → `compareai`

**Fix.** Register each in its extension's `_common.py`, with `register_section`
as modules do. `discord`, `mongodb`, `twitch`, `webui` and `errorReporting`
stay, since the core reads them. `schemas.py` drops to about 620 lines.

**Why optional.** A section then exists only once its extension is imported.
The dashboard and `generate_config_example.py` import every extension, so
nothing changes today. But a section must not vanish from the global page if an
extension failed to load: the lot adds a check for that. The gain is where
things live, not lines.

**Lines** −360 / +340. **Risk** medium.

---

## Decisions

Made:

1. **P43**: the draw file is no longer written (your answer on #280).

Still open:

2. **P62**: worth doing, or leave the global sections where they are?
3. **P44**: is `/effacer avant|apres` fine as the one option left taking an id?
   The other way is a message context-menu command, « Effacer jusqu'ici »,
   which is a member-visible change.
4. **P47**: creating a Secret Santa session is open to any member in Discord.
   On the dashboard it would be admins only (the tier). Fine, or should
   « Mon espace » offer it too?
5. **P48**: sanctions from the dashboard are the riskiest lot (a ban is hard to
   undo). Do it, or keep sanctions in Discord and stop at the list and revoke?

---

## Areas checked and fine

- **Settings**: every field of every registered schema is read somewhere
  outside its schema (a scan of the code and the SPA). P28 left none unread.
- **Dependencies**: every runtime dependency in `pyproject.toml` is imported
  (`curl_cffi` by Olympics alone, `prettytable`, `nbtlib` and `asyncssh` by
  Minecraft alone).
- **The SPA**: 593 CSS classes and every one is used (`dc-h1`–`dc-h3` are built
  as `dc-h${level}`). No unused export. The `escapeHtml(` count is at its
  ceiling of 6 (`test_spa_html.py`). The plain template strings left
  (49, mostly in `xp.js`, `rolemenus.js`, `spotify.js`) are static markup or
  already-escaped values, not member text.
- **HTTP**: every client goes through `src/core/http.py` (`fetch` /
  `fetch_untrusted`). The Twitch emote cache is the exception, in P58.
- **Blocking calls on the loop**: a scan of every `async def` for file and
  network calls finds two, the emote cache (P58) and the draw file (P43).
- **Persistent messages**: all 12 extensions that keep one go through
  `fetch_or_create_persistent_message` / `GuildMessages`.
- **Docs**: every path and identifier the conventions name exists, except
  `_build_api()` (P61). The runtime paths (`config/config.json`,
  `data/twitchcreds.json`) are on the volume, as they should be.
- **Zevent's global section** is still read when the extension loads, but
  `GLOBAL_SECTIONS` reloads it on a save (P34), so a change applies. That's fine.
- **Type ignores**: 28 in all. The `arg-type` / `call-overload` ones on number
  parsing go with P58. The rest are interactions.py annotations
  (`allowed_mentions`, `cover_image`), not ours to fix.
