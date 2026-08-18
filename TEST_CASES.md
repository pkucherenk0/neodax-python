# Test cases covered

grug list. every test here. few word. `@stateless` = no money. `@trades` = real order. `@serial` = ordered, share volume.

split by topic. one file per topic. keep fresh: add row when add test. run `pytest --collect-only -q` to check nothing missing.

| topic | file | what |
|---|---|---|
| health | [docs/test-cases/health.md](docs/test-cases/health.md) | env ping. smoke. |
| perps | [docs/test-cases/perps.md](docs/test-cases/perps.md) | perp api — risk tiers, positions, orders, account, liquidation reduction, history. |
| spot | [docs/test-cases/spot.md](docs/test-cases/spot.md) | spot api — account, orders, trade. |
| competition | [docs/test-cases/competition.md](docs/test-cases/competition.md) | competition fee overlay (perp-spot-0) — enroll, fees, tiers. |
| e2e | [docs/test-cases/e2e.md](docs/test-cases/e2e.md) | UI e2e via a mock EIP-1193 wallet (separate Node project — `cd e2e && npx playwright test`). |
