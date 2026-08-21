# e2e — UI tests (pytest + Playwright + a mock wallet)

Page-Object-Model suite, own venv, driving the real live app through a mock EIP-1193 wallet
(real signatures, no MetaMask extension, no Node dependency).

```
e2e/
├── pages/          one class per FE page/component — locators + actions, no assert/expect
├── components/     shared modals (welcome/what's-new, race-condition handling)
├── lib/            wallet.py (mock wallet) · api.py (API helpers) · arrangement.py (loader)
│                   · screenshots.py · artifacts.py
├── tests/          the actual specs
├── conftest.py     fixtures
└── spike_wallet_mock.py   throwaway proof-of-concept, kept for reference (see below)
```

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
pytest                # safe default: connectivity check only, no real orders/transfers
pytest -m trades      # the real flow -- places a real order, transfers real funds. deliberate.
```

`pytest.ini`: headless chromium, screenshot-on-failure, trace-on-failure. Named checkpoint
screenshots (`lib/screenshots.py`) land in `screenshots/` — catches a failure inside a
page-object method, not just the test itself, which automatic on-failure alone misses.

**Safety rail**: `trades` is opt-in (default `addopts` excludes it), same as the main suite's
markers. CI passes `-m trades` explicitly — keep that flag when touching the workflow.

## The connect → trade flow

```
 mock wallet installed (add_init_script, before goto())
        │
        v
 page loads  ──►  EIP-6963 "announce fake wallet" shim fires  ──►  wagmi/AppKit discovers it
        │                                                                 │
        │                                    eth_chainId / wallet_getPermissions /
        │                                    wallet_switchEthereumChain (connector init)
        │                                                                 │
        │                                                                 v
        │                                                        eth_requestAccounts
        │                                                                 │
        v                                                                 v
 app AUTO-CONNECTS on its own (no Connect-button click) <───── signing happens runner-side
        │                                                       (eth_account, real sig)
        v
 wait_for_wallet_connected() — "Deposit" link OR "Connected as 0x..." modal text, whichever first
        │
        v
 place order / transfer  ──►  poll for UI state to reflect it  ──►  assert
```

Signing happens entirely runner-side in Python (`eth_account`) — the browser-side shim never
sees a private key, it only forwards `request()` calls back over `page.exposeFunction()`.
`eth_signTypedData_v4` stays unimplemented on purpose (never needed by this app's connect/order
flows). `spike_wallet_mock.py` proved this shim-forwarding approach out standalone, before
`lib/wallet.py`/`conftest.py` were built on top of it.

## Known issues / workarounds (debugging notes, not rules)

FE/live-app bugs and quirks — not this port's fault, still true against the real app:

| Issue | Where | Fix |
|---|---|---|
| App auto-connects once a wallet is discoverable — no Connect-button click | `HomePage.wait_for_wallet_connected()` | wait for connected-state signal (top-nav "Deposit" link, or "Connected as 0x..." modal text — whichever renders first), don't click |
| "What's new"/"Welcome" modals render ~3s late — a one-shot check races them | `components/modals.py`'s `click_robust_to_modal_race()` | bounded-timeout click, dismiss both modals if that's what blocked it, retry once. fixed a 10-min CI hang in the original |
| Transfer dialog's "Transfer from" balance can be stale (known UAT FE bug) — button silently stays disabled with funds present (confirmed: `GET /spot/account` shows correct balance, `POST /accounts/transfer` succeeds instantly) | `AssetsPage._refresh_stale_from_balance()` | toggle the selector away and back to force a refetch. picker is a portal, scoped page-level not dialog-level |
| Order-form field order unverified, no stable selector | `PerpOrderPage.place_resting_limit_buy` | fills `input[type=text]` by position (price, size); field set logged + screenshotted every run |
| Position propagation trails the fill by a beat, no live-refresh | `PositionsPage.wait_until_visible()` | bounded reload-retry loop (not a fixed sleep); reload re-triggers wagmi auto-reconnect (~1s ×10), so it waits on "Deposit" link with real headroom |
| `NIMBUS_FE_BASE` can carry a stray quote/whitespace (CI secret setup) | `conftest.py`'s `fe_base` fixture | stripped before use |
| Faucet address casing must be EIP-55 checksummed | `tools/arrange_metamask_e2e.py` (root `.venv`, shelled out to — not duplicated here) | enforced there |
| Funding setup sees more transient 5xx than the hot path — 404 `account_not_found` right after a fresh deposit, 503 `validation_unavailable` on spot→perp transfer | `tools/arrange_metamask_e2e.py`'s `PATIENT_RETRIES` (6, unlike the rest of the script's bare non-retrying contexts) | retries those specifically, since `e2e/` depends on this script succeeding |
| Order matching can't target just our own order — shared live book, thin market, server tick-rounds prices | `lib/api.py`'s `match_resting_order_with_api_counterparty` | sweeps the entire current bid book with one market sell |

### New, specific to the Python port

| Gotcha | Fix |
|---|---|
| `Locator.is_visible(timeout=...)` is a one-shot snapshot, not a real poll (unlike the parallel-looking JS `isVisible({timeout})`) | use `Locator.wait_for(state=...)` everywhere a wait-up-to-N-seconds check is needed (both modals, open-orders cancel check) — the snapshot version silently reads "not rendered yet" as "absent" |
| No `wait_for(state="enabled")` exists | `PerpOrderPage`'s post-Open-Long "form usable again" check uses `expect(locator).to_be_enabled(timeout=...)` purely as a bounded wait, not a test assertion (pages never grade pass/fail) |
| Mock wallet hung 20s on first live run — wagmi calls `wallet_getPermissions`/`wallet_switchEthereumChain`/`eth_chainId` during connector init, before `eth_requestAccounts` | `lib/wallet.py`'s `eip1193_request` answers all three now |
