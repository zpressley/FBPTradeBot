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
