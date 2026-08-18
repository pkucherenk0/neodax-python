# Test-writing conventions — the fixed approach

These rules are **not suggestions**. Every test in this repo follows them so that any human or
AI agent can read, write, and trust any test without re-learning local style. Enforcement is
review + the patterns in `TEMPLATE_template.py` (a ruff/flake8 config can mechanize some rules
later).

> One-line mental model: **Arrange via fixtures → Act via the API client → Assert on ground truth.**
> Deterministic, isolated, marked, no live-money surprises.

---

## 1. File layout & naming (fixed)
- Path: `suites/<domain>/test_<feature>.py` — e.g. `suites/competition/test_enrollment.py`.
- Class = feature: `class TestCompetitionEnrollment`. Marker on the class = the lane.
- Test method = **observable behavior**, present tense, cause→effect:
  `test_rejects_enrollment_when_terms_are_not_accepted_422`. Not `test_enroll`, not `test_works`.
- One behavior per test. If the name needs "and", split it.

## 2. Structure — Arrange / Act / Assert (fixed)
Every test body has exactly these three comment-labeled sections, in order:
```python
# arrange — set up inputs via fixtures only
# act     — perform the single action under test
# assert  — verify the outcome against ground truth
```
No assertions in the Arrange section. No new actions in the Assert section.

## 3. Imports (fixed)
- Specs get clients/accounts **only** from conftest fixtures (`env`, `fresh_wallet`, `account`,
  `spot_maker`, `perp_maker`, `new_funded_account`) — never `sync_playwright()` or
  `request.new_context` inline in a spec.
- Domain behavior comes from `lib/` only. Never reimplement fee/tier/sizing math in a spec.

## 4. Fixtures for all setup/teardown (fixed)
- Need new shared setup? Add a fixture; don't copy setup code between specs.
- The client from `env.client_for()` is rate-limited and retries `429`/`5xx`/network with
  backoff (`lib/http.py`). Always use it. This is REQUEST-level retry and is independent of
  the no-test-retries rail (§8).

## 5. Determinism — no arbitrary waits (fixed)
- **Never** `time.sleep()` in a spec to "let things settle".
- Wait for a *condition* with an explicit budget: `poll_until(read, predicate, timeout_s=...)`
  from `lib/poll.py`.
- Endpoint lag is real here (fee-tier-effective trails the charged fee by ~1 fill). Model it
  as a poll with a stated timeout — never a fixed sleep.

## 6. Assertions — every test asserts, on ground truth (fixed)
- Every test must contain at least one `assert` with a message.
- Assert on the **authoritative** value: the *charged* fee is ground truth, not the advisory
  `fee-tier-effective` snapshot.
- Observational-only findings use `record_check(..., info=True)` — they are recorded and
  reported, **never** silently dropped and never fail the run. Silence is not allowed.

## 7. Isolation & markers (fixed)
Mark every test class with exactly one lane:
- `stateless` — independent, parallel-safe, no funding, no trades. **Default. Prefer this.**
- `trades`    — places real orders. NO retries ever. Run deliberately (`-m trades`).
- `serial`    — ordered flow; one process, phases share module state.
Tests never depend on another test's side effects **except** within a single `serial` class.

## 8. Safety rails (fixed — money is real)
- pytest has no retries by default and **none may ever be added** on `trades`/`serial`
  (no pytest-rerunfailures). A retry re-places live orders → double volume / lost funds.
- No destructive default: the default run excludes `trades`/`serial` (pytest.ini).
- Never log or commit secrets. `configs/accounts.stage.json` is ignored.
- Private keys: never printed to stdout/report, never committed. EXCEPTION (§12): the artifact
  writer saves minted **UAT throwaway** wallet keys to git-ignored `results/runs/<runId>/artifacts/`.
  uat only, ephemeral. Do NOT save keys for `stage` (its pool is real).

## 9. Test data (fixed)
- Stateless tests: one **fresh wallet per test** (`fresh_wallet()`) — zero shared state.
- Funded tests: the **worker-leased `account`** (session-scoped = per xdist worker process).
- No hardcoded slugs/tiers/notionals in specs — read from `configs/` (typed, env-overridable).

## 10. Readability for review & agents (fixed)
- No branching logic in a test body (`if`/`try` around assertions).
- No `@pytest.mark.skip` / commented-out tests committed without a tracked reason.
- Parameterize with `@pytest.mark.parametrize`, not with conditionals inside one test.

