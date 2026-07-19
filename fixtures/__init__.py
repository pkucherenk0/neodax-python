"""pytest fixtures, split by concern. registered as plugins from the root conftest.py's
`pytest_plugins` — see there for the load order and why (accounts.py depends on clients.py's
`ClientFactory`/`env_cfg`, resolved by fixture name at runtime, not by import).

clients.py    — HTTP client factory + --env resolution (env_cfg, env, clients)
accounts.py   — wallets/trading accounts (account, enrolled_account, spot_maker, perp_maker, ...)
reporting.py  — per-test artifacts + the results/runs/<runId>/detailed-report.md writer
"""
