# Privacy Policy

**Effective date: 2026-11-21**

This policy explains what personal data **Michel** (the "Bot"), a Discord bot, processes, why, with whom it is shared, and how long it is kept. It also covers the Bot's web dashboard.

## 1. Who is responsible

The instance of Michel described here is run by **LightD31** (the "Operator"). Contact: **michel.f40ls@wholeakedmyemail.fr**.

Michel is open-source software. Anyone can run their own copy, and a copy run by someone else is that person's responsibility, not the Operator's.

Michel is made of modules. A Discord server's administrators choose which modules are enabled in their server and how they are configured. Most of what follows only applies in servers where the module concerned is enabled.

## 2. Data the Bot receives from Discord

Like any Discord bot, Michel receives, for the servers it is in:

- the server's members: their Discord ID, username, display name, avatar and roles;
- messages in the channels it can see, including their text, in order to run its features. Examples are counting activity for XP, moderation filters, keyword replies, and the context for AI answers;
- reactions, voice-channel presence (used for voice XP) and scheduled events;
- the commands you run, and the buttons and menus you use.

The Bot does **not** store message text in general. It stores only what a feature needs, as listed in section 3.

## 3. Data stored by the Bot

Data is stored in the Bot's own database, separately for each server. Data from direct messages (Secret Santa run in a group DM, for example) goes into a shared database. The table below is complete for the modules shipped with the Bot.

| Feature | What is stored |
|---|---|
| Member directory | Discord ID, username and display name of each member of each server, kept up to date. Used to show names on leaderboards and in the dashboard. |
| XP & levels | Per member: XP, level, message count and last activity time. A log of each XP gain (time, amount, and whether it came from a message or voice activity), kept 400 days; older gains are summed per member, month and source, and the log entries deleted. No message text. Where the server uses seasons, the top 10 of each finished season (member and XP), posted as a podium. |
| Birthdays | The date you register (the year only if you give it), whether to hide the year, and your time zone (Europe/Paris unless you choose another). |
| Reminders | The reminder text, its time and recurrence, and who should receive it. For a reminder made from a message ("⏰ Me le rappeler"), a link to that message; not its text. |
| Polls | A public poll is a Discord poll: Discord keeps it and its votes, the Bot stores nothing. For an anonymous or ranked poll, the poll, its options and each participant's vote (an anonymous poll stores the votes but does not display who voted for what). |
| Giveaways | The giveaway (with the role or XP level it may require) and its participants and winners. Entering means clicking its "Participer" button; the participants can be listed in that message. Checking an XP level reads the level the XP system already keeps. |
| Suggestions | The suggestion's title and text, its author, the votes cast (by member), its discussion thread and the moderators' decision and reason, which its author also receives by direct message. For a suggestion made from a message, a link to that message. |
| Moderation | Infractions (warning, timeout, kick, ban): the member, the moderator, the reason, the date and the duration. Messages caught by automatic filters are deleted; the infraction is recorded. Reports of a message ("🚩 Signaler"): which message and channel, its author, who reported it, their reason and the date, and how the moderators handled it (who, and the case). The message's text is not stored: the report's card in the moderators' channel quotes it, so Discord keeps that copy. Who reported a message is shown to the moderators only. |
| Starboard | Which messages were reposted to the starboard and their reaction counts. The repost itself is a message in the server, so a copy of the original text and images is kept by Discord. A message from a channel not every member can see is not reposted to a starboard they all can, unless the server's administrators allow it. |
| Reaction roles | The role menus (roles, labels, their style and limit, and who created them). A menu still being built with `/menu-roles creer` is kept in memory only, for 15 minutes. |
| Tricount (shared expenses) | Groups and their members, expenses (amount, description, category, payer and who shares it) and reimbursements between members. A member who leaves a group still appears in its balance until they have settled up. |
| Secret Santa | Participants, the draw results (who gives to whom) and the pairs that must not be drawn together. The draw results are stored unencrypted, readable by the Operator. Your wish list if you write one, and, for a session whose gifts are posted, when you marked your gift sent and the one you were given received. Anonymous messages between a Santa and their recipient are passed on by direct message and not stored; a message you report is sent to the session's organiser, with who sent it. |
| Spotify playlist | The tracks added through the Bot, with who added them; votes on tracks, by member. Server administrators may also link members to their Spotify account and to a first name shown instead of their Discord name. |
| AI answers (`/ask`, mentions) | Comparisons of answers: the question, the answers, the asker's and each voter's IDs, and the votes. Daily usage counters per member, used to enforce limits. See section 5. |
| Book club ("Confrérie") | Submitted drafts and their authors. |
| "Feur" replies | Per member: how many times the Bot answered them. |
| Profile (`/profil`) and weekly Gazette | Nothing new: they show what the modules above already keep and the server already shows (level, season XP, starboard posts, playlist tracks, suggestions, giveaways won, the day and month of a birthday, never the year). The Gazette also names the members who joined the server that week, from Discord. The Bot keeps only the date and message of each Gazette it posted. |
| ZUnivers | The ZUnivers username you provide and your reminder settings. |
| Account registration (`/inscription`, where enabled) | On the server's own account directory (LLDAP), managed by that server's administrators: an account with your email, a username derived from your Discord name, your display name and your Discord ID. Your roles are kept in sync with your directory groups. The temporary password is shown only to you and is not stored by the Bot. |
| Other modules | Notifications (RSS, YouTube, Twitch, game servers, esports, events…) store only their own state, such as the last item posted. This is not personal data. |

