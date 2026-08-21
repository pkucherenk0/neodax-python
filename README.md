# nimbus-python

[![pr-check](https://github.com/pkucherenk0/neodax-python/actions/workflows/pr-check.yml/badge.svg)](https://github.com/pkucherenk0/neodax-python/actions/workflows/pr-check.yml)
[![perp-spot-tests](https://github.com/pkucherenk0/neodax-python/actions/workflows/ci.yml/badge.svg)](https://github.com/pkucherenk0/neodax-python/actions/workflows/ci.yml)

A test-automation portfolio project: three layers of testing against a live (throwaway-UAT)
crypto perpetuals/spot trading platform (anonymized for public sharing — real name/domain
scrubbed).

**Highlights, for a fast skim:**
| | |
|---|---|
| Contract-first API testing | every response validated against a pydantic schema (`lib/schemas.py`), not just status codes. domain math (fees, tiers, liquidation pricing) lives in framework-agnostic `lib/`, unit-tested offline (`tests/unit/`). |
| Safety-railed live-money testing | real orders, hard rails: no retries ever on placement (retry = doubled volume), trading lanes opt-in, ordered/stateful flows isolated via `xdist_group`. See `CONVENTIONS.md`. |
| Anti-false-positive discipline | `tools/red_green.py` proves a test can go red before trusting it green; `tools/api_coverage.py` diffs BE endpoint registry vs test coverage. See `CONVENTIONS.md` §13. |
| Real UI e2e (`e2e/`) | separate pytest + Playwright project, POM-structured, drives the real FE through a mock EIP-1193 wallet (real signatures, no browser extension, no Node). |
| k6 performance suite (`perf/`) | manual-only (never in CI), tiered by blast radius (public reads → authed reads → never-filling orders → real fills), automatic post-run analysis. See `perf/README.md`. |
| CI-integrated | `pr-check.yml` (required PR gate) + `ci.yml` (full lane every push), parallelized via `pytest-xdist`. |

Three layers of the harness itself:
- **API integration** (`suites/`, `tests/unit/`) — pytest + Playwright in **API mode** (no
  browser), response-shape validated via [pydantic](https://docs.pydantic.dev). Domain logic in
  `lib/`; specs are thin **Arrange → Act → Assert** wrappers.
- **UI e2e** (`e2e/`) — separate pytest + Playwright project (own venv, own `pytest.ini`), real
  browser + own deps so it lives outside the main suite. Mock EIP-1193 wallet; page objects in
  `pages/`, shared modals in `components/`. See `e2e/README.md` / `AGENTS.md`.
- **Performance** (`perf/`) — separate k6 project, manual-only, **never wired into CI**. See
  `perf/README.md`.

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

cd e2e && python3 -m venv .venv && source .venv/bin/activate \
  && pip install -r requirements.txt && playwright install chromium && pytest  # UI e2e (separate project)
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
- `serial` — ordered flows (fee-tier, position lifecycle, liquidation); xdist-safe, ordered
  classes carry `@pytest.mark.xdist_group` (`-n N --dist loadgroup`).

UI e2e tests (mock wallet, real FE) are a separate pytest project with its own venv — see
`e2e/`, not a marker in this one.

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
suites/       competition/ + nimbus/{perp,spot}/ ; TEMPLATE_template.py to copy
tests/unit/   offline unit tests for pure lib math (run first)
tools/        red_green.py (anti-false-positive) + api_coverage.py (endpoint registry × tests)
              + arrange_metamask_e2e.py (funds e2e/ accounts) + arrange_perf_accounts.py
              (funds perf/ accounts)

e2e/          SEPARATE pytest + Playwright project (own venv) — UI e2e via a mock EIP-1193
              wallet, no MetaMask extension, no Node. pages/ + components/ + lib/ + tests/.
              `cd e2e && pytest`. See e2e/README.md.

perf/         SEPARATE k6 project — load/perf testing, manual only, NEVER wired into CI. Four
              safety tiers by blast radius. See perf/README.md.

docs/test-cases/   per-topic grug test-case lists (health/perps/spot/competition/e2e), indexed
                   by TEST_CASES.md — the case-level detail behind the suites/ above.
results/      per-invocation run output (git-ignored): results/runs/<runId>/, results/latest
              symlinks the newest. detailed-report.md (human) + detailed/<test>.json (agents).
.github/workflows/  pr-check.yml (required PR gate) + ci.yml (full lane, push to main).

CONVENTIONS.md    the fixed, enforced test-writing rules — read before writing any test.
AGENTS.md         entry guide for AI agents and contributors.
TEST_STRATEGY.md  where each test layer lives + how CI wires across the BE/FE/this repo.
TEST_CASES.md     index into docs/test-cases/ — add a row when you add a test.
```

## Contributing

- **[`CONVENTIONS.md`](./CONVENTIONS.md)** — the fixed, enforced test-writing rules. Read first.
- **[`AGENTS.md`](./AGENTS.md)** — entry guide for AI agents and contributors.
- Copy `suites/TEMPLATE_template.py` to start a new test.
