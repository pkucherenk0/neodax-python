# Test cases — e2e

grug list. see [index](../../TEST_CASES.md).

suite lives under `e2e/` — a **separate pytest + Playwright project** (own venv), not part of
the main suite. mock EIP-1193 wallet (real signatures, no browser extension) drives the actual
FE — session-injected JWT alone can't pass the wallet-connect gate (order buttons stay
disabled, Open Orders/Positions panels stay locked). see `e2e/README.md`.
Run: `cd e2e && pip install -r requirements.txt && playwright install chromium && pytest`.

| case | grug |
|---|---|
| spot to perp transfer, UI order matched by API counterparty, position visible | mock wallet auto-connects. transfer spot->perp via UI. place resting perp limit order via UI. confirm in Open Orders. match via a raw API call from a second (API-only) account. confirm resulting position renders in UI. |
