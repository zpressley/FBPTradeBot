# Replay test notes (not implemented)

Spec 02, "Data migration", suggests replaying 2026 weeks through `rules.resolve()`.

## How it would work

1. `git log --follow --format=%H -- data/auction_current.json` lists every commit that touched the file.
2. For each week, take the last commit before `resolved_at` was set (or the commit that sets it: it still holds `bids`, `matches`, `priority_order`, and `resolved_winners`). Later commits blank `bids` and `matches`, so the pre-reset version is the only copy.
3. Map 2026 rows to rules objects: `Bid(team, prospect_id, bid_type, amount, timestamp)` and `decisions = {prospect_id: match["decision"]}`.
4. Balances: WB balance at the start of resolution. Rebuild from `data/wizbucks.json` / `wb_ledger` at that commit (balance before the `auction_winner` rows for that week), not from today's file.
5. Run `rules.resolve()` and compare team, amount per prospect against `resolved_winners`.

## Expected differences (not bugs in the new code)

- **Flat $10 OB-only.** Any uncontested OB above $10 resolves at its OB amount now, but 2026 recorded $10. Compare with the OB-only price normalized to 10, or report these separately.
- **Affordability bug.** 2026 `_enforce_affordability` only checked teams that had wins at the start. A week where a dropped prospect pushed an inheriting team over budget will differ; the new code trims that team.
- **Equal-bid ties.** 2026 resolution already used the lower seed. Committed funds during the week used "whoever is already high" instead, which does not affect resolution.
- **Priority order.** Uses the `priority_order` stored on that week's state, so it should match.
- **Unsold prospects.** New code emits no Result when no affordable bidder is left. 2026 did the same (no winner entry).
- **Dropped-prospect fallback.** If all CB teams drop out, the OB team gets the prospect at its OB amount even after a forfeit (same as 2026's recompute, but now at the OB amount rather than $10).

A mismatch outside these categories means the old or the new code has a bug.

## Choices for Zach to confirm

- **Affordability drop order.** When a team's wins cost more than its balance, `resolve()` handles over-budget teams in `priority_order` order (worst standings first) and drops that team's *latest* win first. "Latest" is the OB's `placed_at` for an OB_ONLY or OB_MATCH win (the team's OB starts that win) and the winning CB's `placed_at` for a CB_WIN. Every team is re-checked after each drop. The spec does not say which win is dropped or in what order teams are handled; this is a choice, pinned by tests in `auction/tests/test_rules.py`. The 2026 code dropped by dict order, so a replay can differ in weeks where a team was over budget.
- **OB_MATCH timing changed.** Before this note the match was dated by the challenger's CB; it is now dated by the matching team's own OB, so a team's CB wins are dropped before its OB match.

## Before go-live

- **`calendar["start"]` must be the Monday of the pre-season auction week, not Opening Day.** `rules.phase_for` treats every week before `start` as `off_week`, so setting it to Opening Day would make the first auction week unbiddable. Set it from the 2027 league calendar when the auction opens (mid-March).
- **The affordability drop order needs Zach's confirmation** (see "Choices for Zach to confirm" above). Until he confirms it, treat which win is dropped when a team can't pay for all of them as provisional.
- `resolve_week` records a prospect that became owned between bidding and resolution under `unsold` with the reason "already owned by a team" and resolves the rest. Whether such a prospect should instead go to the next bidder is a rule question for Zach.
