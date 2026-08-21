"""offline unit tests for spot reference-price fallback math. no network."""
from lib.spot import SpotTopOfBook, spot_reference_price_or_mark, spot_resting_sell_price_or_mark


class TestSpotReferencePriceOrMark:
    def test_uses_book_price_when_close_to_mark(self):
        top = SpotTopOfBook(best_bid=2350, best_ask=2360)
        assert spot_reference_price_or_mark(top, mark_price=2370) == 2350

    def test_falls_back_to_mark_when_book_empty(self):
        top = SpotTopOfBook(best_bid=0, best_ask=0)
        assert spot_reference_price_or_mark(top, mark_price=2370) == 2370

    def test_falls_back_to_mark_when_book_stale(self):
        # confirmed live: a leftover resting bid from a past run's failed teardown sat at 2000
        # while the real mark had moved to 2370.61 -- >15% off, picked as "best bid" forever.
        top = SpotTopOfBook(best_bid=2000, best_ask=0)
        assert spot_reference_price_or_mark(top, mark_price=2370.61) == 2370.61

    def test_no_mark_available_trusts_the_book_regardless(self):
        top = SpotTopOfBook(best_bid=2000, best_ask=0)
        assert spot_reference_price_or_mark(top, mark_price=0) == 2000


class TestSpotRestingSellPriceOrMark:
    def test_uses_book_derived_price_when_close_to_mark(self):
        top = SpotTopOfBook(best_bid=2350, best_ask=2360)
        price = spot_resting_sell_price_or_mark(top, mark_price=2370)
        assert price == "2359.99"  # one tick inside the ask

    def test_falls_back_to_mark_when_book_empty(self):
        top = SpotTopOfBook(best_bid=0, best_ask=0)
        assert spot_resting_sell_price_or_mark(top, mark_price=2370) == "2370.00"

    def test_falls_back_to_mark_when_book_derived_price_stale(self):
        top = SpotTopOfBook(best_bid=2000, best_ask=0)  # derives 2000.01, still stale vs 2370.61
        assert spot_resting_sell_price_or_mark(top, mark_price=2370.61) == "2370.61"
