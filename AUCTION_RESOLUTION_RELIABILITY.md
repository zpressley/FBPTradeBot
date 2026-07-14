# Weekly Auction Resolution — Reliability Notes (2026-07-13)

## Why "Auction Resolution Pending Persistence" kept happening

Three independent processes were all trying to resolve the current auction
week and commit the same files to git, every Sunday, with **no cross-process
lock**:

1. The live Discord bot's `auction_tick` loop (`commands/auction.py`) — runs
   every minute, retries with backoff (60/120/300/600/900s).
2. `.github/workflows/daily-update.yml`'s "Resolve weekly auction (Sundays
   only)" step — ran at ~6am ET, before the 10am ET match/forfeit deadline
   even closed, with a bare `git commit && git push` and no retry.
3. `.github/workflows/resolve-auction.yml` — added 2026-07-06 specifically to
   fix this, running at ~10:30am ET (after the deadline). Its own commit
   message says it was meant to be "isolated from daily-update.yml" — but
   the old step in `daily-update.yml` was never removed. So the fix added a
   third racer instead of replacing the buggy one.

`AuctionManager.resolve_week()` has an idempotency guard (`resolved_at` in
`auction_current.json`), but nothing prevents two of these three from
pushing to git at nearly the same moment — one gets rejected as a
non-fast-forward push, and that's the literal mechanism behind "persistence
failed." Git history shows a season-long pattern of manual "Backfill missed
auction resolutions" commits roughly every 2 weeks, confirming this wasn't a
one-off.

## What's been fixed so far

- **2026-07-13:** Removed the redundant step from `daily-update.yml`. Down
  from 3 racers to 2 (the live bot's tick loop, and `resolve-auction.yml`).

## Recommended next steps (not yet applied — pending sign-off)

1. **Make the live bot the sole writer.** Change `resolve-auction.yml` to
   stop running `scripts/resolve_auction.py` + its own git commit in a
   separate GitHub-hosted checkout. Instead, have it `POST` to the existing
   `/api/admin/auction/resolve-now` endpoint (already live in `health.py`,
   already proxied through the Cloudflare Worker) so the *same* Railway
   process that runs the tick loop does the resolving and committing either
   way. This removes the cross-machine git race entirely — there's only one
   process on the planet ever writing these files. If the bot happens to be
   down when the Action's HTTP call fires, that's a clear, loud failure
   (HTTP error) instead of a mysterious "persistence failed" message days
   later.
2. **Close the one remaining in-process race.** `/api/admin/auction/resolve-now`
   (`health.py`) already wraps `resolve_week()` + commit in `DATA_LOCK`. The
   tick loop in `commands/auction.py` does **not** — so a manually-triggered
   resolve-now call and the tick loop's own automatic attempt could still
   interleave within the same process. Wrap the tick loop's resolve+commit
   calls in `DATA_LOCK` too, matching the endpoint.
3. **Optional:** add a Discord/log alert specifically for "the Sunday
   resolve-now ping got no response from the bot," so a Railway outage at
   the critical moment surfaces immediately instead of silently waiting for
   someone to notice stale auction data.
