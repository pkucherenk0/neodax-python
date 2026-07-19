# Test strategy — where tests live, what they cover, how CI wires across repos

Context: **three repos, deployed independently to shared envs (uat / stage).**

- **BE repo** — the trading / fee-engine / competition services.
- **FE repo** — `yellow-pro-hub` (Next.js / React).
- **This repo** (`neodax-api-tests`) — a **standalone black-box test harness** that targets a *deployed*
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

**Do not re-test fee math through the browser.** Driving 287k of volume or a 24h-YELLOW average by
clicking is slow and flaky, and it duplicates what the API layer already proves.

Instead, **reuse this repo's `fixtures/` + `lib/` as the *arrange* engine, drive the browser only for
the last-mile assertion:**

```
API-arrange (existing helpers):  enroll + faucet + transfer + driveCompetitionVolume → VIP1
        ↓
Browser (Playwright):            log in, open the fees/competition page
        ↓
Assert:                          the UI renders the discounted 8bps / VIP1 tier
```

Playwright does both `APIRequestContext` and browser in one project, so this is additive — new
browser projects alongside the existing `uat`/`stage` API projects, sharing the same fixtures.

Keep the browser set **small and journey-focused** (enroll, see discount, place order, see charged
fee). Everything combinatorial stays in the API layer.

### Auth is the real hurdle (yellow-pro-hub uses wallet-connect)
The API harness signs challenges directly with `ethers`. A browser can't click through a wallet
extension easily. Two options, in order of preference:

1. **Seed the session via the API, inject it into the browser** — reuse the existing auth fixture to
   get the `access_token`, then set it in the browser context (localStorage/cookie) so the app loads
   authenticated. Skips the wallet UI; tests everything *behind* login. Pragmatic and fast.
2. **Injected test provider** — expose a synthetic `window.ethereum` backed by a known test key
   (custom provider or a tool like Synpress) to exercise the *real* connect flow.

Use (1) for the bulk of journeys; add **one** (2)-style test that covers the wallet-connect flow
itself, so login isn't a coverage hole.

> Trade-off of (1): you skip testing the login UI on most tests — accepted, because the one connect
> test covers it and the value is in the post-auth journeys.

---

## 3. Cross-repo CI wiring (the multi-repo crux)

This repo can't be triggered by BE/FE **code** alone — it tests **deployments**. Wire it on deploy
events and schedules, not on push:

1. **Post-deploy trigger (primary).** When BE or FE finishes deploying to uat/stage, the deploy
   workflow fires a GitHub `repository_dispatch` (or `workflow_dispatch`) into this repo's Actions,
   passing the target env. 
   - BE deploy → run the **API/contract** suite (+ the FE-integrated smoke).
   - FE deploy → run the **FE-integrated** suite (+ a fast API smoke).
2. **Report back.** This repo posts a **commit status / check** back to the triggering repo via the
   GitHub API (or a Slack/dashboard notice), so a bad deploy is visible where the change was made.
3. **Nightly scheduled** full run against uat (and stage) — catches drift and flake.
4. **Manual dispatch** for release gating before promoting an env.

### Guard the contract *between* the repos (so you're not relying on slow E2E)
Separate repos drift silently. Add a fast, cheap contract guard so a BE change that breaks the FE↔API
shape fails *before* the full E2E:

- **Single source of truth for the API schema** (OpenAPI): BE publishes it; this repo's `lib/schemas.ts`
  (zod) and the FE's client validate against it. A schema change becomes a visible diff/PR.
- Or **consumer-driven contract tests** (Pact): the FE (consumer) publishes expectations, the BE
  (provider) verifies them in its own CI. Heavier, but decouples the repos properly.

Minimum viable version: keep the zod schemas in this repo as the shared contract, and treat a schema
validation failure in the E2E run as "contract drift" (they already do — `parsedJson` fails loudly).

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

**API map — DONE.** `configs/api-endpoints.json` is the registry of BE routes in scope for this
harness (the competition fee-engine + the trading/auth/faucet flows that feed it), extracted from the
BE repos (`neodax`, `yellow-pro-hub` — both Gin; see `generatedFrom` + `excludedAreas`). `python
tools/api_coverage.py` cross-references it against the endpoints the tests actually call (scanned
from `lib/`/`fixtures/`/`suites/`) and prints per-service coverage + two drift signals:
- registry endpoints with no test → **coverage gaps** (what to test next),
- paths referenced in tests but not in the registry → **stale map / typo** (regenerate).

Baseline: **18/47 (38%)** — full on the flows we exercise, with clear gaps (order cancellation,
positions read, leverage, rankings, `/me`, broadcast, internal fee-overlay). **Anti-drift:**
regenerate the registry when the BE route files change (the `generatedFrom` pointers say where); the
runtime `zod` schemas (`lib/schemas.ts`) already guard request/response *shape*, so the map only has
to track the *surface*, not the payloads.

**FE map — SUPERSEDED by a real UI e2e suite.** The original plan here was a Python Page Object
Model (`e2e/pages/<Screen>.ts` + a testid-contract audit) — retired. It turned out the FE's
wallet-connect gate (Reown AppKit + wagmi) can't be satisfied by session injection alone: the
Open Long/Short buttons stay `disabled` and the Open Orders/Positions panels show "Connect Wallet
to Start" without a *real* wallet connection, even though the underlying API calls succeed in the
background. `e2e/` is now a separate Node/Playwright project driving a real MetaMask instance
(dappwright) through the actual FE — see `e2e/lib/metamask.ts` for the connect-flow mechanics and
`e2e/lib/actions.ts` for the named, reusable UI actions.

## 5. Recommended next steps

1. **Adopt this repo now** as the API/contract E2E layer against uat/stage — it's ready.
2. **Add contract guarding**: point `lib/schemas.ts` at (or generate from) the BE's OpenAPI so drift
   is caught cheaply, not only via slow E2E.
3. ~~Add a `browser` project here with 3–5 FE-integrated journeys~~ — **DONE**, see `e2e/`
   (real MetaMask, not session injection — that path turned out to be a dead end, see 4b above).
4. **Wire post-deploy dispatch** from both BE and FE deploy workflows into this repo, reporting a
   status check back.
5. Keep the split honest: fee-engine invariants stay in the API layer; the browser layer only asserts
   the UI reflects them.
