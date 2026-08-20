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

This repo's endpoints span a much wider risk range than a public sandbox API: some are free
public reads, some place real orders on a live shared UAT market, and one actually fills those
orders. The suite is split into tiers so "load test" never accidentally means "spam real orders"
or "open real positions."

| Tier | Folder | What | Auth | Scale |
|---|---|---|---|---|
| **1 — safe** | `scripts/market-data/` | Public reads: `exchangeInfo`, `market-risk-tiers`, `orderbook`, `funding-rates` | None | smoke/load/stress, no cap |
| **2 — moderate** | `scripts/account-reads/` | Authenticated reads: account/balance/positions | JWT, refreshed | smoke/load, **capped at provisioned account count** |
| **3 — real money** | `scripts/order-placement/` | Places + cancels a real (never-filling) resting order | JWT, refreshed | smoke/load/stress, **capped at provisioned account count** |
| **4 — real fills** | `scripts/order-matching/` | Maker+taker pair actually crosses and fills, then flattens both sides | JWT, refreshed (×2 per pair) | **smoke only, on purpose** — see below |

**Start at Tier 1, always.** It needs no provisioning, touches no state, and is the cheapest
possible confirmation the target and script are both alive — the same role
`test-types/smoke-test.js` plays in the practice repo.

**Tier 4 is a different risk category from Tier 3, not just a bigger version of it.** Tier 3's
orders are designed to never fill (10% off-mark) — zero position/PnL exposure at any scale.
Tier 4 orders are designed **to** fill — that's the point, it's testing the match path itself —
which means real position/PnL exposure exists for the (brief, immediately-flattened) window
between fill and flatten. It starts smoke-only (1 VU, 3 iterations) deliberately: this is new
maker/taker-pairing + fill-confirm + flatten coordination logic that hasn't been proven at any
scale yet, unlike Tier 3's placement/cancel which was already a known-good pattern from the
pytest suites before this script existed.

## Why Tiers 2/3 need their own account provisioning

k6's embedded JS runtime has no secp256k1/keccak library, so it can't mint a session itself —
this app's auth needs a real wallet signature (`eth_account`, same as
`tools/arrange_metamask_e2e.py`). Minting stays a Python-side job:

```bash
python3 tools/arrange_perf_accounts.py                        # 5 subject accounts (default), no pairs
python3 tools/arrange_perf_accounts.py --count 20              # match your target VU count (Tier 2/3)
python3 tools/arrange_perf_accounts.py --count 0 --pairs 1      # 1 maker+taker pair, no subjects (Tier 4)
```

This writes `perf/data/accounts.json` (git-ignored — it holds live JWTs). k6 only ever calls
`POST /auth/refresh` with an already-valid `refresh_token` from there on (`lib/auth.js`) — pure
JSON, no signing needed. Tier 4's maker+taker pairs are separate accounts from Tier 2/3's
`--count` subjects — provision only what the tier you're about to run actually needs.

**Two things that make this different from a normal login-pool pattern:**
- **Access tokens expire in 60s.** `perf/data/accounts.json` goes stale in about a minute —
  run the arrange script *immediately* before every Tier 2/3 k6 run, never reuse a copy from an
  earlier session (same rule as `e2e/.arrangement.json`).
- **refresh_token is single-use and rotates.** `lib/accounts.js` assigns exactly one dedicated
  account per VU (`accounts[(__VU - 1) % accounts.length]`) — never shared. If your VU count
  exceeds the provisioned account count, two VUs will eventually share an account and race on
  rotating its refresh_token, breaking one of them with a confusing 401. **Always provision at
  least as many accounts as your peak VU target.**

## Safety rails

