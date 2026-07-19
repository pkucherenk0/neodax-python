"""perp leverage-based tiered margin (YEN-2544). leverage enforcement.
port of suites/neodax/perp/risk-tier-leverage.spec.ts. two layers:

  1. global pre-tier guard on POST /perpetual/leverage. leverage must be in [1, globalMax].
     rejected before any account/position lookup, so no funding needed (@stateless);
  2. per-tier cap enforced sync at order placement: order whose post-order quote notional
     land in tier is rejected when leverage exceed that tier max_leverage
     (@trades — but order REJECTED, so nothing fills, nothing rests, no volume).
"""
import math

import pytest

from config.competition import risk_tier
from lib.perp import (
    cancel_perp_order,
    close_all_perp_positions,
    get_perp_mark_price,
    get_perp_open_orders,
    get_perp_positions,
    resolve_perp_market,
    round_tick,
    set_perp_leverage,
    size_amount,
)
from lib.poll import poll_until  # noqa: F401  (kept for parity with other suites)
from lib.report import record, record_check, step
from lib.risk_tiers import get_market_risk_tiers, select_risk_tier_for_notional
from lib.schemas import FlatError
from lib.validate import parsed_json

MARKET = risk_tier.market
RESET_LEVERAGE = 20  # restore shared worker acct to modest leverage after test


@pytest.mark.stateless
class TestPerpSetLeverageGlobalGuard:
    def test_rejects_setting_leverage_below_1x_400_invalid_leverage_value(self, fresh_wallet, env):
        # arrange — authenticated wallet. this guard run before any account/position lookup.
        wallet = fresh_wallet()
        client = env.client_for(wallet.jwt, env.trading_base)

        # act
        res = client.post("/perpetual/leverage",
                          data={"app_session_id": wallet.address, "market": MARKET, "leverage": "0"})

        # assert — status + flat trading-api error contract.
        assert res.status == 400, "leverage < 1 -> 400"
        body = parsed_json(res, FlatError)
        record_check(name="error code == invalid_leverage_value", passed=body.error == "invalid_leverage_value",
                     detail=body.model_dump())
        assert body.error == "invalid_leverage_value", "sub-1x leverage error code"

    def test_rejects_leverage_above_global_maximum_400_invalid_leverage_value(self, fresh_wallet, env):
        # arrange — leverage one step past BE global cap (limits.MaxLeverage).
        wallet = fresh_wallet()
        client = env.client_for(wallet.jwt, env.trading_base)
        over = risk_tier.global_max_leverage + 1

        # act
        res = client.post("/perpetual/leverage",
                          data={"app_session_id": wallet.address, "market": MARKET, "leverage": str(over)})

        # assert
        assert res.status == 400, "leverage > global max -> 400"
        body = parsed_json(res, FlatError)
        record_check(name="error code == invalid_leverage_value", passed=body.error == "invalid_leverage_value",
                     detail={"over": over, "body": body.model_dump()})
        assert body.error == "invalid_leverage_value", "over-global-max leverage error code"


@pytest.fixture(scope="module")
def _reset_account_leverage(account):
    """acct leverage set high for this test. reset it + clear any residual order/position so
    other @trades specs sharing this worker account not left on 100x+ cross leverage."""
    yield
    try:
        open_orders = get_perp_open_orders(account.trading_client, account.app_session_id, MARKET)
    except Exception:
        open_orders = []
    for o in open_orders:
        try:
            cancel_perp_order(account.order_client, account.app_session_id, MARKET, o.order_id)
        except Exception:
            pass
    close_all_perp_positions(account.order_client, account.app_session_id, MARKET)
    try:
        set_perp_leverage(account.order_client, account.app_session_id, MARKET, RESET_LEVERAGE)
    except Exception:
        pass


