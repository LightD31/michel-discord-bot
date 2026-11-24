# Dependencies, CI and tooling

How the lockfile, the image, CI's pins and the hooks are kept. The one-line rules of `CLAUDE.md` point here.

## Dependencies

`pyproject.toml` declares dependency ranges (runtime in `[project]`, tooling in the `dev` dependency group); `uv.lock` pins both, and the Docker image (`uv sync --locked --no-dev`) and CI (`uv sync` with `UV_LOCKED=1`) install from it. Edit ranges in `pyproject.toml`, then run `uv lock`. CI also runs `pip-audit` against the runtime pins (`uv export --no-dev`) and a detect-secrets sweep against `.secrets.baseline`.

> **Pulling CVE fixes into the lock.** `uv lock` keeps every existing pin that still satisfies `pyproject.toml`, so it never picks up newer releases on its own. To pull in security fixes, run `uv lock --upgrade-package <name>`, or `uv lock --upgrade` to refresh everything. A CVE fix in a transitive dependency can require bumping its *direct* pinning parent too (e.g. the `starlette` 1.3.x fix is gated behind a `fastapi` bump that lifts its `<1.3.0` cap).
>
> **uv version.** The Dockerfile's `uv` stage and `UV_TOOL_VERSION` in `.github/workflows/ci.yml` name the uv release used to install; Dependabot bumps only the Dockerfile one, so move the CI one along with it (CI's `pins` job fails until both agree). The MongoDB the `mongo` tests run against in CI is pinned by digest in `.github/mongo-test/Dockerfile`, a file nothing builds: the `pins` job reads its `FROM` line for the test job's service, and Dependabot's docker ecosystem keeps it current.
>
> **Image publishing.** `.github/workflows/docker.yml` is a reusable workflow, called by `ci.yml`: a pull request builds and scans the image with a read-only token, and a push to `master` or a `v*` tag publishes it only once every CI job has passed (`publish-image` `needs` them all), so a red build never reaches `:latest`. The image holds only what `.dockerignore` allows (an allowlist: the code, the lockfile, `scripts/`), the code belongs to root and is precompiled, and the compose file runs it read-only (`/tmp` a tmpfs, the `data/` and `config/` volumes writable), with no capability and `no-new-privileges`: anything the bot writes goes under `data/`.
>
> **Pre-commit.** The ruff and detect-secrets hooks run the locked tools (`language: system`, `uv run`), so they never drift from `uv.lock`; `pre-commit-hooks` is pinned by commit (`pre-commit autoupdate --freeze`, then put the `pragma: allowlist secret` back on its `rev:` line).
>
> **GitHub Actions are pinned by commit SHA** (`uses: owner/action@<sha> # vX.Y.Z`). When adding or bumping an action by hand, pin the commit of a release tag (the peeled `^{}` entry from `git ls-remote --tags`), never a branch or a moving tag; Dependabot keeps existing pins current. Any recent uv reads and writes the same `uv.lock` (unlike `uv pip compile --universal`, `uv lock` resolves against `requires-python` rather than the host interpreter, so the Python version you lock on doesn't matter).

## Notes

- `requires-python >= 3.12`; CI and the Docker image run Python 3.15, and CI's `test-py312` job runs the suite on 3.12 so that floor stays real — don't use 3.13+-only syntax or APIs. Ruff target-version is `py312`, line length 100.

- **Windows + detect-secrets**: run scans with `PYTHONUTF8=1` (e.g. `$env:PYTHONUTF8=1` before `pre-commit run` or `detect-secrets-hook`). Without it, detect-secrets reads files in the legacy locale encoding, silently skips any file containing characters it can't decode (e.g. the ⭐ emoji in `config.example.json`), and will wrongly prune valid `.secrets.baseline` entries that CI then re-flags. Never commit a baseline shrink you can't reproduce with `PYTHONUTF8=1`.
