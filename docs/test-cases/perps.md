# Test cases — perps

grug list. `@stateless` = no money. `@trades` = real order. `@serial` = ordered, share volume. see [index](../../TEST_CASES.md).

perp api (greenfield). suites live under `suites/nimbus/perp/`.

## test_risk_tiers.py — tiered margin (PERP-2544), GET /perpetual/market-risk-tiers
| tag | case | grug |
|---|---|---|
| @stateless | seeded ladder + shape | ask BTC tiers. real ladder. all field good. |
| @stateless | monotonic ladder | tier up: cap up, leverage down, rate up. |
| @stateless | MMR < IMR each tier | maint rate below open rate. always. |
| @stateless | IMR = 1/leverage | open rate = 1 / max leverage. |
| @stateless | tier by notional (inclusive) | notional pick tier. on cap = low tier. over = next. |
| @stateless | MM jump crossing tier | same notional, higher tier = more maint margin. liq pressure. |
| @stateless | all-markets snapshot | no symbol = all markets. BTC there. |
| @stateless | unknown symbol 404 | bad symbol -> 404 market_not_found. |

## test_risk_tier_leverage.py — leverage guards (PERP-2544)
| tag | case | grug |
|---|---|---|
| @stateless | leverage < 1 rejected | set leverage 0 -> 400 invalid_leverage_value. |
| @stateless | leverage > global max rejected | set leverage 126 -> 400 invalid_leverage_value. |
| @trades | over-tier leverage rejected | cross acct. leverage high while flat. open big -> 400 leverage_exceeds_tier. |

## test_position_history.py — position history (PERP-2548), GET /perpetual/position-history[/:id]
maps notion TC-Perps-011..017. api layer only (no UI, no forced liq/adl).
| tag | case | grug | notion |
|---|---|---|---|
| @stateless | list needs app_session_id | no session -> 400 missing_parameter. | — |
| @stateless | list bad page_size | page_size 101 -> 400 validation_failed. | — |
| @stateless | detail needs app_session_id | no session -> 400 missing_parameter. | — |
| @stateless | detail bad page_size | page_size 501 -> 400 validation_failed. | — |
| @stateless | detail unknown id | ghost id -> 404 not_found. | — |
| @trades | list envelope + market filter | list shape ok. all row match market. empty = null -> []. | TC-011 |
| @trades | closed position in history + fills | open. close. show in history. status normal. fields good. detail = open+close fills, open no pnl, close has pnl, time up. | TC-012, TC-013 |

not covered (manual only, cant force in harness): TC-014/016 liquidation & adl status, TC-015/017 their order details.

## test_tiered_reduction.py — tiered position reduction / Stage0 (PERP-2545)
inject mark via faucet `/api/simulate-mark-price` (hold: re-submit every ~1.5s, price only lasts 2s).
cross, fresh funded subject + maker per test, thin market (SUIUSDT-PERP). needs maker resting at
bankruptcy (reduce is book IOC, NOT insurance fund). observe: `/perpetual/transaction/history?type=liquidation_partial`.
| tag | case | grug | notion |
|---|---|---|---|
| @trades | liquidated PIECE BY PIECE (one tier per mark drop) | open deep tier3 long (~400k notional, 539k SUI). maker rests reduce counterparty bid near entry (insurance fund NOT live -> reduce IOC needs real book order). drop mark in small steps (hold each, re-inject ~1.5s). engine peels ONE tier (tier3->tier2) -> >=1 LIQUIDATION_PARTIAL row, position OPEN at intermediate size, NOT closed all at once; next drop = Stage1 full close. live: 539k->357k(tier2, open)->0. | TC-LIQ-033-ish |
| @trades | reduce by EXACTLY 1 tier + account unlocks | open tier2 (~150k) with FAT deposit (15k) so one reduction to tier1 heals. drive maxPieces=1, restore mark -> position stays OPEN at ~tier1 cap. live: 202k->149k (~110k notional≈tier1), 1 reduction, 1 partial, open after restore. | TC-LIQ-030 |

**GOTCHA / spec-vs-code (file a doc bug):** shipped Stage0 ≠ the ticket. (1) fixed **1 tier per round** (loops ≤10), NO "110% margin-ratio -> 1 vs 2 tier / conservative-aggressive" strategy (2-rung only a min-lot fallback). (2) reduce = ReduceOnly **book IOC at bankruptcy price**, NOT "executed against the insurance fund" (insurance fund is only the Stage1 takeover counterparty). test asserts the CODE, not the ticket.

