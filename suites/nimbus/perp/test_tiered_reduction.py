"""perp tiered position reduction / Stage0 (PERP-2545).

liquidate a cross position PIECEWISE: each mark drop peels ONE tier (ReduceOnly IOC on the
book at bankruptcy) -> LIQUIDATION_PARTIAL ledger row. insurance fund NOT live, so the reduce
needs a real counterparty order -> the TEST seeds a maker bid. cross margin, fresh funded
subject + maker per test (never touch shared account). thin market.
as-built (NOT the ticket): fixed 1 tier/round, book-matched IOC, no insurance fund.

run: pytest -m serial suites/nimbus/perp/test_tiered_reduction.py   (one process)
"""
import pytest

from configs.competition import tiered_reduction as cfg
from fixtures.accounts import auto_refreshing, refresh_account_token
from lib.liquidation import drive_stepwise_liquidation
from lib.mark_price import restore_mark_price
from lib.perp import (
    close_all_perp_positions,
    create_perp_order,
    get_perp_mark_price,
    get_perp_positions,
    get_perp_top_of_book,
    get_perp_transaction_history,
    maker_price_inside_spread,
    resolve_perp_market,
    round_tick,
    set_perp_leverage,
    size_amount,
    wait_for_perp_fill,
)
from lib.poll import poll_until
from lib.report import record, record_check, step, stepping
from lib.risk_tiers import get_market_risk_tiers


def long_size(positions) -> float:
    return sum(float(p.amount) for p in positions if p.direction == "long")


# module state so teardown restores the injected mark even if the test throws (blast-radius safety).
state: dict = {"restore": None}


@pytest.fixture(scope="module", autouse=True)
def _restore_mark(env_cfg, clients):
    yield
    if not state["restore"]:
        return
    faucet = clients.make(env_cfg.faucet_url).client if env_cfg.faucet_url else None
    if faucet:
        restore_mark_price(faucet, state["restore"]["market"], state["restore"]["mark"])


def _open_long_vs_maker(subject, maker, mkt, notional_usd: float, leverage: int) -> tuple[float, float]:
    """subject opens a LONG of `notional_usd` against maker's resting sell. returns (size, entry)."""
    mark = get_perp_mark_price(subject.trading_client, mkt.market)
    amount = size_amount(notional_usd, mark, mkt)
    open_top = get_perp_top_of_book(subject.trading_client, mkt.market)
    create_perp_order(maker.order_client, maker.app_session_id, market=mkt.market, side="sell",
                      direction="short", type="limit", amount=amount,
                      price=maker_price_inside_spread("sell", open_top, mark, mkt), tif="gtc", leverage=leverage)
    open_uuid = create_perp_order(subject.order_client, subject.app_session_id, market=mkt.market,
                                  side="buy", direction="long", type="market", amount=amount, leverage=leverage)
    wait_for_perp_fill(subject.trading_client, subject.app_session_id, mkt.market, open_uuid, 20)
    poll_until(
        lambda: long_size(get_perp_positions(subject.trading_client, subject.app_session_id, mkt.market)),
        lambda size: size > 0, timeout_s=20, message="subject long opened",
    )
    positions = get_perp_positions(subject.trading_client, subject.app_session_id, mkt.market)
    open_long = long_size(positions)
    long_row = next(p for p in positions if p.direction == "long")
    entry = float(long_row.entry_price or get_perp_mark_price(subject.trading_client, mkt.market))
    return open_long, entry