Server administrators can also enter configuration that contains personal data, such as members' first names. That configuration is stored in the Bot's configuration file.

## 4. The web dashboard

The dashboard lets server administrators configure the Bot. Every member can also open their own space there ("Mon espace"), on the servers they share with the Bot: their level and rank, their reminders, their birthday, their Tricount balances and their Secret Santa wish list, which they can change there as with the slash commands. It shows only the signed-in member's own data, on servers they are a member of right now (checked against Discord at least every minute); nothing new is stored for it.

- **Sign-in:** you sign in with Discord (OAuth2, scopes `identify` and `guilds`). The Bot receives your Discord ID, username, display name, avatar and the list of your servers with your permissions in each. It uses these to decide what you may manage.
- **Session:** your session lasts 24 hours. It is stored under a hash of your session cookie, never the cookie itself. The tokens Discord issues at sign-in are discarded straight after.
- **Cookies:** two cookies, both strictly necessary: `michel_session` (24 hours, your session) and `oauth_state` (5 minutes, protects the sign-in). There are no advertising or analytics cookies.
- **IP addresses:** they are used in memory to rate-limit sign-in attempts, for about a minute, and are not stored.
- **Changes are logged:** each configuration change and action made through the dashboard (publishing embeds, reloading the Bot's modules, revoking a sanction…) is recorded with the name and Discord ID of whoever made it, in the technical logs and in an audit log kept one year. A server's audit log is shown to every administrator of that server in the dashboard; it names the settings that changed, never their values.
- **Earlier settings:** each time a module's settings are saved from the dashboard, the settings they replace are kept for 90 days, so that the server's administrators can see what changed and put an earlier version back. Passwords and keys are never kept, and neither is who saved. These copies can hold whatever the administrators had written in the settings (message texts, first names, channel and role IDs).
- **Third-party requests:** the dashboard loads images (avatars, server icons) from Discord's CDN, and the charting library from the jsDelivr CDN. These services therefore see your IP address when you use the dashboard.
- **Public statistics pages:** server administrators can publish XP or Spotify statistics at a secret link. Anyone with that link can see the display names of the most active members, contributors and voters, but no Discord IDs. Votes on an open Spotify poll are only shown once it closes.

## 5. Sharing with third parties

The Operator does not sell personal data. Data is sent to third parties only when you, or a server you are in, use a feature that needs them:

- **Discord**, which hosts the servers, messages and accounts the Bot works with. Its privacy policy applies.
- **AI providers**, for `/ask` and replies when the Bot is mentioned: OpenRouter and/or NanoGPT, which forward each request to the developer of the selected model (for example OpenAI, Anthropic, Google or Mistral). Each request sends:
  - the question and the asker's display name;
  - the server and channel names and the date;
  - the text of recent messages in the channel, with their authors' display names (10 by default; server administrators can choose from 0 to 50), and for a mention up to 5 earlier messages of the reply chain. Attachments are not sent;
  - the instructions and server description written by the server's administrators.

  This means **your messages can be sent when someone else asks a question in the same channel.** The AI features are off by default: server administrators must enable them, and replies to mentions are a separate option. `/ask` only exists on the servers that enabled them: not in direct messages, nor through a user install. The AI providers process this data under their own terms and policies. Do not share sensitive information in channels where the AI features are used.
- **Spotify:** what you type in the song search, and the playlist changes the Bot makes on the Operator's Spotify account.
- **Notion**, for the book club: drafts and works are kept in a Notion workspace chosen by the server's administrators.
- **A link-shortening service** chosen by the Operator: links in the Bot's posts are replaced by short links.
- **ZUnivers / ZUnivers Ninja:** the ZUnivers username you provide, to fetch your public game data.
- **The server's own account directory**, for `/inscription` (see section 3).

The Bot also fetches public information (RSS feeds, YouTube, Twitch, game servers, esports and event APIs) without sending any personal data to those services.

## 6. Logs, monitoring and backups

- **Technical logs** record the Bot's activity and errors, including usernames and Discord IDs (for example who ran a command or changed a setting). They are kept on the Operator's server, up to about 100 MB, the oldest lines dropped first (usually a few weeks), and the most recent 2000 lines are also kept in memory for the Bot's developers.
- **Monitoring:** when the Operator enables it, a metrics endpoint publishes counters to the Operator's monitoring system. These include the display names of the most active members, but no Discord IDs.
- **Error reports:** when the Operator enables it, the details of an unexpected error are sent to an error-tracking service chosen by the Operator (Sentry, or a GlitchTip server the Operator may host). A report contains the error, the code where it happened, the server's ID, the command or button involved, and the Bot's warning and error log lines from just before it. Those lines can include usernames, Discord IDs or what was typed in a command. A report does not include the data the code was working on, dashboard sessions or credentials. Reports are kept according to that service's retention settings.
- **Backups:** the database and the configuration file are copied every day to the Operator's server, readable only by the Bot's account there, and may be copied off-site, encrypted. Only the last few copies are kept (7 by default, a week for the off-site copy), and older ones are deleted. Dashboard sessions are never included.

## 7. How long data is kept

- Member directory: as long as you are a member of the server; your entry is deleted when you leave it (at the Bot's next start if it was offline then).
- Your other data on a server: 30 days after you leave it, unless you come back within that time, what is yours alone (XP, birthday, reminders…) is deleted and what you share with others is anonymised, as `/mes-donnees supprimer` does (section 9).
- XP gain log: 400 days, then summed per month (see section 3).
- Dashboard sessions: 24 hours.
- Dashboard audit log: one year.
- Earlier module settings kept by the dashboard: 90 days.
- Reports of a message to the moderators: one year after the message was first reported.
- AI usage counters: about two days.
- One-time reminders: deleted when they are sent.
- Secret Santa wish lists and gift marks: 30 days after the exchange date (90 days after the session was created when it has no date), or sooner when the session is cancelled or replaced.
- Technical logs: a few weeks (see section 6).
- Backups: about a week by default (see section 6).
- Everything stored for a server: deleted, with that server's settings, 30 days after the Bot leaves the server (kicked, or the server deleted), unless it is added back within that time.
- Everything else: as long as the feature is used in the server, or until it is deleted by you, by the server's administrators or by the Operator on request.

## 8. Why the data is processed

The data is processed to provide the features that you, or the administrators of a server you are in, chose to use. That covers the Operator's and the server administrators' legitimate interest in running the server and its tools, and the requests you make yourself, such as a reminder, a birthday or an account registration. Moderation records are kept to keep servers safe. Logs and backups are kept to run the Bot reliably and to recover from failures.

## 9. Your rights

You can do two of these yourself, on every server at once, with the commands below or from your space in the dashboard ("Mes données"), which do the same:

- `/mes-donnees exporter` sends you a file, visible to you alone, with everything the Bot stores about you.
- `/mes-donnees supprimer` deletes what is yours alone (XP and its log, birthday, reminders, ZUnivers reminders, AI usage counters, Secret Santa wish lists and gift marks, giveaway entries, member directory entry…) and anonymises what you share with others: your votes still count, but no longer under your name, and the polls, suggestions, playlist tracks, AI questions, giveaways, starboard posts, role menus, book club drafts and season podiums you are in no longer name you. It keeps your moderation records (including the reports of your messages; if you reported a message, the report no longer names you, but your reason stays), the Secret Santa draws already made, and your Tricount groups while you still owe or are owed money there. Configuration written by a server's administrators (a first name, a linked Spotify account) and an LLDAP account made with `/inscription` are handled by that server's administrators.

You can also ask the Operator, at the address in section 1, to:

- access the data the Bot holds about you;
- correct it;
- delete it;
- object to or restrict its processing.

The Operator will answer within a reasonable time, and at the latest within one month. Some data may have to be kept where that is needed to keep a server safe, such as moderation records. You can also:

- stop using a feature, or ask a server's administrators to disable it;
- leave a server, which removes you from its member directory;
- lodge a complaint with your data protection authority (in France, the CNIL).

## 10. Children

The Bot is meant to be used on Discord, which requires its users to meet a minimum age (13, and older in some countries). The Bot is not intended for children below that age. If you believe a child has provided personal data through the Bot, contact the Operator and it will be deleted.

## 11. Security

Configuring a server in the dashboard is limited to its administrators, and a member's space to that member's own data on the servers they belong to; both are re-checked against Discord at least every minute. Sessions are stored as hashes, and secrets are never shown in the dashboard. The database and backups are kept on servers controlled by the Operator. No system is perfectly secure: if you notice a problem, please report it to the address in section 1.

## 12. Changes to this policy

This policy may change as the Bot evolves. The current version is always the one published in the Bot's repository, with its effective date at the top.
