"""YEN-3325: liquidation takeover price integrity.
port of suites/neodax/perp/liquidation-takeover.spec.ts.

a full CROSS liquidation force-settles underwater legs at the bankruptcy price via the
settlement pool (NOT the book) -> real exec_type=liquidation_takeover trades. bug: a NEGATIVE
bankruptcy price was persisted as-is (price & total < 0). fix clamps <=0 -> 0.
INVARIANT: price >= 0 AND total >= 0.

a non-positive price ONLY arises in the MULTI-LEG batch calculator (single-leg short
bankruptcy = entry + deposit/size is always positive). so this drives the exact incident
shape: a dominant LONG (market A) crashed toward 0 drags shared cross equity deeply negative;
the batch allocates the deficit and a SHORT sibling (market B, held ~flat so Step2 does not
IOC it out) gets a negative allocated price -> clamped to 0. the SHORT leg's takeover
recording **price=0** is the fingerprint that the clamp fired: it makes the test non-vacuous
(remove the clamp -> that price goes negative -> red). fresh cross subject + maker; restore
marks (blast radius).

run: pytest -m serial suites/neodax/perp/test_liquidation_takeover.py   (one process)
"""
import pytest

from config.competition import takeover_liquidation as cfg
from lib.liquidation import drive_cross_account_liquidation
from lib.mark_price import MarkPriceInject, restore_mark_price
from lib.perp import (
    PerpTrade,
    close_all_perp_positions,
    create_perp_order,
    get_perp_mark_price,
    get_perp_positions,
    get_perp_top_of_book,
    get_perp_trades,
    maker_price_inside_spread,
    resolve_perp_market,
    round_tick,
    set_perp_leverage,
    size_amount,
    wait_for_perp_fill,
)
from lib.poll import poll_until
from lib.report import record, record_check, step
from lib.risk_tiers import get_market_risk_tiers  # noqa: F401 (parity import; ladder not needed here)

LIQ_EXEC_TYPES = ["liquidation", "liquidation_takeover", "adl"]


def is_liq_trade(t: PerpTrade) -> bool:
    return t.exec_type in LIQ_EXEC_TYPES


def leg_size(positions, direction: str) -> float:
    # one direction's leg size for a market (fresh account -> single leg per market+direction).
    return sum(abs(float(p.amount)) for p in positions if p.direction == direction)


def total_abs_size(positions) -> float:
    # abs open size across ALL legs (0 when the account is fully liquidated).
    return sum(abs(float(p.amount)) for p in positions)


# module state: teardown restores BOTH injected marks even on raise; test 2 scans trades
# captured by test 1.
state: dict = {"restore": [], "captured_trades": [], "liquidated": False}


@pytest.fixture(scope="module", autouse=True)
def _restore_marks(env_cfg, clients):
    yield
    if not state["restore"] or not env_cfg.faucet_url:
        return
    faucet = clients.make(env_cfg.faucet_url).client
    for r in state["restore"]:
        restore_mark_price(faucet, r["market"], r["mark"])


