# Test cases — competition

grug list. `@stateless` = no money. `@trades` = real order. `@serial` = ordered, share volume. see [index](../../TEST_CASES.md).

competition fee overlay (perp-spot-0). suites live under `suites/competition/`.

## enrollment.spec.ts
| tag | case | grug |
|---|---|---|
| @stateless | terms not accepted | terms false -> 422 TERMS_NOT_ACCEPTED. |
| @stateless | settled comp not enrollable | dead comp -> 409 COMPETITION_NOT_ENROLLABLE. |
| @stateless | happy enroll | live comp. enroll ok. owner = me. |

## funding.spec.ts
| tag | case | grug |
|---|---|---|
| @trades | funded acct has collateral | faucet. spot + perp money there. |

## spot-fees.spec.ts
| tag | case | grug |
|---|---|---|
| @stateless | overlay best-of on spot | enrolled. spot rate = best of standard/overlay. never worse. |

## base-vip-overlay.spec.ts
| tag | case | grug |
|---|---|---|
| @stateless | base VIP kept when overlay worse | overlay worse than base -> keep base. no override. |

## non-enrolled-fees.spec.ts
| tag | case | grug |
|---|---|---|
| @trades | non-enrolled pays standard | not enrolled taker. standard fee. overlay never. |

## perp-trade.spec.ts
| tag | case | grug |
|---|---|---|
| @trades | perp taker fee + volume | enrolled buy vs maker. charged = effective rate. volume grow. |

## spot-trade.spec.ts
| tag | case | grug |
|---|---|---|
| @trades | spot taker fee + volume | enrolled spot buy. charged = effective. volume grow. |

## perp-yellow-tier.spec.ts
| tag | case | grug |
|---|---|---|
| @trades | YELLOW deepens tier (no volume) | hold YELLOW. tier up by balance. rate step down. no trade. |

## perp-fee-tier.spec.ts — fee-tier step-down flow
| tag | case | grug |
|---|---|---|
| @serial | schedule has cheaper tier | comp schedule offer cheaper tier past threshold. |
| @serial | drive volume -> overlay active | trade till volume cross. overlay on at tier. |
| @serial | perp taker steps down | at tier, perp taker = best-of, below base. |
| @serial | spot taker discounted 10->8 | at tier, spot taker charged overlay rate. |
| @serial | maker side discounted | subject maker fill = overlay maker rate. both side. |
