# Data Quality Report

_Generated 2026-08-08T11:17:55.790978+00:00 from dataset `imbalance_prices`, 26208 cached rows._

## Gaps

_none_

## Duplicates

_none_

## Regulation state distribution

_Skipped: this dataset has no `regulation_state` column. The settled ENTSO-E feed publishes the two prices but not the state, so the dual-price analysis below stands in for it._

## Settlement invariant

`price_short >= price_long` follows from Table 2 for every regulation state: a BRP can never be paid more for being long than it is charged for being short in the same period. A non-zero count means the columns are swapped or the settlement rules have changed.

**Violations: 0 of 26208 ISPs.**

## Dual pricing

**35.9% of 26208 ISPs are dual-priced** (`price_long != price_short`).

This is a rigorous **lower bound** on the frequency of regulation state 2, not its exact value: states 0/+1/-1 always price both sides identically, so a difference implies state 2 — but a fully reverse-priced state-2 period collapses both legs to the mid-price and is counted here as single-priced.

## Structural breaks — dual-price share before/after

ADR-007 predicts the state-2 share RISES at 2026-02-03, when TenneT switched the regulation-state input from the 1-minute to the 12-second balance delta (15 → 75 samples per ISP), with no change in the physical system. Reported plainly whichever way it goes: if it did not rise, ADR-007's reasoning is wrong and must be corrected rather than explained away.

**Confounded with season** — these windows span winter to summer, so a rise is consistent with the mechanism but does not demonstrate it (ADR-016).

```
      date                                                 what  dual_share_before  dual_share_after  n_before  n_after
2025-11-25                    balance_delta_cadence_1min_to_12s           0.309028          0.364165      2304    23904
2026-02-03 regulation_state_input_switched_to_12s_balance_delta           0.288010          0.396764      9024    17184
```
