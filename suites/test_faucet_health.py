"""faucet canary. fail fast+cheap on exactly the failure this repo hit repeatedly this
session: faucet deposit reports success but the spot balance never actually settles (a live
UAT infra outage, not a test bug) -- discovered previously only downstream, as confusing
timeouts scattered across every @trades/@serial test that funds an account. this catches it
in one place, in safe-lane, before any of those run.

smoke (fail-fast intent, same as test_health.py) but deliberately NOT stateless: it performs
one real, tiny, throwaway-UAT faucet deposit. see pr-check.yml's safe-lane comment for that
accepted trade-off.
"""
import pytest

from configs.competition import funding
from lib.funding import faucet_deposit, get_spot_available, wait_for_balance

CANARY_USDT = "10"  # tiny on purpose: proves the pipeline is alive, isn't a trading fixture


@pytest.mark.smoke
class TestFaucetHealth:
    def test_faucet_deposit_settles_to_spot_balance(self, env_cfg, clients, fresh_wallet):
        if not env_cfg.has_faucet or not env_cfg.faucet_url:
            pytest.skip(f"{env_cfg.name} has no faucet configured -- nothing to canary")

        wallet = fresh_wallet()
        faucet = clients.make(env_cfg.faucet_url)
        trading = clients.make(env_cfg.trading_base, wallet.jwt)
        try:
            faucet_deposit(faucet.client, wallet.app_session_id, CANARY_USDT)
            spot = wait_for_balance(
                lambda: get_spot_available(trading.client, wallet.app_session_id),
                float(CANARY_USDT) * 0.9,
                funding.settle_timeout_s,
            )
        finally:
            faucet.dispose()
            trading.dispose()

        assert spot > 0, (
            f"faucet deposit did not settle for {wallet.address} (available {spot}) -- "
            f"this is the faucet/settlement pipeline, not this test"
        )
