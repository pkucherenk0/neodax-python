"""offline unit tests for perp sizing math. no network."""
import pytest

from lib.perp import PerpMarket, TopOfBook, maker_price_inside_spread, round_tick, size_amount

MKT = PerpMarket(market="X", amount_precision=3, price_precision=2, step_size=0.001, tick_size=0.01,
                 min_qty=0, min_notional=0, max_allowed_leverage=100, resolved=True)


class TestRoundTick:
    def test_rounds_to_the_tick_and_formats_at_price_precision(self):
        assert round_tick(100.017, 0.01, 2) == "100.02"
        assert round_tick(100.014, 0.01, 2) == "100.01"
        assert round_tick(100, 0.5, 2) == "100.00"
        assert round_tick(100.3, 0.5, 2) == "100.50"


class TestSizeAmount:
    def test_sizes_usd_notional_into_lot_aligned_base_amount(self):
        assert float(size_amount(1000, 100, MKT)) == pytest.approx(10, abs=1e-10)  # 1000/100 = 10, step-aligned
        assert size_amount(1000, 100, MKT) == "10.000"  # formatted at step decimals

    def test_rounds_up_to_the_step(self):
        # 1005/100 = 10.05 -> already step-aligned at 0.001
        assert float(size_amount(1005, 100, MKT)) == pytest.approx(10.05, abs=1e-6)
        # a non-aligned qty rounds up to the next step
        assert float(size_amount(1000, 3, MKT)) >= 1000 / 3

    def test_respects_min_qty(self):
        m2 = PerpMarket(**{**MKT.__dict__, "min_qty": 50})
        assert float(size_amount(1000, 100, m2)) >= 50

    def test_respects_min_notional(self):
        m3 = PerpMarket(**{**MKT.__dict__, "min_notional": 5000})
        # wanted 1000 notional but min 5000 -> qty must be >= 5000/100 = 50
        assert float(size_amount(1000, 100, m3)) * 100 >= 5000


class TestMakerPriceInsideSpread:
    spread = TopOfBook(best_bid=99, best_ask=101)

    def test_sells_one_tick_below_best_ask_new_best_ask_still_above_bid(self):
        assert maker_price_inside_spread("sell", self.spread, 100, MKT) == "100.99"

    def test_buys_one_tick_above_best_bid_new_best_bid_still_below_ask(self):
        assert maker_price_inside_spread("buy", self.spread, 100, MKT) == "99.01"

    def test_falls_back_to_mark_relative_quote_when_no_real_spread(self):
        flat = TopOfBook(best_bid=0, best_ask=0)
        assert maker_price_inside_spread("sell", flat, 100, MKT) == "100.10"  # mark * 1.001
        assert maker_price_inside_spread("buy", flat, 100, MKT) == "99.90"  # mark * 0.999
