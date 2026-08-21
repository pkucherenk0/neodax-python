# e2e_py — UI tests (pytest + Playwright + a mock wallet)

Page-Object-Model port of the old `e2e/` (Node/Playwright) project — same live app, same mock
EIP-1193 wallet approach (real signatures, no MetaMask extension), now Python + pytest,
structured like `practice-py`'s POM layout: `pages/` (locators + actions, no `assert`/`expect`),
`components/` (shared modals), `lib/` (wallet mock, API helpers, arrangement loader),
`conftest.py` (fixtures), `tests/`.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
pytest
```

`pytest.ini` mirrors the old `playwright.config.ts`'s `use` block: headless chromium,
screenshot-on-failure, trace-on-failure. Named checkpoint screenshots (`lib/screenshots.py`)
land in `screenshots/`, same reasoning as before — automatic on-failure alone misses a failure
inside a page-object method, not just the test itself.

## Why this is Python now, not Node

`@johanneskares/wallet-mock`'s actual browser-side script (see the still-installed
`../e2e/node_modules/@johanneskares/wallet-mock/dist/{installMockWallet,createWallet}.js` during
the coexistence period) turned out to have **no crypto in it at all** — it's a generic EIP-6963
"announce a fake wallet" shim that forwards every `request()` call back to the test runner over
`page.exposeFunction()`. All the actual signing happens runner-side (Node+viem there, Python+
`eth_account` here, already used elsewhere in this repo). That meant porting this needed no
Node build step, no bundling, no npm dependency anywhere — see `spike_wallet_mock.py` for how
that was proven out first, before anything else was built.

## Known issues / workarounds (debugging notes, not rules)

Everything below was true of the original Node suite and stays true here — same live app, same
FE, same bugs. A few new, Python-porting-specific ones are called out separately at the end.

**The app auto-connects on its own once a wallet is discoverable — no Connect-button click
needed.** `HomePage.wait_for_wallet_connected()` waits for the connected-state signal instead of
clicking anything. Two signals are accepted — the top-nav "Deposit" link, or the "Welcome to
Yellow Pro" modal's own "Connected as 0x..." text, whichever renders first.

**"What's new" / "Welcome" modals render with a delay** (confirmed: not present immediately,
~3s later) — a one-shot check has a real gap where the modal renders after the check and blocks
the next click. `components/modals.py`'s `click_robust_to_modal_race()`: try the click with a
short bounded timeout, dismiss both modals if that's what blocked it, retry once. Fixed a
10-minute CI hang in the original.

**Transfer dialog's "Transfer from" balance can be stale (known UAT FE bug).** The Transfer
button silently stays disabled even though the account has the funds (confirmed independently:
same balance visible via `GET /spot/account`, identical transfer succeeds instantly via
`POST /accounts/transfer`). `AssetsPage._refresh_stale_from_balance()` toggles the "Transfer
from" selector away and back to force a refetch. Its picker is a portal — scoped at the page
level, not the dialog.

**Order-form field order is unverified, not enforced.** `PerpOrderPage.place_resting_limit_buy`
fills `input[type=text]` by position (price, then size) — no stable selector exists. The
form's actual field set is logged + screenshotted every run so a bad-input symptom is
diagnosable from the report, not just a rerun.

**Position propagation trails the fill by a beat, UI may not live-refresh.**
`PositionsPage.wait_until_visible()` reloads on a bounded retry loop rather than a fixed sleep.
Reload also re-triggers wagmi's wallet-auto-reconnect (retries ~1s up to 10x), so it waits on
the "Deposit" link with real headroom, not the locator default.

**`NIMBUS_FE_BASE` can carry a stray quote/whitespace** from how a CI secret was set — stripped
in `conftest.py`'s `fe_base` fixture before use.

**Faucet address casing must be EIP-55 checksummed** — unchanged, still enforced in
`tools/arrange_metamask_e2e.py` (this project shells out to the root `.venv`'s copy of that
script rather than duplicating it).

**Order matching sweeps the whole live book, not a targeted counter-order.**
`lib/api.py`'s `match_resting_order_with_api_counterparty` can't reliably target just our own
order — shared live book, thin market, server tick-rounds submitted prices. Sweeps the entire
current bid book with one market sell.

### New, specific to the Python port

**`Locator.is_visible(timeout=...)` in Playwright Python is a one-shot snapshot check, not a
real poll** — despite looking parallel to the JS original's `isVisible({timeout})`. Every place
that needs "wait up to N seconds to see if this appears" uses `Locator.wait_for(state=...)`
instead (both modals, the open-orders cancel check) — using the snapshot version would silently
treat "not rendered yet" as "absent."

**No `wait_for(state="enabled")` exists.** `PerpOrderPage`'s "form usable again" check
(after clicking Open Long) uses `expect(locator).to_be_enabled(timeout=...)` — `expect()` used
here purely as a bounded wait primitive, not as a test assertion (pages never grade pass/fail,
same convention as `practice-py`).

**The mock wallet needs to answer more than accounts+signing.** The first live run of the
ported test hung 20s waiting for a connected-state signal that never arrived. Root cause: wagmi
calls `wallet_getPermissions`/`wallet_switchEthereumChain`/`eth_chainId` during its own
connector init, before ever calling `eth_requestAccounts` — the real npm package answers all of
these (the last via a real viem walletClient that knows its own configured chain locally);
`lib/wallet.py`'s `eip1193_request` now does too. `eth_signTypedData_v4` stays unimplemented on
purpose, matching the original's own documented gap — never needed by this app's connect/order
flows.
