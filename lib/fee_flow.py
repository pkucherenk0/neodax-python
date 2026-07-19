"""competition volume driver. port of lib/fee-flow.ts.

enrolled SUBJECT always take. non-enrolled MAKER rest one tick inside spread -> guaranteed
counterparty. HEDGE round-trip stay FLAT so margin freed each cycle. taker work LONG leg,
maker SHORT leg, close legs reduce_only. only SUBJECT taker fills count to competition volume.
"""
from __future__ import annotations

from dataclasses import dataclass

from lib.http import ResilientClient
from lib.perp import (
    PerpFillSummary,
    PerpMarket,
    close_all_perp_positions,
    create_perp_order,
    get_perp_mark_price,
    get_perp_top_of_book,
    maker_price_inside_spread,
    size_amount,
    wait_for_perp_fill,
)


@dataclass(frozen=True)
class VolumeParticipant:
    order_client: ResilientClient  # place orders. retry_on_5xx=False -> no double execution
    trading_client: ResilientClient  # read fills
    app_session_id: str


@dataclass(frozen=True)
class MakerRef:
    order_client: ResilientClient
    app_session_id: str


@dataclass(frozen=True)
class DriveVolumeResult:
    traded_volume_usd: float  # subject taker notional summed over both legs every cycle
    cycles: int
    skipped: int  # legs with no fill (book/cross issue). surfaced, never silently dropped


def _leg(
    subject: VolumeParticipant, maker: MakerRef, market: PerpMarket, leverage: float,
    rest_side: str, amount: str, mark_price: float, reduce_only: bool, fill_timeout_s: float,
) -> float:
    """one market-vs-limit leg. maker rest `rest_side` inside spread, subject take other side.
    return subject filled notional. 0 if fill never appears."""
    top = get_perp_top_of_book(subject.trading_client, market.market)
    maker_price = maker_price_inside_spread(rest_side, top, mark_price, market)
    create_perp_order(maker.order_client, maker.app_session_id, market=market.market, side=rest_side,
                      direction="short", type="limit", amount=amount, price=maker_price, tif="gtc",
                      reduce_only=reduce_only, leverage=leverage)  # maker hold SHORT leg across open+close
    taker_uuid = create_perp_order(subject.order_client, subject.app_session_id, market=market.market,
                                   side="buy" if rest_side == "sell" else "sell",  # take opposite side
                                   direction="long", type="market", amount=amount,
                                   reduce_only=reduce_only, leverage=leverage)  # taker hold LONG leg
    fill = wait_for_perp_fill(subject.trading_client, subject.app_session_id, market.market, taker_uuid, fill_timeout_s)
    return fill.notional


def drive_competition_volume(
    *,
    subject: VolumeParticipant,
    maker: MakerRef,
    market: PerpMarket,
    leverage: float,
    order_notional_usd: float,
    target_volume_usd: float,  # stop when subject cumulative traded notional reach this
    max_cycles: int,  # safety cap on round-trips
    fill_timeout_s: float = 15.0,
) -> DriveVolumeResult:
    """round-trip perp open+close cycles until subject cumulative taker notional reach target
    (or max_cycles). re-read mark price each cycle so sizing tracks live market."""
    traded = 0.0
    cycles = 0
    skipped = 0
    while traded < target_volume_usd and cycles < max_cycles:
        cycles += 1
        mark_open = get_perp_mark_price(subject.trading_client, market.market)
        amount = size_amount(order_notional_usd, mark_open, market)
        # OPEN: maker sell/short (new best ask), subject buy/long lift it.
        open_notional = _leg(subject, maker, market, leverage, "sell", amount, mark_open, False, fill_timeout_s)
        if open_notional > 0:
            traded += open_notional
        else:
            skipped += 1
        # CLOSE: maker buy/short reduce_only (new best bid), subject sell/long reduce_only hit it -> flat.
        mark_close = get_perp_mark_price(subject.trading_client, market.market)
        close_notional = _leg(subject, maker, market, leverage, "buy", amount, mark_close or mark_open, True, fill_timeout_s)
        if close_notional > 0:
            traded += close_notional
        else:
            skipped += 1
    return DriveVolumeResult(traded_volume_usd=traded, cycles=cycles, skipped=skipped)


def capture_subject_maker_fill(
    *,
    subject: VolumeParticipant,  # rest maker order + read its fill
    maker: MakerRef,  # take with market order
    market: PerpMarket,
    leverage: float,
    order_notional_usd: float,
    max_attempts: int = 3,
    fill_timeout_s: float = 15.0,
) -> PerpFillSummary | None:
    """capture ONE clean MAKER fill for subject. subject rest sell one tick inside spread,
    counterparty lift with market order. book can move between top-of-book read and placement
    -> RETRY with fresh book, flatten positions between attempts. return None if no clean
    maker fill in max_attempts."""
    for _ in range(max_attempts):
        mark = get_perp_mark_price(subject.trading_client, market.market)
        amount = size_amount(order_notional_usd, mark, market)
        top = get_perp_top_of_book(subject.trading_client, market.market)
        price = maker_price_inside_spread("sell", top, mark, market)
        maker_uuid = create_perp_order(subject.order_client, subject.app_session_id, market=market.market,
                                       side="sell", direction="short", type="limit", amount=amount,
                                       price=price, tif="gtc", leverage=leverage)
        create_perp_order(maker.order_client, maker.app_session_id, market=market.market, side="buy",
                          direction="long", type="market", amount=amount, leverage=leverage)
        fill = wait_for_perp_fill(subject.trading_client, subject.app_session_id, market.market, maker_uuid, fill_timeout_s)
        if fill.fills > 0 and fill.is_maker:
            return fill  # got clean maker fill
        # book moved. flatten both sides, retry with fresh quote.
        close_all_perp_positions(subject.order_client, subject.app_session_id, market.market)
        close_all_perp_positions(maker.order_client, maker.app_session_id, market.market)
    return None
