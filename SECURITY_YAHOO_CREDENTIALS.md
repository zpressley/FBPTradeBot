# Yahoo credential exposure — remediation

**Status: open. Requires actions only the repo owner can take.**

## What is exposed

This repository is **public**. Two things in it are secrets:

1. `token.json` — tracked in git. Contains a live Yahoo `access_token` and
   `refresh_token`.
2. `data_pipeline/token_manager.py` — contains hardcoded `CLIENT_ID` and
   `CLIENT_SECRET` fallbacks, used whenever the env vars are unset.

Together these are enough for anyone to call Yahoo's Fantasy API as this app.
Yahoo throttles and blocks at the **app ID** level (see `WARP.md`, "External
API Rate Limits"), so third-party use of these credentials consumes the same
quota this bot depends on, and cannot be distinguished from our own traffic.

## Why neither can simply be deleted

Both are load-bearing:

- `.github/workflows/daily-update.yml` stages `token.json` on every run
  (`git add data/ token.json`). Committing the refreshed token back to the repo
  **is** the mechanism by which the refresh token survives Railway redeploys,
  which reset the container filesystem. Untracking the file without a
  replacement breaks token continuity.
- That same workflow sets no `YAHOO_CLIENT_ID` / `YAHOO_CLIENT_SECRET`, so CI's
  token refresh currently depends on the hardcoded fallback. Deleting the
  fallback before the secrets exist breaks the daily pipeline.

The workflow now reads `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET` and
`YAHOO_REDIRECT_URI` from repo secrets, falling back to the hardcoded values
while those secrets are unset. That makes the sequence below non-breaking.

## Remediation, in order

1. **Make this repository private.** Settings → General → Danger Zone → Change
   visibility. This is the single highest-value step: it closes the exposure of
   both the committed token and the client secret at once, and nothing here
   depends on the repo being public. `fbp-hub` is the GitHub Pages repo, not
   this one, so Pages is unaffected.

2. **Rotate the Yahoo client secret** at <https://developer.yahoo.com/apps/>.
   Assume the current one is compromised — it has been publicly readable.

3. **Add repo secrets** (Settings → Secrets and variables → Actions):
   `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`, `YAHOO_REDIRECT_URI`.
   Update the matching Railway service variables with the rotated values.

4. **Re-run the OAuth flow** to mint a fresh token pair against the rotated
   secret, and update Railway's `YAHOO_TOKEN_JSON`.

5. **Delete the hardcoded fallback** in `data_pipeline/token_manager.py` (the
   `or "dj0y..."` / `or "f12120..."` expressions). Safe only after step 3.

## Git history

Steps above stop future exposure. They do **not** remove the secrets from the
~4,500 commits of history already pushed. Rotation (step 2) is what actually
invalidates them; history rewriting on a branch that a running bot pushes to
hourly is high-risk and is not recommended as a substitute for rotating.

## Longer term

Committing `token.json` is only tolerable because the repo is private. The
durable fix is to stop persisting credentials in git — e.g. have the daily
workflow write the refreshed token back to Railway and GitHub as secrets via
their APIs, or mount a Railway volume for token storage that redeploys do not
reset. Either is a real piece of work, not a config tweak.
