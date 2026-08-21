# Test strategy — where tests live, what they cover, how CI wires across repos

Context: **three repos, deployed independently to shared envs (uat / stage).**

- **BE repo** — the trading / fee-engine / competition services.
- **FE repo** — `web-hub` (Next.js / React).
- **This repo** (`nimbus-python`) — a **standalone black-box test harness** that targets a *deployed*
  environment (uat/stage), not a local build.

The question this doc answers: given separate BE/FE repos, where should each kind of test live, and
how do they run in CI so a change in one repo is actually gated?

---

## 1. The layering (test pyramid across repos)

Each layer catches a **different failure mode**. Don't make one layer do another's job.

| Layer | Lives in | Runs against | Owns | Speed |
|---|---|---|---|---|
| Unit | BE repo / FE repo | in-process | pure logic, components | ms |
| Component / integration | BE repo | local/ephemeral BE | one service + its DB, endpoint shape | seconds |
| FE component | FE repo | mocked API (MSW / route mocks) | rendering, wiring, states — no real BE | seconds |
| **API / contract E2E** | **this repo** | **deployed uat/stage** | **cross-service business logic** (fee engine, overlay, tiers) | minutes |
| **FE-integrated E2E** | **this repo** (new browser layer) | **deployed uat/stage** | **the UI reflects what the API computes**; critical journeys | minutes |

Rule of thumb: **invariants & edge cases → API layer (here). User-visible behavior → FE-integrated
layer (here). FE-only rendering → FE repo with a mocked API.**

### Why cross-service E2E lives HERE, not in BE or FE
A full-stack test needs BE **and** FE both deployed and talking. Neither single repo can stand that
up in its own CI. A standalone repo pointed at a deployed env is the natural, honest home for it —
and it's env-agnostic (uat/stage swap via `--project`), which a repo coupled to one service's build
is not.

---

## 2. FE-integrated E2E — the cheap, correct shape

**Do not re-test fee math through the browser** — driving volume/NIM by clicking is slow,
flaky, and duplicates what the API layer already proves. Instead, reuse this repo's
`fixtures/` + `lib/` as the *arrange* engine, drive the browser only for the last-mile assert:

```
API-arrange (existing helpers):  enroll + faucet + transfer + driveCompetitionVolume → VIP1
        ↓
Browser (Playwright):            log in, open the fees/competition page
        ↓
Assert:                          the UI renders the discounted 8bps / VIP1 tier
```

Playwright does both `APIRequestContext` and browser in one project — additive, new browser
projects alongside the existing `uat`/`stage` API projects, sharing fixtures. Keep the browser
set small and journey-focused (enroll, see discount, place order, see charged fee); everything
combinatorial stays in the API layer.

### Auth is the real hurdle (web-hub uses wallet-connect)
The API harness signs challenges directly; a browser can't click through a wallet extension
easily. Two options:

| Option | How | Trade-off |
|---|---|---|
| 1. Seed session via API, inject into browser (used for most journeys) | reuse the auth fixture's `access_token`, set it in browser context (localStorage/cookie) | skips login UI — accepted, since the one connect test below covers it |
| 2. Injected test provider (one connect test only) | synthetic `window.ethereum` backed by a test key, exercises the real connect flow | slower, only needed to keep login itself from being a coverage hole |

---

## 3. Cross-repo CI wiring (the multi-repo crux)

This repo can't be triggered by BE/FE **code** alone — it tests **deployments**. Wire it on
deploy events and schedules, not on push:

1. **Post-deploy trigger (primary).** BE/FE deploy workflow fires `repository_dispatch` (or
   `workflow_dispatch`) into this repo's Actions with the target env. BE deploy → API/contract
   suite + FE-integrated smoke. FE deploy → FE-integrated suite + fast API smoke.
2. **Report back** — commit status/check to the triggering repo (GitHub API or Slack/dashboard),
   so a bad deploy is visible where the change was made.
3. **Nightly** full run against uat (+ stage) — catches drift and flake.
4. **Manual dispatch** for release gating before promoting an env.

