# Privacy Policy

**Effective date: 2026-11-07**

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

Data is stored in the Bot's own database, separately for each server. Data from direct messages, or from use outside configured servers, goes into a shared database. The table below is complete for the modules shipped with the Bot.

| Feature | What is stored |
|---|---|
| Member directory | Discord ID, username and display name of each member of each server, kept up to date. Used to show names on leaderboards and in the dashboard. |
| XP & levels | Per member: XP, level, message count and last activity time. A log of each XP gain (time, amount, and whether it came from a message or voice activity). No message text. |
| Birthdays | The date you register, whether to hide the year, and your time zone. |
| Reminders | The reminder text, its time and recurrence, and who should receive it. |
| Polls | The poll, its options and each participant's vote (anonymous polls also store votes, but do not display who voted for what). |
| Giveaways | The giveaway and its winners. Entering means reacting to the giveaway message; the participants can be listed in that message. |
| Suggestions | The suggestion text, its author, the votes cast (by member) and the moderators' decision. |
| Moderation | Infractions (warning, timeout, kick, ban): the member, the moderator, the reason, the date and the duration. Messages caught by automatic filters are deleted; the infraction is recorded. |
| Starboard | Which messages were reposted to the starboard and their reaction counts. The repost itself is a message in the server, so a copy of the original text and images is kept by Discord. |
| Reaction roles | The role menus (roles, labels and who created them). |
| Tricount (shared expenses) | Groups and their members, expenses (amount, description, category, payer and who shares it) and reimbursements between members. A member who leaves a group still appears in its balance until they have settled up. |
| Secret Santa | Participants, the draw results (who gives to whom) and the pairs that must not be drawn together. |
| Spotify playlist | The tracks added through the Bot, with who added them; votes on tracks, by member. Server administrators may also link members to their Spotify account and to a first name shown instead of their Discord name. |
| AI answers (`/ask`, mentions) | Comparisons of answers: the question, the answers, the asker's and each voter's IDs, and the votes. Daily usage counters per member, used to enforce limits. See section 5. |
| Book club ("Confrérie") | Submitted drafts and their authors. |
| "Feur" replies | Per member: how many times the Bot answered them. |
| ZUnivers | The ZUnivers username you provide and your reminder settings. |
| Account registration (`/register`, where enabled) | On the server's own account directory (LLDAP), managed by that server's administrators: an account with your email, a username derived from your Discord name, your display name and your Discord ID. Your roles are kept in sync with your directory groups. The temporary password is shown only to you and is not stored by the Bot. |
| Other modules | Notifications (RSS, YouTube, Twitch, game servers, esports, events…) store only their own state, such as the last item posted. This is not personal data. |

Server administrators can also enter configuration that contains personal data, such as members' first names. That configuration is stored in the Bot's configuration file.

## 4. The web dashboard

The dashboard lets server administrators configure the Bot.

- **Sign-in:** you sign in with Discord (OAuth2, scopes `identify` and `guilds`). The Bot receives your Discord ID, username, display name, avatar and the list of your servers with your permissions in each. It uses these to decide what you may manage.
- **Session:** your session lasts 24 hours. It is stored under a hash of your session cookie, never the cookie itself. The tokens Discord issues at sign-in are discarded straight after.
- **Cookies:** two cookies, both strictly necessary: `michel_session` (24 hours, your session) and `oauth_state` (5 minutes, protects the sign-in). There are no advertising or analytics cookies.
- **IP addresses:** they are used in memory to rate-limit sign-in attempts, for about a minute, and are not stored.
- **Changes are logged:** each configuration change made through the dashboard is recorded with the name and Discord ID of whoever made it.
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

  This means **your messages can be sent when someone else asks a question in the same channel.** The AI features are off by default: server administrators must enable them, and replies to mentions are a separate option. `/ask` can also be used in direct messages and in servers where the Bot was installed by a user, if the Operator allows it. The AI providers process this data under their own terms and policies. Do not share sensitive information in channels where the AI features are used.
- **Spotify:** what you type in the song search, and the playlist changes the Bot makes on the Operator's Spotify account.
- **Notion**, for the book club: drafts and works are kept in a Notion workspace chosen by the server's administrators.
- **A link-shortening service** chosen by the Operator: links in the Bot's posts are replaced by short links.
- **ZUnivers / ZUnivers Ninja:** the ZUnivers username you provide, to fetch your public game data.
- **The server's own account directory**, for `/register` (see section 3).

The Bot also fetches public information (RSS feeds, YouTube, Twitch, game servers, esports and event APIs) without sending any personal data to those services.

## 6. Logs, monitoring and backups

- **Technical logs** record the Bot's activity and errors, including usernames and Discord IDs (for example who ran a command or changed a setting). They are kept on the Operator's server, and the most recent 2000 lines are also kept in memory for the Bot's developers.
- **Monitoring:** when the Operator enables it, a metrics endpoint publishes counters to the Operator's monitoring system. These include the display names of the most active members, but no Discord IDs.
- **Error reports:** when the Operator enables it, the details of an unexpected error are sent to an error-tracking service chosen by the Operator (Sentry, or a GlitchTip server the Operator may host). A report contains the error, the code where it happened, the server's ID, the command or button involved, and the Bot's warning and error log lines from just before it. Those lines can include usernames, Discord IDs or what was typed in a command. A report does not include the data the code was working on, dashboard sessions or credentials. Reports are kept according to that service's retention settings.
- **Backups:** the database and the configuration file are copied every day to the Operator's server. Only the last few copies are kept (7 by default), and older ones are deleted. Dashboard sessions are never included.

## 7. How long data is kept

- Member directory: as long as you are a member of the server; your entry is deleted when you leave it.
- Dashboard sessions: 24 hours.
- AI usage counters: about two days.
- One-time reminders: about a day after they are due.
- Backups: about a week by default (see section 6).
- Everything else: as long as the feature is used in the server, or until it is deleted by you, by the server's administrators or by the Operator on request.

## 8. Why the data is processed

The data is processed to provide the features that you, or the administrators of a server you are in, chose to use. That covers the Operator's and the server administrators' legitimate interest in running the server and its tools, and the requests you make yourself, such as a reminder, a birthday or an account registration. Moderation records are kept to keep servers safe. Logs and backups are kept to run the Bot reliably and to recover from failures.

## 9. Your rights

You can ask the Operator, at the address in section 1, to:

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

Access to the dashboard is limited to the administrators of each server, and their permissions are re-checked against Discord at least every minute. Sessions are stored as hashes, and secrets are never shown in the dashboard. The database and backups are kept on servers controlled by the Operator. No system is perfectly secure: if you notice a problem, please report it to the address in section 1.

## 12. Changes to this policy

This policy may change as the Bot evolves. The current version is always the one published in the Bot's repository, with its effective date at the top.
