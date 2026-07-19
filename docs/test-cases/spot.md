# Test cases — spot

grug list. `@trades` = real order. see [index](../../TEST_CASES.md).

spot api. suites live under `suites/neodax/spot/`.

## account.spec.ts
| tag | case | grug |
|---|---|---|
| @trades | spot USDT balance | seed acct. spot USDT there. |

## orders.spec.ts
| tag | case | grug |
|---|---|---|
| @trades | resting spot limit -> cancel | rest buy low. open_orders. USDT lock. cancel -> back. |

## trade.spec.ts
| tag | case | grug |
|---|---|---|
| @trades | market buy vs maker | maker rest sell. acct buy. USDT down, base up. |
