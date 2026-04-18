# Security

## Reporting a vulnerability

Open a private security advisory on GitHub:
<https://github.com/LightD31/michel-discord-bot/security/advisories/new>.
Do **not** file public issues for security problems.

---

## ⚠️ Action required — SFTP credential rotation

A previous commit shipped a file named `debug_sftp.py` that contained a
working SFTP host, port, username, and password. The file has been deleted
from the working tree, but **the credentials were pushed to `origin` and must
be considered compromised**.

### What you (the maintainer) must do

1. **Rotate the password** on the SFTP account the credentials belonged to
   (host `82.65.116.168`, port `2224`, user `admin`). The new password must
   never be committed.
2. (Recommended) **Purge the file from git history** so the credentials stop
   appearing in every historical clone. For example with
   [`git filter-repo`](https://github.com/newren/git-filter-repo):

   ```bash
   git filter-repo --path debug_sftp.py --invert-paths
   git push --force-with-lease origin master
   ```

   Force-pushing rewrites public history — coordinate with any contributors
   first. Without purging, the credentials remain recoverable from old
   commits, mirrors, and GitHub's cached views.
3. Audit recent SFTP logs for unauthorized access during the exposure
   window.

---

## Secret-scanning policy

- Commits are scanned by
  [`detect-secrets`](https://github.com/Yelp/detect-secrets) via
  `.pre-commit-config.yaml`. Install the hook once:

  ```bash
  pip install -e ".[dev]"
  pre-commit install
  ```

- The baseline lives at `.secrets.baseline`. If a genuine new detection
  needs to be accepted (e.g. a documented test fixture), update the baseline
  with `detect-secrets scan --baseline .secrets.baseline` and commit the
  change.

- CI re-runs the scan against the baseline on every push and pull request
  (`.github/workflows/ci.yml`).

- Never store credentials in tracked files. Use `config/config.json`
  (git-ignored) or environment variables loaded through `python-dotenv`.
