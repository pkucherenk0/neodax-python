# Test cases — e2e

grug list. see [index](../../TEST_CASES.md).

suite lives under `e2e/` — a **separate Node/Playwright project**, not pytest. Mock EIP-1193
wallet (`@johanneskares/wallet-mock`, real signatures, no browser extension) driving the actual
FE, because the FE's wallet-connect gate (Reown AppKit + wagmi) can't be satisfied by session
injection alone: order buttons stay `disabled` and the Open Orders/Positions panels show
"Connect Wallet to Start" without a real wallet connection.
Run: `cd e2e && npm install && npx playwright install chromium && npx playwright test`.

| case | grug |
|---|---|
| spot to perp transfer, UI order matched by API counterparty, position visible | mock wallet auto-connects. transfer spot->perp via UI. place resting perp limit order via UI. confirm in Open Orders. match via a raw API call from a second (API-only) account. confirm resulting position renders in UI. |