### Guard the contract *between* the repos (so you're not relying on slow E2E)
Separate repos drift silently — add a fast, cheap contract guard so a BE shape change fails
before the full E2E:
- **OpenAPI as source of truth** — BE publishes it, this repo's `lib/schemas.py` (pydantic) and
  the FE's client validate against it. A schema change becomes a visible diff/PR.
- **Or consumer-driven contract tests (Pact)** — FE (consumer) publishes expectations, BE
  (provider) verifies in its own CI. Heavier, decouples the repos properly.

Minimum viable: pydantic schemas here ARE the shared contract already — a schema-validation
failure in the E2E run already reads as "contract drift" (`parsed_json` fails loudly).

---

## 4. What runs when

| Trigger | Suite | Env |
|---|---|---|
| BE PR | BE unit + component (in BE repo) | ephemeral/local |
| FE PR | FE unit + component w/ mocked API (in FE repo) | none |
| BE deploy → uat | API/contract E2E + FE-integrated smoke (`@smoke`) | uat |
| FE deploy → uat | FE-integrated E2E + API `@smoke` | uat |
| Nightly | full API + FE-integrated | uat (+ stage) |
| Release gate | full suite, manual dispatch | stage → prod-candidate |

Note the existing safety rails still apply: `@trades`/`@serial` place **real orders and spend real
balance**, `retries` stays 0, and funded worker fixtures cost faucet credits per worker (see
`AGENTS.md`). Keep the fast `@stateless`/`@smoke` subset as the per-deploy gate; run the heavy
`@trades`/`@serial` and browser sets on a smaller cadence (post-deploy of the relevant service +
nightly), not on every push.

---

## 4b. The two maps (coverage + traceability)

Two inventories, each **anchored to a source of truth** (never hand-maintained lists — those rot):

**API map — DONE.** `configs/api-endpoints.json` is the registry of BE routes in scope
(competition fee-engine + the trading/auth/faucet flows feeding it), extracted from the BE
repos (`nimbus`, `web-hub` — both Gin; see `generatedFrom` + `excludedAreas`).
`python tools/api_coverage.py` cross-references it against endpoints the tests actually call
(scanned from `lib/`/`fixtures/`/`suites/`), prints per-service coverage + two drift signals:
registry endpoints with no test (**coverage gap**), and paths referenced in tests but not in the
registry (**stale map / typo**).

Baseline: **18/47 (38%)** — full on the flows exercised, gaps in order cancellation, positions
read, leverage, rankings, `/me`, broadcast, internal fee-overlay. Anti-drift: regenerate the
registry when BE route files change (`generatedFrom` pointers say where); `lib/schemas.py`
already guards request/response *shape*, so the map only tracks *surface*, not payloads.

**FE map — SUPERSEDED by a real UI e2e suite.** Original plan (POM via session injection +
testid-contract audit) retired: the FE's wallet-connect gate (Reown AppKit + wagmi) can't be
satisfied by session injection alone — Open Long/Short stays `disabled`, Open Orders/Positions
show "Connect Wallet to Start", even though the underlying API calls succeed in the background.
`e2e/` is now a pytest + Playwright project driving a mock EIP-1193 wallet through the actual
FE — see `e2e/lib/wallet.py` for the connect-flow mechanics and `e2e/pages/` for the reusable
page objects.

## 5. Recommended next steps

1. **Adopt this repo now** as the API/contract E2E layer against uat/stage — it's ready.
2. **Add contract guarding**: point `lib/schemas.py` at (or generate from) the BE's OpenAPI so
   drift is caught cheaply, not only via slow E2E.
3. ~~Add a `browser` project here with 3–5 FE-integrated journeys~~ — **DONE**, see `e2e/`
   (mock EIP-1193 wallet, not session injection — that path was a dead end, see 4b above).
4. **Wire post-deploy dispatch** from both BE and FE deploy workflows into this repo, reporting a
   status check back.
5. Keep the split honest: fee-engine invariants stay in the API layer; the browser layer only
   asserts the UI reflects them.
