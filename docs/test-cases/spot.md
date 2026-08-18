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

## test_trade.py
| tag | case | grug |
|---|---|---|
| @trades | market buy vs maker | maker rest sell. acct buy. USDT down, base up. |