@pytest.mark.serial
class TestPerpTieredPositionReduction:
    @pytest.mark.timeout(900)  # two provisions + open + stepwise drops (each holds several seconds)
    def test_1_liquidation_reduces_position_piece_by_piece_not_all_at_once(self, env, clients, new_funded_account):
        # arrange — faucet-host client for mark injection (uat only).
        assert env.faucet_url, "faucet host needed for mark injection (uat)"
        faucet = env.client_for(None, env.faucet_url)

        # fresh funded cross accounts: subject (gets liquidated) + maker (the reduce counterparty).
        subject = step("provision subject", lambda: new_funded_account(
            spot_usdt=cfg.subject_deposit_usdt, perp_usdt=cfg.subject_deposit_usdt, role="liq-subject"))
        maker = step("provision maker", lambda: new_funded_account(
            spot_usdt=cfg.maker_deposit_usdt, perp_usdt=cfg.maker_deposit_usdt, role="liq-maker"))

        mkt = resolve_perp_market(subject.trading_client, cfg.market)
        mark = get_perp_mark_price(subject.trading_client, mkt.market)
        assert mark > 0, "mark price available"
        state["restore"] = {"market": mkt.market, "mark": str(mark)}

        # open notional must reach tier 3+ so several 1-tier reductions can happen (SUI: > tier-2 cap 250k).
        tiers = get_market_risk_tiers(subject.trading_client, mkt.market)
        assert len(tiers) >= 3, "multi-tier ladder"
        assert cfg.open_notional_usd > tiers[1].max_notional_quote, "open notional reaches tier 3+ (multiple pieces possible)"

        # token ttl 60s. provision+reads can eat it. refresh before first trade call.
        refresh_account_token(env.cfg, clients, subject)
        refresh_account_token(env.cfg, clients, maker)
        set_perp_leverage(subject.order_client, subject.app_session_id, mkt.market, cfg.leverage)
        set_perp_leverage(maker.order_client, maker.app_session_id, mkt.market, cfg.leverage)

        # act 1 — subject opens a tier-3 LONG against maker's resting sell.
        with stepping("subject opens tier-3 long"):
            open_long, entry = _open_long_vs_maker(subject, maker, mkt, cfg.open_notional_usd, cfg.leverage)
        record("subject opened", {"openLong": open_long, "entry": entry, "notional": open_long * entry})

        # act 2 — the COUNTERPARTY: maker rests a big BID near entry (insurance fund not live ->
        # the reduce IOC must match a real order). near-entry -> each reduce fills ~PnL-neutral.
        amount = size_amount(cfg.open_notional_usd, entry, mkt)
        reduce_bid = round_tick(entry * (1 - cfg.maker_bid_below_entry_pct), mkt.tick_size, mkt.price_precision)
        step("maker rests reduce counterparty bid near entry",
             lambda: create_perp_order(maker.order_client, maker.app_session_id, market=mkt.market,
                                       side="buy", direction="long", type="limit", amount=amount,
                                       price=reduce_bid, tif="gtc", leverage=cfg.leverage))

        partials_before = len(get_perp_transaction_history(
            subject.trading_client, subject.app_session_id,
            type="liquidation_partial", market=mkt.market, page_size=200).items)

        # act 3 — drop the mark in small steps; each step peels one tier. (holds/re-injects internally.)
        # ttl 60s, max_steps x step_hold_s can run minutes. size_of refreshes token each poll.
        size_of = auto_refreshing(
            env.cfg, clients, subject,
            lambda: long_size(get_perp_positions(subject.trading_client, subject.app_session_id, mkt.market)),
        )
        steps = step("drive stepwise liquidation", lambda: drive_stepwise_liquidation(
            faucet=faucet, market=mkt.market, entry=entry,
            round_tick=lambda x: round_tick(x, mkt.tick_size, mkt.price_precision),
            step_drop_pct=cfg.step_drop_pct, max_steps=cfg.max_steps, step_hold_s=cfg.step_hold_s,
            size_of=size_of,
        ))
        restore_mark_price(faucet, mkt.market, str(mark))  # restore ASAP (blast radius)

        # gather outcomes.
        partials = get_perp_transaction_history(subject.trading_client, subject.app_session_id,
                                                type="liquidation_partial", market=mkt.market, page_size=200).items
        reductions = [s for s in steps if s.size_after < s.size_before - 1e-9]
        intermediate_open = any(0 < s.size_after < open_long - 1e-9 for s in steps)
        final_size = steps[-1].size_after if steps else open_long
        record("stepwise result", {"openLong": open_long, "finalSize": final_size,
                                   "reductionSteps": len(reductions), "partialRows": len(partials) - partials_before,
                                   "steps": [s.__dict__ for s in steps]})

        # assert — PIECEWISE: a partial-reduction row (Stage0 one-tier peel, NOT a full close),
        # size shrank, and the position was observed OPEN at an intermediate size (proves it was
        # NOT closed all at once). as-built: 1 partial peel then Stage1 full-close -> min_pieces=1.
        record_check(name=f">= {cfg.min_pieces} LIQUIDATION_PARTIAL rows (piecewise, not a full close)",
                     passed=len(partials) - partials_before >= cfg.min_pieces,
                     detail={"before": partials_before, "after": len(partials)})
        record_check(name="size shrank at a partial-reduction step", passed=len(reductions) >= cfg.min_pieces,
                     detail=[r.__dict__ for r in reductions])
        record_check(name="observed open at an intermediate size (not all-at-once)", passed=intermediate_open,
                     detail={"openLong": open_long, "finalSize": final_size})
        assert len(partials) - partials_before >= cfg.min_pieces, \
            "LIQUIDATION_PARTIAL ledger row (partial reduce, not a full close)"
        assert len(reductions) >= cfg.min_pieces, "position shrank at a partial-reduction step"
        assert intermediate_open, "liquidated by pieces (open at an intermediate size), not all at once"

        # cleanup: flatten both disposable accounts. maker token stale since provisioning, refresh first.
        refresh_account_token(env.cfg, clients, subject)
        refresh_account_token(env.cfg, clients, maker)
        close_all_perp_positions(subject.order_client, subject.app_session_id, mkt.market)
        close_all_perp_positions(maker.order_client, maker.app_session_id, mkt.market)

    # TC-LIQ-030: reduce by EXACTLY one tier, then the account UNLOCKS (stays open, healthy).
    # fat deposit on a tier-2 position so ONE reduction to tier-1 leaves equity >> tier-1
    # maintenance -> ladder heals and stops after one tier (no cascade).
    @pytest.mark.timeout(900)
    def test_2_tc_liq_030_liquidation_reduces_exactly_one_tier_and_account_unlocks(self, env, clients, new_funded_account):
        assert env.faucet_url, "faucet host needed for mark injection (uat)"
        faucet = env.client_for(None, env.faucet_url)
        subject = step("provision subject", lambda: new_funded_account(
            spot_usdt=cfg.one_tier_subject_deposit_usdt, perp_usdt=cfg.one_tier_subject_deposit_usdt, role="liq1-subject"))
        maker = step("provision maker", lambda: new_funded_account(
            spot_usdt=cfg.maker_deposit_usdt, perp_usdt=cfg.maker_deposit_usdt, role="liq1-maker"))

        mkt = resolve_perp_market(subject.trading_client, cfg.market)
        mark = get_perp_mark_price(subject.trading_client, mkt.market)
        assert mark > 0, "mark price available"
        state["restore"] = {"market": mkt.market, "mark": str(mark)}

        tiers = get_market_risk_tiers(subject.trading_client, mkt.market)
        tier1_cap = tiers[0].max_notional_quote  # reduce target: tier-1 upper limit
        assert cfg.one_tier_open_notional_usd > tier1_cap, "open notional in tier 2 (> tier-1 cap)"
        assert cfg.one_tier_open_notional_usd <= tiers[1].max_notional_quote, "open notional in tier 2"

        # token ttl 60s. provision+reads can eat it. refresh before first trade call.
        refresh_account_token(env.cfg, clients, subject)
        refresh_account_token(env.cfg, clients, maker)
        set_perp_leverage(subject.order_client, subject.app_session_id, mkt.market, cfg.leverage)
        set_perp_leverage(maker.order_client, maker.app_session_id, mkt.market, cfg.leverage)

        # open subject tier-2 long vs maker.
        with stepping("subject opens tier-2 long"):
            open_long, entry = _open_long_vs_maker(subject, maker, mkt, cfg.one_tier_open_notional_usd, cfg.leverage)

        # counterparty reduce bid near entry (insurance fund not live).
        amount = size_amount(cfg.one_tier_open_notional_usd, entry, mkt)
        step("maker rests reduce counterparty bid near entry",
             lambda: create_perp_order(maker.order_client, maker.app_session_id, market=mkt.market,
                                       side="buy", direction="long", type="limit", amount=amount,
                                       price=round_tick(entry * (1 - cfg.maker_bid_below_entry_pct),
                                                        mkt.tick_size, mkt.price_precision),
                                       tif="gtc", leverage=cfg.leverage))

        partials_before = len(get_perp_transaction_history(
            subject.trading_client, subject.app_session_id,
            type="liquidation_partial", market=mkt.market, page_size=200).items)

        # drive down, STOP after the first reduction (max_pieces=1), then restore the mark.
        # ttl 60s, max_steps x step_hold_s can run minutes. size_of refreshes token each poll.
        size_of = auto_refreshing(
            env.cfg, clients, subject,
            lambda: long_size(get_perp_positions(subject.trading_client, subject.app_session_id, mkt.market)),
        )
        steps = step("drive one-tier reduction", lambda: drive_stepwise_liquidation(
            faucet=faucet, market=mkt.market, entry=entry,
            round_tick=lambda x: round_tick(x, mkt.tick_size, mkt.price_precision),
            step_drop_pct=cfg.step_drop_pct, max_steps=cfg.max_steps, step_hold_s=cfg.step_hold_s,
            max_pieces=1, size_of=size_of,
        ))
        restore_mark_price(faucet, mkt.market, str(mark))

        # after restore (higher mark), confirm the reduced position is STABLE + open (unlocked, healthy).
        poll_until(size_of, lambda size: size > 0, timeout_s=15, intervals=(2, 3),
                   message="reduced position still open after restore")
        long_after = size_of()
        partials = get_perp_transaction_history(subject.trading_client, subject.app_session_id,
                                                type="liquidation_partial", market=mkt.market, page_size=200).items
        reductions = [s for s in steps if s.size_after < s.size_before - 1e-9]
        # price reduction at the mark active when it happened, not the later-restored one --
        # mixing price-A reduction with price-B (post-restore) notional is an unbounded confound.
        reduction_mark = float(reductions[0].level) if reductions else mark
        reduced_notional = long_after * reduction_mark
        record("one-tier result", {"openLong": open_long, "longAfter": long_after,
                                   "reducedNotional": reduced_notional, "reductionMark": reduction_mark,
                                   "restoredMark": mark, "tier1Cap": tier1_cap,
                                   "reductionSteps": len(reductions),
                                   "partialRows": len(partials) - partials_before,
                                   "steps": [s.__dict__ for s in steps]})

        # assert — exactly one tier peeled, position OPEN (not fully liquidated), reduced to ~tier-1 cap.
        record_check(name="exactly one reduction step", passed=len(reductions) == 1,
                     detail=[r.__dict__ for r in reductions])
        record_check(name="a LIQUIDATION_PARTIAL row appeared", passed=len(partials) > partials_before,
                     detail={"before": partials_before, "after": len(partials)})
        record_check(name="position still OPEN after restore (unlocked, not full-closed)", passed=long_after > 0,
                     detail={"openLong": open_long, "longAfter": long_after})
        record_check(name="reduced to ~tier-1 cap (one tier down)",
                     passed=tier1_cap * 0.75 < reduced_notional <= tier1_cap * 1.15,
                     detail={"reducedNotional": reduced_notional, "tier1Cap": tier1_cap})
        assert len(reductions) == 1, "exactly one tier reduction"
        assert len(partials) > partials_before, "a LIQUIDATION_PARTIAL row appeared"
        assert long_after > 0, "position remains open (not fully liquidated)"
        assert long_after < open_long, "position was reduced"
        assert reduced_notional <= tier1_cap * 1.15, "reduced roughly to the tier-1 upper limit"

        # maker token stale since provisioning, refresh first. subject's stayed fresh via size_of.
        refresh_account_token(env.cfg, clients, maker)
        close_all_perp_positions(subject.order_client, subject.app_session_id, mkt.market)
        close_all_perp_positions(maker.order_client, maker.app_session_id, mkt.market)
