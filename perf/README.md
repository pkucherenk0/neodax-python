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

## The three tiers — read this before running anything

This repo's endpoints span a much wider risk range than a public sandbox API: some are free
public reads, some place real orders on a live shared UAT market. The suite is split into three
tiers so "load test" never accidentally means "spam real orders."

| Tier | Folder | What | Auth | Scale |
|---|---|---|---|---|
| **1 — safe** | `scripts/market-data/` | Public reads: `exchangeInfo`, `market-risk-tiers`, `orderbook`, `funding-rates` | None | smoke/load/stress, no cap |
| **2 — moderate** | `scripts/account-reads/` | Authenticated reads: account/balance/positions | JWT, refreshed | smoke/load, **capped at provisioned account count** |
| **3 — real money** | `scripts/order-placement/` | Places + cancels a real (never-filling) resting order | JWT, refreshed | **smoke only, always** |

**Start at Tier 1, always.** It needs no provisioning, touches no state, and is the cheapest
possible confirmation the target and script are both alive — the same role
`test-types/smoke-test.js` plays in the practice repo.

## Why Tiers 2/3 need their own account provisioning

k6's embedded JS runtime has no secp256k1/keccak library, so it can't mint a session itself —
this app's auth needs a real wallet signature (`eth_account`, same as
`tools/arrange_metamask_e2e.py`). Minting stays a Python-side job:

```bash
python3 tools/arrange_perf_accounts.py            # 5 subject accounts + 1 maker (defaults)
python3 tools/arrange_perf_accounts.py --count 20 # match your target VU count
```

This writes `perf/data/accounts.json` (git-ignored — it holds live JWTs). k6 only ever calls
`POST /auth/refresh` with an already-valid `refresh_token` from there on (`lib/auth.js`) — pure
JSON, no signing needed.

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
- **Tier 3 is smoke-only, on purpose — no load/stress/soak script exists for order placement.**
  It targets `LINKUSDT-PERP` (idle, confirmed live-tradeable this session — see
  `CONVENTIONS.md`'s UAT market allocation), never the shared default `BTCUSDT-PERP`/`ETHUSDT`
  or the liquidation tests' `SUIUSDT-PERP`/`DOGEUSDT-PERP`. If you ever add heavier
  order-placement load, pick a different idle market from that same list and update it there.
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

```bash
# Tier 1 — no provisioning needed
k6 run perf/scripts/market-data/smoke.js
k6 run perf/scripts/market-data/load.js
k6 run perf/scripts/market-data/stress.js

# Tier 2 — provision first (fresh every time, 60s TTL)
python3 tools/arrange_perf_accounts.py --count 10
k6 run perf/scripts/account-reads/smoke.js
k6 run perf/scripts/account-reads/load.js

# Tier 3 — provision first, smoke only
python3 tools/arrange_perf_accounts.py --count 2
k6 run perf/scripts/order-placement/smoke.js
```

Override the target market with `PERF_MARKET` (Tier 1 defaults to `BTCUSDT-PERP`; Tier 2/3 use
whatever `--market` the arrange script wrote into `accounts.json`, default `LINKUSDT-PERP`):

```bash
PERF_MARKET=ETHUSDT-PERP k6 run perf/scripts/market-data/smoke.js
```

`NIMBUS_UAT_TRADING_BASE`/`NIMBUS_UAT_AUTH_BASE` etc. must be in your shell environment — the
same `.env` the Python side already uses:

```bash
export $(grep -v '^#' .env | xargs)
```

## Visualizing results

No `docker-compose.yml`/Grafana stack is duplicated into this repo — reuse the one already set
up at `/Users/pk/Documents/LearningQA/performance/docker-compose.yml`:

```bash
cd /Users/pk/Documents/LearningQA/performance && docker compose up -d
cd -   # back to neodax-python
k6 run --out experimental-prometheus-rw perf/scripts/market-data/load.js
# open http://localhost:3000 (admin/admin) -> Dashboards -> "k6 Prometheus"
```

## Writing up results

Copy the practice repo's `RESULTS.md` template pattern if you run this for real: what load it
handled, where a tier started degrading, one concrete recommendation. A performance test nobody
reads the results of is wasted effort.
