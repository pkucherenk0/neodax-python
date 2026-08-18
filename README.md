# nimbus-python

Two-layer test harness for Nimbus:
- **API integration** (`suites/`, `tests/unit/`) — **pytest + Playwright in API mode** (no
  browser), with response-shape validation via [pydantic](https://docs.pydantic.dev). Domain
  logic lives in `lib/`; specs are thin **Arrange → Act → Assert** wrappers.
- **UI e2e** (`e2e/`) — a separate Node/Playwright project driving the real FE through a real
  MetaMask wallet (dappwright). Lives outside the Python suite because dappwright (real
  extension automation) only exists in Node. See `e2e/README` / `AGENTS.md` for why and how.

> ⚠️ **These tests hit live environments and can spend real balance.** Read the safety rails
> below and in [`AGENTS.md`](./AGENTS.md) before running anything that trades.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env             # optional — defaults target UAT public endpoints
```

## Running tests

```bash
pytest                                        # SAFE DEFAULT: stateless + smoke + offline units
pytest tests/unit                             # offline unit tests only (no network at all)
pytest -m smoke                               # env connectivity check (fail fast)
pytest -m trades                              # everything that places real orders — DELIBERATE
pytest -m serial suites/competition/test_perp_fee_tier.py   # ordered fee-tier flow, ONE process
pytest --env=stage ...                        # target stage instead of uat
pytest -n 2 -m stateless                      # parallel via xdist (keep workers modest — live BE)

cd e2e && npm install && npx playwright install chromium && npx playwright test  # UI e2e (separate project)
```

Env is chosen with `--env`:

| `--env` | Wallets | Faucet |
|---|---|---|
| `uat` (default) | fresh, auto-funded | yes |
| `stage` | fixed pre-funded pool (`configs/accounts.stage.json`) | no |

## Safety rails (read before running trading suites)

- **The default `pytest` run excludes `trades`/`serial`** (see `pytest.ini` addopts). Trading
  lanes are opt-in via `-m`.
- There are **no retries** and none may ever be added on `trades`/`serial` — a retry re-places
  live orders (double volume / lost funds). Never install pytest-rerunfailures here.
- `serial` suites share module state between ordered phases: run them in ONE process (no `-n`).
- Secrets never touch disk: private keys stay in memory; `.env` and `configs/accounts.stage.json`
  are git-ignored. EXCEPTION: minted **UAT throwaway** wallet keys are saved to git-ignored
  `results/runs/<runId>/artifacts/` (CONVENTIONS §12) — uat only, never stage.

## Test lanes (markers)

- `stateless` — independent, parallel-safe, no funding. **Default; prefer this.**
- `smoke` — connectivity/health, fail-fast.
- `trades` — places real orders; no retries ever.
- `serial` — ordered flows (fee-tier, position lifecycle, liquidation); one process.

UI e2e tests (real MetaMask, real FE) are a separate Node project — see `e2e/`, not a pytest marker.

## Test validity — making sure a test can actually FAIL (anti-false-positive)

A green test proves nothing until you've seen it go red. See **[`CONVENTIONS.md`](./CONVENTIONS.md) §13**;
the tooling:

```bash
pytest tests/unit                                   # fast OFFLINE unit tests for pure lib logic
python tools/red_green.py suites/<file>.py -k "name"   # prove a spec can fail: corrupt its
                                                    # expected values, confirm it goes RED.
                                                    # trades/serial need --force (places live orders)
python tools/api_coverage.py                      # BE endpoint registry × tests: coverage + drift
# mutation testing: `mutmut` against lib/ — not yet wired.
```

- **Independent oracle:** expected values come from the BE code / spec / pydantic contract /
  the *charged* value — never the model's own guess.
- **No vacuous asserts:** no `assert True`, `status < 500`, `> 0` on an always-positive value,
  or wide bands that hide drift. Assert the specific value/code.
- **Read the evidence:** green ≠ correct — check `results/latest/detailed-report.md` for what
  actually happened (each run lives in `results/runs/<runId>/`).

## Layout

```
lib/          domain logic (framework-agnostic) + types.py + schemas.py (pydantic) + validate.py
fixtures/     env / fresh_wallet / account / spot_maker / perp_maker / new_funded_account
              — the ONLY way specs get clients & accounts. + detailed reporter hooks.
conftest.py   wires fixtures/ into pytest (pytest_plugins) + --env CLI option
configs/      typed run params (no CLI-flag archaeology)
suites/       competition/ + nimbus/ ; TEMPLATE_template.py to copy
tests/unit/   offline unit tests for pure lib math (run first)
tools/        red_green.py (anti-false-positive) + api_coverage.py (endpoint registry × tests)
              + arrange_metamask_e2e.py (funds accounts for e2e/, see below)

e2e/          SEPARATE Node/Playwright project — UI e2e via a real MetaMask wallet (dappwright).
              lib/metamask.ts (connect/approve), lib/actions.ts (named page actions), tests/.
              Not pytest — `cd e2e && npx playwright test`. See e2e/README or AGENTS.md.
```

## Contributing

- **[`CONVENTIONS.md`](./CONVENTIONS.md)** — the fixed, enforced test-writing rules. Read first.
- **[`AGENTS.md`](./AGENTS.md)** — entry guide for AI agents and contributors.
- Copy `suites/TEMPLATE_template.py` to start a new test.