## 11. Comments — caveman style (fixed, enforced in review)
Grug write comment. Few word. Small word. Say WHY, not what code already say.
- lowercase. drop "the/a/is". one idea per line. broken grammar ok if shorter.
- keep hard fact: number, error code, endpoint, gotcha. drop story.
- rule touch COMMENTS only. NOT test name, NOT `step`/`record`/`record_check` name, NOT
  string, NOT assert message.
- keep `# arrange` `# act` `# assert` label (§2).
```
# bad:  In cross margin the order's own leverage field is ignored — the opening
#        risk-tier check evaluates the ACCOUNT's initial leverage, so we must ...
# good: cross acct ignore order leverage. tier check use acct leverage.
```

## 12. Save generated creds + order/trade ids (fixed)
Every minted account saved. every order + trade id saved. so a run can be traced/replayed.
- `lib/artifacts.py` writes JSONL to git-ignored `results/runs/<runId>/artifacts/worker-<pid>.jsonl`.
  run id minted once per invocation (`conftest pytest_configure` → `lib/run_context.py`);
  `results/latest` symlinks the newest run. best-effort, never fail a test.
- account creds saved AUTO in fixtures. order id saved AUTO in `create_perp_order`/
  `create_spot_order`. trade ids saved AUTO in the fill readers, deduped per order.
- auto. spec write no artifact code. new account/order helper must keep saving.

## 13. A test must be able to FAIL (fixed — anti-false-positive)
A green test proves nothing until you've seen it go red. AI (and humans) write the test AND
the expected value, so a test can silently "bless" wrong behavior. Every test must satisfy ALL:
- **Red-green proof.** Before trusting a new green, make it fail on purpose and confirm red.
  See `tools/red_green.py` / README.
- **Independent oracle.** The expected value comes from a source the test did NOT compute —
  the BE code, the spec, a pydantic contract, or the *charged* value. Never assert a mirror
  function against itself (e.g. `select_risk_tier_for_notional`) except against LIVE data.
- **No tautological / vacuous asserts.** Banned smells: `assert True`, `> 0` on an
  always-positive value, `status < 500`, wide bands that hide drift, a "jump" that a `+1e-7`
  nudge would also satisfy. Assert the SPECIFIC value/code, not a loose range.
- **Fail-loud preconditions.** Assert the Arrange actually created the scenario
  (`open_long > 0`, "a tier below market max exists") so the test can't silently no-op.
  No swallowing, no assert-skipping branches.
- **Read the evidence.** Green ≠ correct. Inspect what actually happened (`record()` /
  detailed report).
- **"Delete the feature" test.** If the feature were removed/broken, would this test go red?
  If not, it's decorative — rewrite it.
Pure `lib/` logic is additionally guarded by unit tests (`pytest tests/unit`); mutation testing
(`mutmut`) is not yet wired.

---

## Known gotchas (debugging notes, not rules)

**Multi-leg live-money setups: order by risk, not by narrative.** A test that opens more than
one margin-consuming position on the same account should open the small/low-volatility leg(s)
FIRST, the large/volatile one LAST — not in whatever order reads best in the test's own story.
Real cause, found in `suites/nimbus/perp/test_liquidation_takeover.py`: opening a large
($40k-notional) leg first let its own live mark-price movement (a few % in well under a
minute — normal volatility on a thin market, not a bug) eat into unrealized PnL enough that a
*second* order's margin-availability CHECK got rejected (`insufficient_margin`, `Available: 0`)
even though the account had plenty of nominal equity. This is NOT a settlement lag — a
poll-and-wait fix does nothing, because there's no lag to wait out, just a real loss that
already happened. The fix was reordering the two `create_perp_order` calls (small leg's margin
check now passes against the untouched deposit before the volatile leg exists at all), not
padding the deposit or retrying. If you see `insufficient_margin` with a suspiciously exact
`Available: 0` right after opening an unrelated position, dump the FULL account
(`get_perp_account`, not just `get_perp_balance_snapshot`) before assuming it's a timing bug —
`total_unrealized_pnl` will tell you immediately whether a real price move is the cause.

## The canonical shape
See **`suites/TEMPLATE_template.py`** (copy it) and the live reference
**`suites/competition/test_enrollment.py`**. If a new test doesn't look like these, it's wrong.
