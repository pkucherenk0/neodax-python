# nimbus-python — agent & contributor guide

API/integration test harness for Nimbus. pytest + Playwright in **API mode** (no browser).
Domain logic lives in `lib/`; specs are thin arrange/act/assert wrappers.

## Run
```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
pytest tests/unit                        # OFFLINE units — run first, verifies lib math
pytest                                   # safe default: stateless + smoke (hits live UAT, no funds)
pytest -m smoke                          # env connectivity check (fail fast)
pytest -m trades                         # LIVE ORDERS — deliberate only
pytest -m serial suites/<one-file>.py    # ordered flows — ONE process, never -n
pytest --collect-only -q                 # discover tests without running
python tools/api_coverage.py           # BE endpoint registry × tests: coverage + drift
python tools/red_green.py <file> -k "name"   # prove a test can fail (CONVENTIONS §13)
```

## UI e2e (separate Node project — not pytest)
`e2e/` drives the real FE through a mock EIP-1193 wallet (`@johanneskares/wallet-mock`, real
signatures, no browser extension) — plain JWT session injection can't get past the FE's
wallet-connect gate (order buttons stay disabled, Open Orders/Positions panels stay locked, even
with a valid JWT); it needs a wallet wagmi/AppKit actually recognizes as connected. See
`e2e/lib/wallet.ts` for why and how; `e2e/lib/actions.ts` for the reusable, named UI actions.
```bash
cd e2e && npm install && npx playwright install chromium && npx playwright test
```

## ⚠️ Safety rails — READ BEFORE RUNNING OR EDITING
- Tests hit **live environments and spend real balance.** There is no isolated "test" env.
- **`uat`** = fresh auto-funded wallets. **`stage`** = a FIXED pre-funded pool
  (`configs/accounts.stage.json`, git-ignored) — no faucet.
- **Never add retries/reruns to any `trades` test.** A retry re-places live orders → double
  volume / lost funds. pytest has no retries by default; keep it that way.
- The default run excludes `trades`/`serial` (pytest.ini addopts); nothing dangerous
  runs by default. Overriding `-m` is a deliberate act.
- Secrets: never printed/committed. EXCEPTION: minted **UAT throwaway** wallet keys are saved
  to git-ignored `results/runs/<runId>/artifacts/` (CONVENTIONS §12) — uat only, never stage.

## Test taxonomy (markers — select with -m)
- `stateless` — independent, parallel-safe, no funding. **Start here**; these are the templates.
- `smoke`     — connectivity/health, fail-fast.
- `trades`    — places real orders; run deliberately.
- `serial`    — ordered flows (fee-tier, lifecycle, liquidation); module state, xdist-safe via
  `@pytest.mark.xdist_group` (`-n N --dist loadgroup`).

UI e2e tests live in the separate `e2e/` Node project (see above), not as a pytest marker here.

## Where things live
- `CONVENTIONS.md` — **the fixed test-writing contract. Read before writing any test.**
  §11 = caveman comment rule. §13 = anti-false-positive rules.
- `TEST_CASES.md` — index into per-topic lists under `docs/test-cases/`. Add a row when you
  add a test.
- `fixtures/`     — ALL fixtures, split by concern (`clients.py`, `accounts.py`, `reporting.py`):
  `env`, `fresh_wallet`, `account`, `spot_maker`, `perp_maker`,
  `new_funded_account`. Get clients/accounts from fixtures, never ad-hoc. Also the detailed
  reporter (per-test actions/checks/records → `results/runs/<runId>/detailed-report.md`).
- `lib/`          — pure, framework-agnostic domain logic (fees, tiers, sizing). Compose from here.
- `lib/types.py`  — canonical shapes. **Read first.**
- `lib/schemas.py`— pydantic response contracts. Validate response SHAPE, not just status.
- `lib/validate.py` — `parsed_json(res, Schema)`: typed body + readable error on contract drift.
- `lib/poll.py`   — `poll_until(...)`: bounded condition polling. NEVER `time.sleep` in specs.
- `lib/report.py` — `step()` (timed action), `record()` (JSON), `record_check()` — enrich the
  report; they never affect pass/fail. `assert` stays the gate.
- `lib/artifacts.py` — saves minted creds + order/trade ids (auto; CONVENTIONS §12).
- `configs/`       — typed run params (competition slug, sizing knobs). No CLI-flag archaeology.
- `suites/TEMPLATE_template.py` — copy this to add a test.

### Test isolation & shared accounts (read before adding trades tests)
- `account`/`spot_maker`/`perp_maker` are **session-scoped** (= per xdist worker process):
  created once, lazily, and **shared across every test file that process runs**. So `trades`
  tests are NOT fully isolated — a shared account accumulates volume and open positions across
  files. Assertions must not assume a pristine account (assert DELTAS, not absolutes; don't
  assert "drove volume > 0" — the account may already be past a threshold). For full isolation
  use `fresh_wallet` (unfunded) or `new_funded_account` (funded, disposable).
- **Teardown positions you open.** Add a module fixture calling `flatten_perp_pair(...)` /
  `close_all_perp_positions(...)` so positions don't accumulate margin / risk liquidation on
  the shared account. The volume driver already round-trips flat.

## Adding a test — the fixed approach
**Read `CONVENTIONS.md` first.** It is the authoritative, non-negotiable spec.

1. Copy `suites/TEMPLATE_template.py` → `suites/<domain>/test_<feature>.py`
   (reference: `suites/competition/test_enrollment.py`).
2. Get everything from conftest fixtures — never build clients/wallets inline.
3. Structure the body **arrange → act → assert** (comment-labeled). Every test asserts.
4. Mark the class with one lane: `stateless` (default) | `trades` | `serial`.
5. No `time.sleep` — `poll_until` a condition with a timeout. No branching in tests.
6. Run `pytest --collect-only` + `pytest tests/unit` before committing.

## Output
- Per invocation everything groups under `results/runs/<runId>/` (`run-NNNN-<ts>`);
  `results/latest` → newest.
- Humans: `results/latest/detailed-report.md` (per-test actions/checks/records).
  Agents/CI: `results/latest/detailed/<test>.json`.

## Status
lib (15 modules), fixtures/ (split by concern), 20 suites (health ×2 -- env reachability +
faucet canary, competition ×7, nimbus perp ×8, nimbus spot ×3), offline unit tests, red-green
+ api-coverage tools, and a real UI
e2e suite (`e2e/`, separate Node project — mock EIP-1193 wallet, not session injection or a
real MetaMask extension). Live suites have been run against UAT from this repo (safe/trades/serial lanes
all green). CI is wired: `.github/workflows/ci.yml` (full lane, push) and
`.github/workflows/pr-check.yml` (fast safe-lane, required PR check). Mutation testing (mutmut)
is not wired yet.
