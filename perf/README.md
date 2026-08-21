# perf — k6 performance testing (manual only, not in CI)

Separate project, plain k6 JS (k6's own embedded JS engine — no npm/Node needed to run these).
**Not wired into any CI workflow** — no `.github/workflows/*.yml` references this folder at
all. Run entirely on demand, the same way you'd run a load test against any real system: with
intent, not on every push. Modeled on the conventions from
`/Users/pk/Documents/LearningQA/performance` (phased scripts, thresholds as pass/fail gates,
`SharedArray` for parameterized data, a shared `lib/`) — see that repo's
`PERFORMANCE_TESTING_PLAN.md` for the underlying k6 concepts (VUs vs iterations, checks vs
thresholds, load shapes) if any of this is unfamiliar.

## Install

```bash
brew install k6
k6 version
```

## The four tiers — read this before running anything

Endpoints here span free public reads to real fills on a live shared UAT market — split into
tiers by blast radius so "load test" never accidentally means "spam real orders" or "open real
positions."

| Tier | Folder | What | Auth | Scale |
|---|---|---|---|---|
| **1 — safe** | `scripts/market-data/` | Public reads: `exchangeInfo`, `market-risk-tiers`, `orderbook`, `funding-rates` | None | smoke/load/stress, no cap |
| **2 — moderate** | `scripts/account-reads/` | Authenticated reads: account/balance/positions | JWT, refreshed | smoke/load, **capped at provisioned account count** |
| **3 — real money** | `scripts/order-placement/` | Places + cancels a real (never-filling) resting order | JWT, refreshed | smoke/load/stress, **capped at provisioned account count** |
| **4 — real fills** | `scripts/order-matching/` | Maker+taker pair actually crosses and fills, then flattens both sides | JWT, refreshed (×2 per pair) | **smoke only, on purpose** — see below |

**Start at Tier 1, always** — no provisioning, touches no state, cheapest possible
confirmation the target and script are both alive.

**Tier 4 is a different risk category from Tier 3, not just a bigger version of it.** Tier 3
orders are designed to never fill (10% off-mark) — zero position/PnL exposure at any scale.
Tier 4 orders are designed **to** fill (that's the point — testing the match path itself), so
real position/PnL exposure exists for the brief window between fill and flatten. Smoke-only (1
VU, 3 iterations) on purpose: new maker/taker-pairing + fill-confirm + flatten coordination
logic, unproven at scale — unlike Tier 3's placement/cancel, a known-good pattern already
proven in the pytest suites.

## Account provisioning (Tiers 2–4)

```
tools/arrange_perf_accounts.py (Python — k6 has no secp256k1/keccak, can't sign itself)
        │  mints wallets, signs with eth_account (same as tools/arrange_metamask_e2e.py)
        v
perf/data/accounts.json   (git-ignored — live JWTs, 60s access-token TTL)
        │
        v
k6 run  ──►  lib/auth.js: POST /auth/refresh (refresh_token, no signing needed)
        ──►  lib/accounts.js: accounts[(__VU - 1) % accounts.length] — one dedicated account per VU
```

```bash
python3 tools/arrange_perf_accounts.py                     # 5 subject accounts (default), no pairs
python3 tools/arrange_perf_accounts.py --count 20           # match target VU count (Tier 2/3)
python3 tools/arrange_perf_accounts.py --count 0 --pairs 1   # 1 maker+taker pair, no subjects (Tier 4)
```

Tier 4's maker+taker pairs are separate from Tier 2/3's `--count` subjects — provision only what
the tier you're about to run needs. Two things that make this different from a normal
login-pool pattern:
- **60s access-token TTL** — `accounts.json` goes stale in ~1 minute. Run the arrange script
  *immediately* before every Tier 2/3 run, never reuse an older copy (same rule as
  `e2e/.arrangement.json`).
- **`refresh_token` is single-use and rotates**, one account per VU, never shared. VU count over
  the provisioned account count → two VUs share an account, race on rotation, one gets a
  confusing 401. **Always provision ≥ peak VU target.**

## Safety rails

| Rail | Detail |
|---|---|
| Tier 1 by default | reach for Tier 2/3 only deliberately |
| Tier 2 VU count ≤ provisioned account count | see provisioning above |
| Tier 3 risk capped by design, not scale | orders rest 10% off-mark, never fill (mirrors `test_orders.py`) — zero position/PnL risk at any VU count. Only scaling risk: an orphaned resting order if its own cancel fails — `lib/orders.js` retries cancellation (idempotent, unlike placement). Targets `LINKUSDT-PERP` (idle, confirmed live-tradeable — see `CONVENTIONS.md`'s market allocation), never `BTCUSDT-PERP`/`ETHUSDT` or the liquidation tests' `SUIUSDT-PERP`/`DOGEUSDT-PERP` |
| Tier 4 risk real but bounded + short-lived | a fill means real position/PnL exposure for both sides — `order-matching/smoke.js` confirms the fill then immediately flattens both (reduce-only, retried — `lib/orders.js`'s `closePositionWithRetry`). Notional per fill deliberately tiny (`NOTIONAL_USD = 500`). Targets `BNBUSDT-PERP`, separate from Tier 3's `LINKUSDT-PERP` on purpose (real fills move price on a thin market, Tier 3's never-filling orders don't — tiers must never share a market). **Smoke-only for now**, until the pairing/fill-confirm/flatten coordination is proven at 1 VU |
| No `soak.js` for any tier yet | leak-hunting gap — see `METRICS_AND_MONITORING.md` §3 |
| Check `gh run list --status in_progress --status queued` before any run | same shared UAT env as pytest `@trades`/`@serial` and `e2e/` — concurrent traffic on a thin market corrupts both runs (same CI-vs-CI collision class as `CONVENTIONS.md`) |
| No retries anywhere | k6 doesn't retry by default — don't add any, same reasoning as `CONVENTIONS.md` §8 (a retried order is a doubled order) |
| During a confirmed UAT infrastructure outage | don't run anything against live UAT, not even Tier 1 reads — this suite should be ready the moment an outage is resolved, not adding load to a system being fixed |

## Running

**Always run through `perf/run.sh`, not bare `k6 run`** — it's the canonical entrypoint and the
only thing that gives you the automatic post-run analysis below.

```bash
# Tier 1 — no provisioning needed
perf/run.sh perf/scripts/market-data/smoke.js
perf/run.sh perf/scripts/market-data/load.js
perf/run.sh perf/scripts/market-data/stress.js

# Tier 2 — provision first (fresh every time, 60s TTL)
python3 tools/arrange_perf_accounts.py --count 10
perf/run.sh perf/scripts/account-reads/smoke.js
perf/run.sh perf/scripts/account-reads/load.js

# Tier 3 — provision first (fresh every time, 60s TTL)
python3 tools/arrange_perf_accounts.py --count 2
perf/run.sh perf/scripts/order-placement/smoke.js

python3 tools/arrange_perf_accounts.py --count 5   # match load.js's peak VU target
perf/run.sh perf/scripts/order-placement/load.js

python3 tools/arrange_perf_accounts.py --count 15  # match stress.js's peak VU target
perf/run.sh perf/scripts/order-placement/stress.js

# Tier 4 — provision a maker+taker pair first (fresh every time, 60s TTL)
python3 tools/arrange_perf_accounts.py --count 0 --pairs 1
perf/run.sh perf/scripts/order-matching/smoke.js
```

Any extra args after the script path pass straight through to `k6 run` — e.g. override the
target market with `PERF_MARKET` (Tier 1 defaults to `BTCUSDT-PERP`; Tier 2/3 use whatever
`--market` the arrange script wrote into `accounts.json`, default `LINKUSDT-PERP`):

```bash
perf/run.sh perf/scripts/market-data/smoke.js -e PERF_MARKET=ETHUSDT-PERP
```

`NIMBUS_UAT_TRADING_BASE`/`NIMBUS_UAT_AUTH_BASE` etc. must be in your shell environment — the
same `.env` the Python side already uses:

```bash
export $(grep -v '^#' .env | xargs)
```

## What you get after every run, automatically

`perf/run.sh` captures raw request output (`--out json=...` → `perf/results/<script>-<timestamp>.ndjson`,
git-ignored) and runs two reports on top of k6's own terminal summary (per-check pass/fail %,
`http_req_duration` percentiles, `http_req_failed` rate, non-zero exit on any failed
`thresholds` — `echo $?` is the real alert):

| Tool | Gives you |
|---|---|
| `tools/status_code_breakdown.sh` | every distinct HTTP status code seen — k6's own summary only breaks out values a threshold references, so a stray 400/429/503 otherwise stays invisible |
| `tools/time_trend_report.py` | first-half vs second-half comparison (p95 creeping up? fail rate rising?) — the question `stress`/`load` exists to answer, hidden by one averaged summary line. Flags itself low-confidence on smoke-scale samples |

Both adapted from the practice repo's `scripts/analyze-time-trend.sh` /
`scripts/count-status-codes.sh`, wired to run automatically instead of needing a separate call.

**See `METRICS_AND_MONITORING.md`** for the full picture: every metric explained, what to watch
server-side if you have backend visibility, and which tier surfaces which failure mode.

## Visualizing results

No `docker-compose.yml`/Grafana stack is duplicated into this repo — reuse the one already set
up at `/Users/pk/Documents/LearningQA/performance/docker-compose.yml`:

```bash
cd /Users/pk/Documents/LearningQA/performance && docker compose up -d
cd -   # back to neodax-python
perf/run.sh perf/scripts/market-data/load.js --out experimental-prometheus-rw
# k6 accepts multiple --out flags -- this streams to Prometheus AND still writes run.sh's own
# ndjson file for the automatic analysis above. open http://localhost:3000 (admin/admin) ->
# Dashboards -> "k6 Prometheus"
```

## Writing up results

Copy the practice repo's `RESULTS.md` template pattern if you run this for real: what load it
handled, where a tier started degrading, one concrete recommendation. A performance test nobody
reads the results of is wasted effort.
