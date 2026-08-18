# e2e — UI tests (Playwright + dappwright)

Separate Node/Playwright project, not pytest. Real MetaMask wallet (dappwright) driving the
actual FE — the wallet-connect gate (Reown AppKit + wagmi) can't be satisfied by session
injection alone: order buttons stay `disabled`, Open Orders/Positions panels show "Connect
Wallet to Start" without a real wallet connection.

```bash
cd e2e && npm install && npx playwright install chromium && npx playwright test
```

`playwright.config.ts` captures a screenshot + trace on failure only (`test-results/`), plus
manual named checkpoints in `screenshots/` along the way (see `lib/actions.ts`).

## Known issues / workarounds (debugging notes, not rules)

**MetaMask popup navigates in place, doesn't always reopen.** dappwright's own
`wallet.approve()`/`sign()` assume every popup step opens a NEW page and closes when done.
This app's connect popup instead navigates in place from "Connect" to its own SIWE-style
signature request — whether that second step even appears is session-state dependent (CI has
seen both). `connectMetaMask()` in `lib/metamask.ts` races both outcomes instead of assuming
the popup stays open.

**MetaMask popup can self-close mid-click (timing race).** Confirmed via a CI trace/screenshot:
the popup can be a perfectly well-formed "Approve Signature Request" (correct network/domain/
challenge, Confirm button visible+enabled) and still close on its own in the gap between an
`isClosed()` check and the click landing — not a broken import or bad seed phrase. Treated as
the same valid "closed on its own" outcome the surrounding code already tolerates for step 1.

**"What's new" / "Welcome" modals render with a delay.** Both appear a beat after page load or
after certain clicks (confirmed: not present immediately, ~3s later), so a one-shot "check then
click" has a real gap where the modal renders after the check and blocks the next click.
Playwright's own retry doesn't help — it waits for the target to become clickable, it has no
notion of dismissing an unrelated overlay. `clickRobustToModalRace()` in `lib/actions.ts`:
try the click with a short bounded timeout, dismiss both modals if that's what blocked it,
retry once. This is what fixed a 10-minute CI hang (the click retried against a blocked state
for the full test timeout).

**Transfer dialog's "Transfer from" balance can be stale (known UAT FE bug).** The Transfer
button silently stays disabled even though the account has the funds — confirmed independently
(same balance visible via `GET /spot/account`, identical transfer succeeds instantly via
`POST /accounts/transfer`). FE-only, not a real balance issue. Toggling the "Transfer from"
selector away and back forces a refetch that picks up the real balance
(`refreshTransferFromBalanceViaDirectionToggle`). Its picker is a portal — nested in the ARIA
snapshot but not necessarily in the DOM — so it's scoped at the page level, not `dialog`.

**Order-form field order is unverified, not enforced.** `placeRestingPerpLimitBuy` fills
`input[type=text]` by position (price, then size) — there's no stable selector. The FE's
modals have reordered before this session; a silent reorder here would fill plausible-looking
wrong values instead of failing loudly. The form's actual field set is logged + screenshotted
every run so a bad-input symptom is diagnosable from the report, not just a rerun.

**Position propagation trails the fill by a beat, UI may not live-refresh.**
`assertPositionVisibleInUi` reloads on a bounded retry loop rather than a fixed sleep, driven
by Playwright's own retrying assertion. Reload also re-triggers wagmi's wallet-auto-reconnect
(retries ~1s up to 10x), so give re-hydration real headroom, not the 5s locator default.

**`NIMBUS_FE_BASE` can carry a stray quote/whitespace from how a CI secret was set** (e.g.
`gh secret set --body "$url"` where `$url` still has quotes in it) — Chrome's `Page.navigate`
rejects that outright with an opaque "invalid URL". Stripped in `tests/spot-to-perp-position.spec.ts`
before use, with a loud diagnosis (length only, never the value itself) if it's still invalid
after stripping.

**Faucet address casing must be EIP-55 checksummed.** Confirmed live: the faucet does not
normalize address casing against the trading account, so a mismatch silently drops a deposit
(reports success, credit never lands). `tools/arrange_metamask_e2e.py` forces
`to_checksum_address()` once at the auth choke point; every downstream call reuses that string.

**Order matching sweeps the whole live book, not a targeted counter-order.** `matchRestingOrderWithApiCounterparty`
can't reliably target just our own order — this is a shared live book (interrupted past runs
can leave stale resting bids we have no credentials to cancel), and the server tick-rounds
submitted prices so filtering by our computed price is unreliable. It's a thin test market, so
the simplest robust fix is to sweep the entire current bid book with one market sell.
