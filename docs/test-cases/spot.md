# Test cases — spot

grug list. `@trades` = real order. see [index](../../TEST_CASES.md).

spot api. suites live under `suites/nimbus/spot/`.

## test_account.py
| tag | case | grug |
|---|---|---|
| @trades | spot USDT balance | seed acct. spot USDT there. |

## test_orders.py
| tag | case | grug |
|---|---|---|
| @trades | resting spot limit -> cancel | rest buy low. open_orders. USDT lock. cancel -> back. |

**Ground truth:** a limit buy locks `amount × price` of quote exactly, no buffer/fee (slippage
buffer is market-only). Source: `spot_service.go` `LockOrderFunds`/`PrepareLockOrderFunds`
~L1800-1829, `TestSpotService_LockOrderFunds_PostOnly_BuyLocksQuoteAtPrice`.

**Gotchas:**
- `account`'s baseline settle-check only confirms PERP (>=90%), never re-confirms spot after
  the transfer — reading spot "before" immediately can catch a trickling debit (real recurring
  flake). Settle to the fixture's known funded baseline first.
- Rest price is computed right before placing the order, not earlier: the balance-settle wait
  can take up to 20s, live price can drift that much, and a stale price can land outside the
  exchange's deviation band (confirmed live → `limit_price_deviation_exceeded`).
- Never cross the current best ask, including a stale leftover from a past run's failed
  teardown — confirmed live: a stale ask well below the real mark ate the order as an instant
  taker fill instead of resting.
- `/spot/orders` history inclusion is eventually consistent (lags `open_orders`, unlike perp
  `/orders`) — recorded as observation, never asserted on timing.

## test_trade.py
| tag | case | grug |
|---|---|---|
| @trades | market buy vs maker | maker rest sell. acct buy. USDT down, base up. |

**Setup note:** maker rests a sell one tick inside spread (best ask) as the guaranteed
counterparty. If the book is empty on a quiet UAT market, falls back to the corresponding perp
market's oracle-fed mark price, so the resting order becomes the first price point.
