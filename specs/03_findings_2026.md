# Rosters, Contracts and WizBucks: 2026 Findings

Audit of the Constitution and FBPTradeBot code and data, done 10/2/2026 to prepare Spec 03. File references are to the 2026 code.

## Constitution rules

**WizBucks (Art. I)**
- Installments: PAD by bracket (Championship $100, Consolation $120, Elimination $140). KAP $375. APA $100. PTDA (Championship $15, Consolation $25, Elimination $35).
- Rollovers: up to $25 per installment from the prior season (max $75). PAD to KAP max $30. KAP to APA max $100.
- Bonuses: Consolation winner +$50 KAP, runner-up +$25 KAP. Elimination top +$20 PAD, runner-up +$10 PAD.
- Penalties: roster violation $20 from KAP plus $5 per consecutive day. IP minimum miss $25 from PAD (part redistributed), $75 from KAP in the final 6 weeks plus loss of standings rewards.
- Spending: DC $5, PC $10, BC implied $20 (never stated outright). Draft slot prices not stated. Post-draft finalization: PC +$5, BC +$15 over the $5 down payment. DC debut fee $15. Buy-ins R1 $55, R2 $35, R3 $10. RaT $75. Auction min OB $10, raises $5. After APA, WB only for trades, DC purchases, auction and rollover.
- KAP tax brackets: $421-435 lose RD 4-8, $401-420 RD 5-7, $376-400 RD 6-8, $351-375 RD 7-9, $326-350 RD 8-10, $325 or less none. Cap $435. Must own a pick in a round to spend into that round's bracket.

**Keeper contracts (Art. II)**
- TC-R $5, TC-BC-1/2 $5, TC-1 $15, TC-2 $25, VC-1 $35, VC-2 $55, FC-1 $85, FC-2+ $125.
- Draft RD 1-3 → VC-1, other rounds → TC-1, undrafted non-rookies → TC-1, in-season prospect graduate → TC-R (not international rookies), BC graduate → TC-BC for two seasons then TC-1.
- Advancement is implied, not written: TC-R → TC-1 → TC-2 → VC-1 → VC-2 → FC-1 → FC-2+.
- IL tag: one per contract family, TC -$10, VC -$15, FC -$35, still advances, not on TC-R/TC-BC, injury before September with no return (or commissioner-approved), ineligible for the first 5 weeks.
- RaT: $75 tax-free, KAP only, not on TC or into TC, repeatable across seasons, advances from the reduced tier.
- No franchise or rookie tags. Release is not defined.

**Prospects (Art. III)**
- DC only at PAD, not for players with MLB service time. DC debut requires a $15 purchase to PC or the contract voids (player goes to the next Auction Portal). September call-ups exempt from the fee but can't be rostered unless purchased.
- Auction wins are PC. BC in the Nov 1 Top 100 keeps BC free.
- FBP limits: 350 PA, 100 IP, 30 appearances, or age 26. Forced graduation at the All-Star break (Art. V says MLB or FBP limits, Art. III says FBP only). Graduate must be on the active roster by the first Monday reset or goes to free agency.
- No 30-man or farm-size limit in the Constitution.

**Roster (Art. V)**
- 26 active (IL and NA not counted), at least 1 C, max 8 SP (9 with an SP/RP), max 14 pitchers, max 16 batters. Contracted players can use NA. Trim to 26 keeper salaries before the KAP trade window. Two-way players: 1 contract, 2 roster spots, traded as a package.

## How the code does it today

**WizBucks**
- Wallet `data/wizbucks.json` (keyed by full team name) plus ledger `data/wizbucks_transactions.json`. `wb_ledger.append_transaction` (wb_ledger.py:150) reads `balance_before` from the wallet, has no lock, and builds IDs from the epoch second (collisions).
- No installment or phase field, so rollover caps can't be enforced in code.
- 298 rows, 16 types: auction_winner 66, contract_purchase 58, admin_adjustment 34, trade debit/credit 23 each, buyin_purchase 23, PAD_allotment 12, KAP_allotment 12, KAP_submission 12, KAP_rollover 11, PAD_spend 10, PAD_optional_rollover 8, PAD/KAP_finish_reward 2 each, manual_adjustment_refund 1, auction_correction 1.
- APA and PTDA booked as `admin_adjustment`. Four "graduation penalty" -$10 rows with no constitutional basis (decided 10/27: admin error, totals correct, leave as history).
- **Ledger sum matches the wallet for 1 of 12 teams** (WIZ 160 vs 55, WAR 170 vs 25, HAM -115 vs 5). 23 balance-chain breaks (WAR 145 → 0 at KAP_allotment). No opening 2025 rollover row. 8 rows out of order, 114 with no season, 15 missing IDs, 3 duplicate IDs.
- PAD allotments don't match `final_rank_2025` brackets (TBB 5th got $100; HAM/RV 9th-10th got $120). Decided 10/28: ignore past payments and follow the Constitution (see Spec 03, "2026 final standings").

