"""spot account — read balance. port of suites/neodax/spot/account.spec.ts.

@trades (funded account, opens no orders). spot has no leverage/positions, so account
concern = balances only. report captures it.
"""
import pytest

from lib.report import record, record_check, step
from lib.spot import get_spot_balance_snapshot


@pytest.mark.trades
@pytest.mark.timeout(300)  # first `account` use -> faucet + transfer + enroll
class TestSpotAccount:
    def test_seeded_account_reports_available_spot_usdt_balance(self, account):
        # arrange — account already funded by the fixture.

        # act — read spot USDT balance.
        bal = step("read spot USDT balance",
                   lambda: get_spot_balance_snapshot(account.trading_client, account.app_session_id, "USDT"))
        record("spot USDT balance", bal.__dict__)

        # assert — seeded account reports positive available USDT.
        record_check(name="seeded account has available spot USDT", passed=bal.available > 0, detail=bal.__dict__)
        assert bal.available > 0, "available spot USDT"