- **Tier 1 by default.** Only reach for Tier 2/3 deliberately.
- **Tier 2 VU count ≤ provisioned account count.** See above.
- **Tier 3's real-money risk is capped by design, not by scale.** Every order rests 10% off-mark
  and never fills (mirrors `test_orders.py`) — no position/PnL risk at any VU count. The one
  thing that scales with load is a resting order getting orphaned on the shared book if its own
  cancel fails; `lib/orders.js` retries cancellation (idempotent, unlike placement — see its own
  comment) specifically to keep that safe under `load.js`/`stress.js`. It targets
  `LINKUSDT-PERP` (idle, confirmed live-tradeable this session — see `CONVENTIONS.md`'s UAT
  market allocation), never the shared default `BTCUSDT-PERP`/`ETHUSDT` or the liquidation
  tests' `SUIUSDT-PERP`/`DOGEUSDT-PERP`.
- **Tier 4's risk is real but bounded and short-lived, not eliminated.** A fill means real
  position/PnL exposure exists for both sides — `scripts/order-matching/smoke.js` confirms the
  fill, then immediately flattens both sides with a reduce-only order that retries (idempotent,
  same reasoning as Tier 3's cancel — see `lib/orders.js`'s `closePositionWithRetry`). Notional
  per fill is deliberately tiny (`NOTIONAL_USD = 500`) to minimize price impact on a thin market.
  It targets `BNBUSDT-PERP` — separate from Tier 3's `LINKUSDT-PERP` on purpose, since real fills
  move price on a thin market and Tier 3's never-filling orders don't; the two tiers should never
  share a market. **Smoke-only for now** — no `load.js`/`stress.js` exists for Tier 4 yet, by
  explicit choice, until the maker/taker-pairing + fill-confirm + flatten coordination has been
  proven at 1 VU first.
- **No `soak.js` for any tier, yet.** Long-duration leak-hunting is a real gap in this suite
  right now — see `METRICS_AND_MONITORING.md` §3.
- **Check `gh run list --status in_progress --status queued` before any run.** This hits the
  same shared UAT environment as the pytest `@trades`/`@serial` lanes and `e2e/` — concurrent
  live traffic against a thin shared market corrupts both runs, exactly like the CI-vs-CI
  collisions documented in `CONVENTIONS.md`.
- **No retries anywhere.** k6 doesn't retry by default — don't add any. Same reasoning as
  `CONVENTIONS.md` §8: a retried order is a doubled order.
- **Right now, during the confirmed UAT infrastructure outage: don't execute anything against
  live UAT yet — not even Tier 1 reads.** This suite exists to be ready the moment the outage is
  confirmed resolved, not to add load to a system already being fixed.

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

`perf/run.sh` always captures raw request-level output (`--out json=...` to
`perf/results/<script>-<timestamp>.ndjson`, git-ignored, kept for later re-analysis) and runs
two reports against it after k6 finishes — on top of k6's own terminal summary (per-check
pass/fail %, `http_req_duration` percentiles, `http_req_failed` rate, and a non-zero exit code
if any `thresholds` fail — that exit code is the real "alert," `echo $?` after any run):

- **`tools/status_code_breakdown.sh`** — every distinct HTTP status code actually seen, not
  just 200-vs-not. k6 only keeps a per-tag breakdown for values a threshold references, so a
  stray 400/429/503 silently never appears in the plain summary otherwise.
- **`tools/time_trend_report.py`** — first-half vs second-half comparison of the run: is p95
  creeping up, is the fail rate rising later on. The question a `stress`/`load` run exists to
  answer, which one averaged summary line hides by design. Flags itself as low-confidence on
  small (smoke-scale) samples rather than pretending to a trend that isn't really there.

Both are adapted from the practice repo's `scripts/analyze-time-trend.sh` /
`scripts/count-status-codes.sh` — same logic, wired to run automatically instead of needing a
separate manual invocation.

**See `METRICS_AND_MONITORING.md`** for the full picture: every metric in the table above
explained, plus what to watch server-side (CPU, RAM/leaks, connection pools — no GPU, this
stack doesn't have one) if you have any visibility into the backend under test, and which tier
is good at surfacing which failure mode.

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