**>=2 partial rows NOT reproducible (as-built, verified 2026-07-03):** under a monotonic mark drop the engine does exactly ONE Stage0 partial peel (one tier), landing the remainder at the solvency edge; the very next drop trips Stage1 FULL liquidation (full close emits NO liquidation_partial row). Tried deposits 12k/25k/45k × steps 0.006/0.0035/0.0025 — always 1 partial + full close (more deposit only pushes the trigger deeper, same edge geometry). A 2nd partial needs the mark to RECOVER after the peel (Stage0 heals) — that's TC-LIQ-030's path, not a monotonic drop. So the test asserts `minPieces=1` (>=1 partial + observed-open-at-intermediate = piecewise, not all-at-once), NOT 2.

**Observability facts:** LIQUIDATION_PARTIAL only in `/perpetual/transaction/history` (type filter accepts `liquidation_partial`). `/perpetual/trades` shows the delta fill but exec_type=`trade` (the `liquidation_partial` exec_type const is reserved/unused). `/perpetual/positions` = amount drops (no flag). `/position-history` = NO row for a partial (only full close). manual-only (can't force deterministically here): liquidation/adl full-close close_reason variants (TC-LIQ-031/032 depth/no-depth).

## test_liquidation_takeover.py — takeover trade price integrity (PERP-3325) @trades @serial
full CROSS liquidation force-settles underwater legs at bankruptcy price via the settlement pool (NOT the
book) -> real `exec_type=liquidation_takeover` trades. bug: a NEGATIVE bankruptcy price persisted as-is
(price & total < 0). fix clamps <=0 -> 0, so invariant is **price >= 0 / total >= 0**.

**WHY MULTI-LEG (do NOT "simplify" to one short):** a non-positive price ONLY arises in the batch calculator
(`calculator/cross.go:302-413`). single-leg short bankruptcy = `entry + deposit/size` is ALWAYS positive
(verified: a lone short liquidates at 0.839, clamp never fires -> vacuous). the batch spreads the account
deficit across legs; a leg goes <=0 only when a deeply-crashed LONG drags shared equity negative and the
negative lands on a SHORT sibling. so repro = ETH+DOGE incident shape: dominant LONG (SUI) crashed toward 0 +
small SHORT (DOGE) held ~flat (Step2 does NOT IOC it as profitable), long_notional > deposit + short_notional.
**live proof (run-0010): SUI long takeover price=0.0686 (+); DOGE short takeover price=0 (clamp fired).** the
price=0 is the fingerprint that makes the test non-vacuous — remove the clamp and DOGE goes negative -> red.
WARNING: crashing the long market is market-wide (liquidates other longs there); restore ASAP.
| tag | case | grug | notion |
|---|---|---|---|
| @trades | full cross liq records liquidation_takeover, price/total non-negative | fresh cross subject + maker. subject opens dominant LONG (SUI ~40k) + small SHORT (DOGE ~4k) vs maker. crash SUI mark to entry×0.01, hold DOGE mark entry×1.01 (short at small loss, not IOC'd). hold til account fully liquidated, restore both. poll /perpetual/trades both markets. NON-VACUOUS gate: >=1 takeover clamped to exactly 0 (batch clamp fired). INVARIANT: every liq trade price>=0 AND total>=0. fail-loud: both legs opened, fully liquidated, >=1 takeover. | PERP-3325 |
| @trades | no trade carries negative price / inconsistent total | scan the liquidated account's FULL trade history (all exec_types, both markets). assert every row price>=0, total>=0, total==amount×price (bug persisted total = amount × NEGATIVE price). | PERP-3325 |

**Takeover path IS live (code-confirmed, contra tiered-reduction's "insurance fund not live"):** `NewInsuranceService`
wires FundService/TakeoverService/SettlementService; a full cross liq publishes synthetic `liquidation_takeover`
events (taker = settlement pool, `cross/flow.go:802-848`). Only Stage0/Stage1 IOC partials that hit Finex
persist as `exec_type=trade`. **Residual gaps (not asserted, INFO only):** clamp maps <=0 -> **0** (a 0-priced
takeover is still recorded); the **isolated** flow (`stage1/isolated/flow.go`) passes the raw bankruptcy price
UNCLAMPED; no positive-price guard at the persistence layer (`RecordTrade` / `CreatePerpetualsTrade`).

## test_account.py — perp account
| tag | case | grug |
|---|---|---|
| @trades | account has USDT collateral | seed acct. USDT there. |
| @trades | set leverage reflected | set 10x. acct show 10x. money not move. |

## test_orders.py — perp orders (no fill)
| tag | case | grug |
|---|---|---|
| @trades | resting limit -> open_orders -> cancel | rest limit far. show in open_orders. margin lock. cancel -> gone. margin back. |

## test_positions.py — perp position lifecycle
| tag | case | grug |
|---|---|---|
| @serial | open via market fill | buy vs seeded maker. long grow by amount. margin lock. |
| @serial | close releases margin | reduce-only sell. long shrink. margin back. |