@pytest.mark.trades
@pytest.mark.timeout(300)  # first use of account do faucet + transfer + enroll
class TestPerpOrderTierLeverageEnforcement:
    def test_rejects_opening_position_whose_account_leverage_exceeds_notional_tier(self, account, _reset_account_leverage):
        # arrange — resolve market (incl per-market leverage cap), mark price, and live ladder.
        mkt = resolve_perp_market(account.trading_client, MARKET)
        mark = get_perp_mark_price(account.trading_client, mkt.market)
        assert mark > 0, "perp mark price available"
        assert mkt.max_allowed_leverage > 1, "market reports a max-allowed leverage"
        tiers = get_market_risk_tiers(account.trading_client, mkt.market)
        assert len(tiers) >= 2, "need a multi-tier ladder to exceed a lower-tier cap"

        # acct leverage can only raise up to per-market cap (max_allowed_leverage), separate
        # from per-tier cap. so target first tier whose max leverage is BELOW market cap —
        # only then does over-the-tier yet still-settable acct leverage exist.
        idx = next((i for i, t in enumerate(tiers) if t.max_leverage < mkt.max_allowed_leverage), -1)
        assert idx > 0, "a tier caps leverage below the market max"
        tier = tiers[idx]
        lower_bound = tiers[idx - 1].max_notional_quote  # this tier band is (lowerBound, tier.cap]
        target_notional = lower_bound * risk_tier.over_tier_multiplier  # just above lower edge -> in tier
        amount = size_amount(target_notional, mark, mkt)
        actual_notional = float(amount) * mark
        selected = select_risk_tier_for_notional(tiers, actual_notional)
        leverage = min(mkt.max_allowed_leverage, math.ceil(tier.max_leverage) + 5)
        # preconditions — notional must sit in target tier and leverage must exceed tier cap
        # yet stay within settable market cap.
        assert actual_notional <= tier.max_notional_quote, "sized notional stays within the target tier band"
        assert selected and selected.tier_index == tier.tier_index, "sized notional lands in the target tier"
        assert leverage > tier.max_leverage, "chosen leverage exceeds the tier max"
        assert leverage <= mkt.max_allowed_leverage, "chosen leverage is within the settable market cap"

        # cross-margin accts ignore order own leverage field. opening risk-tier check use acct
        # initial leverage. so flatten first, then raise acct leverage above target tier cap
        # (allowed while flat: leverage endpoint only run tier check when position open).
        close_all_perp_positions(account.order_client, account.app_session_id, mkt.market)
        lev = step(f"set account leverage {leverage}x while flat",
                   lambda: set_perp_leverage(account.order_client, account.app_session_id, mkt.market, leverage))
        assert lev.success, "setting over-tier (but within market cap) leverage is allowed while flat"

        # buy limit 5% below mark: never fills (below touch), so even if enforcement regressed
        # nothing trades. but opening tier check run at placement, so working engine reject here.
        # notional use MARK price, not this price.
        rest_price = round_tick(mark * 0.95, mkt.tick_size, mkt.price_precision)
        record("rejected-order setup", {"mark": mark, "marketMaxLeverage": mkt.max_allowed_leverage,
                                        "band": {"lowerBound": lower_bound, "cap": tier.max_notional_quote},
                                        "amount": amount, "actualNotional": actual_notional,
                                        "accountLeverage": leverage, "restPrice": rest_price})

        # act — submit directly. the 400 IS the assertion.
        res = step("submit tier opening limit order at over-tier account leverage",
                   lambda: account.order_client.post("/perpetual/order", data={
                       "app_session_id": account.app_session_id, "market": mkt.market, "side": "buy",
                       "direction": "long", "type": "limit", "amount": amount, "price": rest_price,
                       "time_in_force": "gtc", "reduce_only": False, "leverage": str(leverage),
                   }))

        # assert — rejected at placement with tier-specific error. nothing rested, nothing opened.
        assert res.status == 400, "over-tier leverage -> 400"
        body = parsed_json(res, FlatError)
        record("rejection body", body.model_dump())
        no_resting = not any(
            float(o.amount) == float(amount) and o.price == rest_price
            for o in get_perp_open_orders(account.trading_client, account.app_session_id, mkt.market)
        )
        flat = all(float(p.amount) == 0
                   for p in get_perp_positions(account.trading_client, account.app_session_id, mkt.market))
        record_check(name="error code == leverage_exceeds_tier", passed=body.error == "leverage_exceeds_tier",
                     detail=body.model_dump())
        record_check(name="rejected order did not rest", passed=no_resting, detail={"amount": amount, "restPrice": rest_price})
        record_check(name="account stayed flat", passed=flat, detail={"market": mkt.market})
        assert body.error == "leverage_exceeds_tier", "over-tier leverage error code"
        assert no_resting, "a rejected order leaves nothing resting"
        assert flat, "a rejected opening order leaves the account flat"

    # note (not tested): risk_tier_exceeded (notional above every tier cap) unreachable on
    # funded acct — top tier cap 1e12 quote, order amount capped at 1e6 base. the POST
    # /perpetual/leverage variant (raise leverage WITH open position) shares the same check
    # function; needs a real fill on thin book to set up, omitted as high-cost / redundant.
