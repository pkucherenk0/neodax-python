"""perp acct surface. port of suites/neodax/perp/account.spec.ts.

read collateral balance + set/read initial leverage. @trades funded acct, open no positions.
tests independent. detail report: results/latest/detailed-report.md
"""
import pytest

from config.competition import perp_market
from lib.perp import get_perp_account, get_perp_balance_snapshot, resolve_perp_market, set_perp_leverage
from lib.report import record, record_check, step

LEVERAGE = 10


@pytest.mark.trades
@pytest.mark.timeout(300)  # first use of account do faucet + transfer + enroll
class TestPerpAccount:
    def test_seeded_perp_account_reports_available_usdt_collateral(self, account):
        mkt = resolve_perp_market(account.trading_client, perp_market)
        bal = step("read perp balance",
                   lambda: get_perp_balance_snapshot(account.trading_client, account.app_session_id))
        record("balance", {"market": mkt.market, **bal.__dict__})

        record_check(name="seeded account has available USDT collateral", passed=bal.available > 0, detail=bal.__dict__)
        assert bal.available > 0, "available USDT collateral"

    @pytest.mark.timeout(120)
    def test_setting_initial_leverage_is_accepted_and_reflected_on_account(self, account):
        mkt = resolve_perp_market(account.trading_client, perp_market)
        before = get_perp_balance_snapshot(account.trading_client, account.app_session_id)
        res = step(f"set leverage {LEVERAGE}x",
                   lambda: set_perp_leverage(account.order_client, account.app_session_id, mkt.market, LEVERAGE))
        record("set-leverage response", res.model_dump())
        acct = get_perp_account(account.trading_client, account.app_session_id)
        reflected = float((acct.initial_leverages or {}).get(mkt.market, "0"))
        after = get_perp_balance_snapshot(account.trading_client, account.app_session_id)

        record_check(name="set-leverage accepted", passed=res.success is True, detail=res.model_dump())
        record_check(name=f"account initial leverage == {LEVERAGE}x", passed=reflected == LEVERAGE,
                     detail={"reflected": reflected, "initial_leverages": acct.initial_leverages})
        record_check(name="balance unchanged by leverage change",
                     passed=abs(after.available - before.available) < 1e-6,
                     detail={"before": before.available, "after": after.available})

        assert res.success, "leverage change accepted"
        assert reflected == LEVERAGE, "account initial leverage reflects the set value"
        assert after.available == pytest.approx(before.available, abs=1e-6), \
            "setting leverage does not move collateral"