@pytest.mark.serial
class TestPerpLiquidationTakeoverPriceIntegrity:
    @pytest.mark.timeout(900)  # two provisions + two opens + hold two marks until liquidated + ingest
    def test_1_full_cross_liquidation_records_takeover_trades_with_non_negative_price_and_total(self, env, new_funded_account):
        # Arrange — faucet host for mark injection (uat only), fresh cross subject + maker.
        assert env.faucet_url, "faucet host needed for mark injection (uat)"
        faucet = env.client_for(None, env.faucet_url)
        subject = step("provision subject", lambda: new_funded_account(
            spot_usdt=cfg.subject_deposit_usdt, perp_usdt=cfg.subject_deposit_usdt, role="tko-subject"))
        maker = step("provision maker", lambda: new_funded_account(
            spot_usdt=cfg.maker_deposit_usdt, perp_usdt=cfg.maker_deposit_usdt, role="tko-maker"))

        long_mkt = resolve_perp_market(subject.trading_client, cfg.long_market)
        short_mkt = resolve_perp_market(subject.trading_client, cfg.short_market)
        mark_a = get_perp_mark_price(subject.trading_client, long_mkt.market)
        mark_b = get_perp_mark_price(subject.trading_client, short_mkt.market)
        assert mark_a > 0, "long-market mark available"
        assert mark_b > 0, "short-market mark available"
        state["restore"] = [{"market": long_mkt.market, "mark": str(mark_a)},
                            {"market": short_mkt.market, "mark": str(mark_b)}]

        amt_a = size_amount(cfg.long_notional_usd, mark_a, long_mkt)
        amt_b = size_amount(cfg.short_notional_usd, mark_b, short_mkt)
        for acct in (subject, maker):
            set_perp_leverage(acct.order_client, acct.app_session_id, long_mkt.market, cfg.leverage)
            set_perp_leverage(acct.order_client, acct.app_session_id, short_mkt.market, cfg.leverage)

        # leg A — subject opens a dominant LONG vs maker's resting sell (this is the leg we crash).
        top_a = get_perp_top_of_book(subject.trading_client, long_mkt.market)
        create_perp_order(maker.order_client, maker.app_session_id, market=long_mkt.market, side="sell",
                          direction="short", type="limit", amount=amt_a,
                          price=maker_price_inside_spread("sell", top_a, mark_a, long_mkt), tif="gtc",
                          leverage=cfg.leverage)
        open_a = step("subject opens dominant LONG (market A)",
                      lambda: create_perp_order(subject.order_client, subject.app_session_id,
                                                market=long_mkt.market, side="buy", direction="long",
                                                type="market", amount=amt_a, leverage=cfg.leverage))
        wait_for_perp_fill(subject.trading_client, subject.app_session_id, long_mkt.market, open_a, 20)

        # leg B — subject opens a small SHORT vs maker's resting buy (this leg receives the clamped price).
        top_b = get_perp_top_of_book(subject.trading_client, short_mkt.market)
        create_perp_order(maker.order_client, maker.app_session_id, market=short_mkt.market, side="buy",
                          direction="long", type="limit", amount=amt_b,
                          price=maker_price_inside_spread("buy", top_b, mark_b, short_mkt), tif="gtc",
                          leverage=cfg.leverage)
        open_b = step("subject opens small SHORT (market B)",
                      lambda: create_perp_order(subject.order_client, subject.app_session_id,
                                                market=short_mkt.market, side="sell", direction="short",
                                                type="market", amount=amt_b, leverage=cfg.leverage))
        wait_for_perp_fill(subject.trading_client, subject.app_session_id, short_mkt.market, open_b, 20)

        poll_until(
            lambda: leg_size(get_perp_positions(subject.trading_client, subject.app_session_id, long_mkt.market), "long"),
            lambda size: size > 0, timeout_s=20, message="dominant long leg opened",
        )
        long_a = leg_size(get_perp_positions(subject.trading_client, subject.app_session_id, long_mkt.market), "long")
        pos_b = get_perp_positions(subject.trading_client, subject.app_session_id, short_mkt.market)
        short_b = leg_size(pos_b, "short")
        pos_a = get_perp_positions(subject.trading_client, subject.app_session_id, long_mkt.market)
        entry_a = float(next(p for p in pos_a if p.direction == "long").entry_price or str(mark_a))
        entry_b = float(next(p for p in pos_b if p.direction == "short").entry_price or str(mark_b))
        record("subject opened cross legs", {"longA": long_a, "entryA": entry_a, "longNotional": long_a * entry_a,
                                             "shortB": short_b, "entryB": entry_b, "shortNotional": short_b * entry_b})

        # Act — crash market A toward 0 (shared equity deeply negative -> batch settles leg B
        # <=0 -> clamp), hold market B just ABOVE its entry (small LOSS -> short B is NOT IOC'd
        # as profitable in Step2).
        crash_mark = round_tick(entry_a * cfg.crash_to_pct, long_mkt.tick_size, long_mkt.price_precision)
        flat_mark = round_tick(entry_b * (1 + cfg.short_flat_above_pct), short_mkt.tick_size, short_mkt.price_precision)
        total_size_of = lambda: total_abs_size(get_perp_positions(subject.trading_client, subject.app_session_id))
        liq = step("crash long market, drive full cross liquidation", lambda: drive_cross_account_liquidation(
            faucet=faucet,
            injections=[
                MarkPriceInject(market=long_mkt.market, mark_price=crash_mark, index_price=crash_mark),
                MarkPriceInject(market=short_mkt.market, mark_price=flat_mark, index_price=flat_mark),
            ],
            total_size_of=total_size_of, timeout_s=cfg.liquidate_timeout_s,
        ))
        for r in state["restore"]:
            restore_mark_price(faucet, r["market"], r["mark"])  # restore ASAP (blast radius)

        # takeover trades land async. poll until liquidation-type fills appear.
        def liq_trade_count() -> int:
            ta = get_perp_trades(subject.trading_client, subject.app_session_id, long_mkt.market)
            tb = get_perp_trades(subject.trading_client, subject.app_session_id, short_mkt.market)
            return len([t for t in ta + tb if is_liq_trade(t)])

        poll_until(liq_trade_count, lambda n: n > 0, timeout_s=cfg.trade_ingest_timeout_s,
                   intervals=(1, 2, 3), message="liquidation-type fills appeared in trade history")
        trades_a = get_perp_trades(subject.trading_client, subject.app_session_id, long_mkt.market)
        trades_b = get_perp_trades(subject.trading_client, subject.app_session_id, short_mkt.market)
        state["captured_trades"] = trades_a + trades_b
        state["liquidated"] = True

        # Assert — full liquidation produced takeover trades; the SHORT leg clamped to 0; NONE negative.
        captured = state["captured_trades"]
        liq_trades = [t for t in captured if is_liq_trade(t)]
        takeovers = [t for t in captured if t.exec_type == "liquidation_takeover"]
        zero_priced = [t for t in takeovers if t.price == 0]
        neg_price = [t for t in liq_trades if t.price < 0]
        neg_total = [t for t in liq_trades if t.total < 0]
        record("liquidation outcome", {"triggered": liq.triggered, "longA": long_a, "shortB": short_b,
                                       "sizeAfter": liq.size_after, "crashMark": crash_mark, "flatMark": flat_mark,
                                       "liqTradeCount": len(liq_trades), "takeoverCount": len(takeovers),
                                       "zeroPricedCount": len(zero_priced),
                                       "liqTrades": [t.__dict__ for t in liq_trades]})

        record_check(name="account fully liquidated (all legs -> 0)",
                     passed=liq.triggered and liq.size_after <= 1e-9,
                     detail={"longA": long_a, "shortB": short_b, "sizeAfter": liq.size_after})
        record_check(name="real liquidation_takeover trades produced", passed=len(takeovers) > 0,
                     detail={"takeoverCount": len(takeovers)})
        record_check(name="batch clamp fired: a takeover clamped to price=0 (raw was <=0)",
                     passed=len(zero_priced) > 0, detail=[t.__dict__ for t in zero_priced])
        record_check(name="no liquidation trade has a negative price", passed=len(neg_price) == 0,
                     detail=[t.__dict__ for t in neg_price])
        record_check(name="no liquidation trade has a negative total", passed=len(neg_total) == 0,
                     detail=[t.__dict__ for t in neg_total])
        # residual gap (agent-confirmed): clamp maps <=0 -> 0, so a takeover still lands at
        # price=0 (not a real market price). shipped fix guarantees >=0, not >0 -> observational.
        record_check(name="takeover price=0 is not a real market price (residual gap)", passed=True, info=True,
                     detail={"zeroPriced": [t.__dict__ for t in zero_priced]})

        # fail-loud preconditions: the exact bug scenario was created (else invariant vacuous).
        assert long_a > 0, "dominant long leg opened"
        assert short_b > 0, "small short leg opened"
        assert liq.triggered, "account fully liquidated (batch takeover ran)"
        assert len(takeovers) > 0, "a real liquidation_takeover trade was produced"
        # NON-VACUOUS gate: the scenario actually drove a raw settlement price <=0 (clamped to
        # 0). without this the test proves nothing. removing the clamp turns this red.
        assert len(zero_priced) > 0, "the batch clamp fired (a takeover clamped to exactly 0)"
        # YEN-3325 invariant — the bug gate. red on buggy backend, green with the clamp.
        for t in liq_trades:
            assert t.price >= 0, f"liquidation trade price non-negative (exec_type={t.exec_type}, market={t.market})"
            assert t.total >= 0, f"liquidation trade total non-negative (exec_type={t.exec_type}, market={t.market})"

        # cleanup — flatten maker in both markets best-effort (subject already fully liquidated).
        close_all_perp_positions(maker.order_client, maker.app_session_id, long_mkt.market)
        close_all_perp_positions(maker.order_client, maker.app_session_id, short_mkt.market)

    def test_2_no_trade_carries_negative_price_or_inconsistent_total(self):
        # Arrange — reuse the trade history captured after the forced liquidation in test 1.
        assert state["liquidated"], "previous test forced a liquidation and captured trades"
        captured: list[PerpTrade] = state["captured_trades"]
        assert len(captured) > 0, "liquidated account has trade rows to scan"

        # Act — inspect every recorded fill (all exec_types: trade, liquidation, takeover, adl).
        bad_price = [t for t in captured if t.price < 0]
        bad_total = [t for t in captured if t.total < 0]
        # total must equal amount x price (the bug persisted total = amount x NEGATIVE price).
        inconsistent = [t for t in captured
                        if abs(t.total - t.amount * t.price) > max(1e-6, abs(t.amount * t.price) * 1e-6)]
        record("trade-history scan", {"rows": len(captured),
                                      "negativePrice": [t.__dict__ for t in bad_price],
                                      "negativeTotal": [t.__dict__ for t in bad_total],
                                      "inconsistentTotal": [t.__dict__ for t in inconsistent]})

        # Assert — no corrupt rows anywhere in the liquidated account's trade history.
        record_check(name="every trade price >= 0", passed=len(bad_price) == 0, detail=[t.__dict__ for t in bad_price])
        record_check(name="every trade total >= 0", passed=len(bad_total) == 0, detail=[t.__dict__ for t in bad_total])
        record_check(name="every trade total == amount x price", passed=len(inconsistent) == 0,
                     detail=[t.__dict__ for t in inconsistent])
        assert bad_price == [], "no trade has a negative price"
        assert bad_total == [], "no trade has a negative total"
        assert inconsistent == [], "every trade total equals amount x price"
