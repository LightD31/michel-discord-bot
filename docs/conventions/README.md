# Conventions

The reasoning behind the one-line rules of `CLAUDE.md`, by topic. Read the file of the area you work in before changing it; a guard test's failure message says which rule it holds.

| File | Read it when you touch |
|---|---|
| [architecture.md](architecture.md) | the layers, where a new module goes, per-guild databases |
| [extension-lifecycle.md](extension-lifecycle.md) | loading, reloading, mixins, `Task`s, slash command scopes, custom commands |
| [config.md](config.md) | a schema, a setting, a persistent message, the URLs in config |
| [dashboard-security.md](dashboard-security.md) | a dashboard route, its tier, saves, secrets, the member space, public pages |
| [dashboard-frontend.md](dashboard-frontend.md) | the SPA: modules, actions, escaping, previews, the save bar, "Tester" buttons |
| [async-io.md](async-io.md) | anything that waits: HTTP, blocking SDKs, CPU work, pollers, cross-loop calls |
| [discord-messages.md](discord-messages.md) | what the bot posts: edits, mentions, links, errors, tone, embeds, forms |
| [data-and-retention.md](data-and-retention.md) | MongoDB documents, dates, backups, retention and member data |
| [observability.md](observability.md) | logging, error reporting, Prometheus metrics, health |
| [features.md](features.md) | XP seasons and the Gazette, reports, first steps, Secret Santa, Communauté+ |
| [testing.md](testing.md) | the test suite, fakes, MongoDB tests, source-scanning guards |
| [tooling.md](tooling.md) | dependencies, the lockfile, CI pins, pre-commit, Python versions |