**PAD / KAP**
- PAD (`pad/pad_processor.py`) trusts the browser's `total_spend` (:604). No server pricing, no 15 DC slot cap. Reads a test-team setting from `os.getenv("WAR")` (:362). Re-saves the old wallet after the ledger write (:656), undoing the deduction. Lock not released on error.
- KAP (`kap/kap_processor.py`): salaries and IL discounts correct. Buy-ins count toward taxable spend but are charged separately. Over $435 returns "no tax" instead of blocking. "Must own a pick" not checked. IL eligibility not checked. Lock without try/finally.

**Contracts**
- `player_type` MLB/Farm. `contract_type` = DC/PC/BC as long names, or "Keeper Contract" for all MLB. `years_simple` = keeper tier or "P". `status` = display copy with the Sheets sort bracket.
- Owned: 55 DC, 96 PC, 74 BC. Keepers: TC-1 160, TC-2 50, VC-2 42, VC-1 33, TC-BC-1 18, TC-R 17, FC-1 5, FC-2 4.
- After KAP, `years_simple` holds next season's tier.
- 44 owned players where `status` and `years_simple` disagree. 28 owned with a blank contract. 267 unowned still "Keeper Contract", 1,284 unowned with "TC 1". `debuted` mixes booleans, years and nulls. 1 duplicate UPID. Two-way players stored as 2 rows linked by `2wayplayer`. No IL tag field.
- No season rollover job. KAP submit advances kept players and resets non-kept MLB players to unowned TC-1.
- **RaT changes no tier on the server.** Vlad, Harper and Riley advanced wrongly in 2026 and were fixed by hand. Acuña (FC-2+) charged at VC-2 for one RaT (two steps).
- Release paths disagree: `bulk-release` blanks the tier, KAP release sets TC-1, a Yahoo drop keeps the tier and the next owner inherits it.

**Graduation**
- Batch scripts only (`simplified_prospect_tracker.py`, `graduate_prospects_2025.py`). Adds a non-constitutional 80-game batter limit. `prospect_stats.json` keyed by name; `pitching_appearances` is 0 in every row, so the 30-appearance rule never fires. Applied with admin `bulk-graduate` or one-off scripts. No All-Star break job, no international rookie exception, no Nov 1 BC retention logic.

**Prospect purchases**
- `contract_purchase_processor.py`: DC→PC $5, DC→BC $15, PC→BC $10, no season gating. No $15 debut fee flow, no voiding. Managers can edit their own `debuted`/`debut_date` (api_manager_players.py:36), which gates DC eligibility.

**Other**
- Auction wins set PC. Trade WB legs with insufficient funds are skipped with a warning. Buy-ins charged per pick by the original owner. No roster limit or penalty logic. Yahoo pickups of unowned prospects become "NRI" (not in the Constitution).

## 2026 KAP data note: Acuña (added 10/4)

Kyle confirmed Acuña was **not** reduced to VC-2 in 2026 ("it was talked about but he didn't do it"). The KAP record says otherwise:

- `kap_submissions.json` lists Acuña at VC-2 with `has_rat: true`.
- JEP's KAP charge on 3/3/2026 was $290: $215 keeper salary (Acuña counted at $55) plus a $75 RaT. No later refund or adjustment appears in the ledger.
- JEP's taxable spend was recorded as $315 ($215 salary + $100 buy-ins), under the $325 tax line, so no rounds were taxed.

What it would have been instead:
- **No RaT (what Kyle describes):** Acuña at FC-2 $125, no $75 RaT. Salary $285, taxable $385, which falls in the $376-400 bracket (RD 6-8 taxed). JEP's WB total would have been $5 lower ($285 vs $290).
- **One-step RaT:** Acuña at FC-1 $85 plus $75 RaT. Salary $245, taxable $345, which falls in the $326-350 bracket (RD 8-10 taxed).

Acuña's stored tier today is FC-2, which is correct for 2027 under either version. This is for the admins to decide whether anything needs correcting; the new system just enforces the one-step rule going forward.
