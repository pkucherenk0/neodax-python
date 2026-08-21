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

**Setup notes:** target the first tier whose `max_leverage` sits below the market's
`max_allowed_leverage` — only there does an over-the-tier-yet-still-settable account leverage
exist. cross-margin accounts ignore the order's own `leverage` field; the opening risk-tier
check uses **account** initial leverage — so flatten first, then raise account leverage above
the target tier's cap (allowed while flat; the leverage endpoint only runs the tier check with
a position open). Rest price is 5% below mark (never fills) so a regression would still be
caught at placement, not by an accidental fill; notional is sized off mark, not the rest price.
**Not tested (gap):** `risk_tier_exceeded` (notional above every tier's cap) — unreachable on a
funded account (top tier cap 1e12 quote, order amount capped at 1e6 base). The
`POST /perpetual/leverage` variant (raise leverage *with* an open position, same check
function) needs a real fill on a thin book to set up — omitted as high-cost/redundant.

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

**Flow (one mark-drop step):**
```
mark drops (held ~2s, re-injected ~1.5s)
        │
        v
 position crosses a tier boundary?  --no--> nothing happens, next step
        │ yes
        v
 Stage0 engine peels exactly ONE tier (book IOC @ bankruptcy price vs maker's resting bid)
        │
        v
 >=1 LIQUIDATION_PARTIAL row  +  position OPEN at intermediate (smaller) size
        │
        v
 mark restored / healed?  --yes--> equity >> next tier's maintenance -> ladder STOPS, stays open
        │ no (mark keeps dropping)
        v
 next drop trips Stage1 FULL liquidation (no liquidation_partial row emitted for the full close)
```

| tag | case | grug | notion |
|---|---|---|---|
| @trades | liquidated PIECE BY PIECE (one tier per mark drop) | open deep tier3 long (~400k notional, 539k SUI). maker rests reduce counterparty bid near entry (insurance fund NOT live -> reduce IOC needs real book order). drop mark in small steps (hold each, re-inject ~1.5s). engine peels ONE tier (tier3->tier2) -> >=1 LIQUIDATION_PARTIAL row, position OPEN at intermediate size, NOT closed all at once; next drop = Stage1 full close. live: 539k->357k(tier2, open)->0. | TC-LIQ-033-ish |
| @trades | reduce by EXACTLY 1 tier + account unlocks | open tier2 (~150k) with FAT deposit (15k) so one reduction to tier1 heals. drive maxPieces=1, restore mark -> position stays OPEN at ~tier1 cap. live: 202k->149k (~110k notional≈tier1), 1 reduction, 1 partial, open after restore. | TC-LIQ-030 |

| Fact | Detail |
|---|---|
| GOTCHA / spec-vs-code (file a doc bug) | shipped Stage0 ≠ ticket. fixed **1 tier per round** (loops ≤10), no "110% margin-ratio → 1-vs-2-tier" strategy (2-rung is only a min-lot fallback). reduce = ReduceOnly **book IOC at bankruptcy price**, not "insurance fund" (that's only the Stage1 takeover counterparty). test asserts the code, not the ticket |
| ≥2 partial rows NOT reproducible (verified 2026-07-03) | monotonic mark drop → exactly ONE Stage0 peel, then next drop trips Stage1 full close (no partial row). tried deposits 12k/25k/45k × steps 0.006/0.0035/0.0025 — always 1 partial + full close. a 2nd partial needs the mark to RECOVER after the peel (TC-LIQ-030's path). test asserts `minPieces=1`, not 2 |
| Observability | `LIQUIDATION_PARTIAL` only in `/perpetual/transaction/history` (`type=liquidation_partial`). `/perpetual/trades` shows the delta fill but `exec_type=trade` (the `liquidation_partial` exec_type const is unused). `/perpetual/positions` = amount drops, no flag. `/position-history` = no row for a partial (full close only). manual-only, can't force here: liquidation/adl full-close `close_reason` variants (TC-LIQ-031/032) |

## test_liquidation_takeover.py — takeover trade price integrity (PERP-3325) @trades @serial
full CROSS liquidation force-settles underwater legs at bankruptcy price via the settlement pool (NOT the
book) -> real `exec_type=liquidation_takeover` trades. bug: a NEGATIVE bankruptcy price persisted as-is
(price & total < 0). fix clamps <=0 -> 0, so invariant is **price >= 0 / total >= 0**.

**Cross-margin liquidation execution (see top-level `CLAUDE.md` for the full formula):**
```
 equity < total maintenance margin  →  account locked, cross.LiquidationFlow.Execute runs:

 Step 1 — same-market long + short?  --yes--> net the opposing legs first (reduces exposure)
        │no
        v
 Step 2 — position PnL at scan   --positive--> IOC order @ MARK price (fills vs book; partial
        │                                       remainder falls through to Step 3)
        │zero/negative
        v
 Step 3 — force-settle @ per-position BANKRUPTCY price (insurance fund/settlement pool takes
          over; user realizes the loss, fund absorbs any shortfall)
```

**WHY MULTI-LEG (do NOT "simplify" to one short):** a non-positive takeover price only arises in the
BATCH calculator (`calculator/cross.go:302-413`) — a single-leg short's bankruptcy price
(`entry + deposit/size`) is always positive (verified: a lone short liquidates at 0.839, clamp
never fires → vacuous test). the batch spreads the account deficit across legs; a leg goes ≤0
only when a deeply-crashed LONG drags shared equity negative and the negative lands on a SHORT
sibling — repro shape: dominant LONG (SUI) crashed toward 0 + small SHORT (DOGE) held ~flat
(Step 2 doesn't IOC it, not profitable), `long_notional > deposit + short_notional`.
**Live proof (run-0010):** SUI long takeover price=0.0686 (+); DOGE short takeover price=0
(clamp fired) — the price=0 IS the non-vacuous fingerprint; remove the clamp and DOGE goes
negative → red. WARNING: crashing the long market is market-wide (liquidates other longs there)
— restore ASAP.

| tag | case | grug | notion |
|---|---|---|---|
| @trades | full cross liq records liquidation_takeover, price/total non-negative | fresh cross subject + maker. subject opens dominant LONG (SUI ~40k) + small SHORT (DOGE ~4k) vs maker. crash SUI mark to entry×0.01, hold DOGE mark entry×1.01 (short at small loss, not IOC'd). hold til account fully liquidated, restore both. poll /perpetual/trades both markets. NON-VACUOUS gate: >=1 takeover clamped to exactly 0 (batch clamp fired). INVARIANT: every liq trade price>=0 AND total>=0. fail-loud: both legs opened, fully liquidated, >=1 takeover. | PERP-3325 |
| @trades | no trade carries negative price / inconsistent total | scan the liquidated account's FULL trade history (all exec_types, both markets). assert every row price>=0, total>=0, total==amount×price (bug persisted total = amount × NEGATIVE price). | PERP-3325 |

**Takeover path IS live** (contra tiered-reduction's "insurance fund not live" — code-confirmed):
`NewInsuranceService` wires FundService/TakeoverService/SettlementService; a full cross liq
publishes synthetic `liquidation_takeover` events (taker = settlement pool, `cross/flow.go:802-848`).
Only Stage0/Stage1 IOC partials that hit Finex persist as `exec_type=trade`. Residual gaps (INFO
only, not asserted): clamp maps ≤0 → **0** (a 0-priced takeover is still recorded); the
**isolated** flow (`stage1/isolated/flow.go`) passes the raw bankruptcy price UNCLAMPED; no
positive-price guard at the persistence layer (`RecordTrade`/`CreatePerpetualsTrade`).

## test_account.py — perp account
| tag | case | grug |
|---|---|---|
| @trades | account has USDT collateral | seed acct. USDT there. |
| @trades | set leverage reflected | set 10x. acct show 10x. money not move. |

## test_orders.py — perp orders (no fill)
| tag | case | grug |
|---|---|---|
| @trades | resting limit -> open_orders -> cancel | rest limit far. show in open_orders. margin lock. cancel -> gone. margin back. |

**Ground truth (ordered independently of the test):** a limit order locks `qty × price /
leverage` exactly — no slippage buffer (market-only) and no fee (checked, not locked). Source:
`converters.go` `CalculateInitialMargin` ~L218-249, `lock_oneway.go`/`lock_hedge.go`,
`TestLockPerpAsset_LockMarginOnly_NotMarginPlusFee`. Rest at 5% below mark (never fills, still
inside the price band). `account`'s baseline settle-check is >=90%, not exact — settle to the
fixture's known funded baseline first (same race as `spot/test_orders.py`). Lock/release both
poll for the exact expected amount rather than one unguarded read, since the lock/release can
trail order visibility by a beat.

## test_positions.py — perp position lifecycle
| tag | case | grug |
|---|---|---|
| @serial | open via market fill | buy vs seeded maker. long grow by amount. margin lock. |
| @serial | close releases margin | reduce-only sell. long shrink. margin back. |

**Gotcha:** margin ledger settles async, separate from position size — poll `allocated`, not a
single read. Gate on `allocated`, not `available`: `available` also bundles instant fill PnL
(open) / realized PnL (close), which can legitimately swing either way on a live market
(confirmed: `BTCUSDT-PERP` moved ~10% within one run, concurrent Trades-lane traffic on the
same market). `allocated` rising/dropping is the actual "margin locked/released" invariant;
`available` is recorded info-only, never a gate.
