"""liquidation drivers. port of lib/liquidation.ts.

drive a LONG position into PIECEWISE liquidation (YEN-2545): insurance fund is not live, so
each tier reduction is a ReduceOnly IOC that fills against a real book bid the CALLER must
seed (counterparty). we drop the mark in small steps and HOLD each level (a mark lasts ~2s,
so re-inject every poll); each step that shrinks the size is one "piece". never raises.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from lib.http import ResilientClient
from lib.mark_price import MarkHolder, MarkPriceInject, simulate_mark_price


@dataclass(frozen=True)
class LiquidationStep:
    step: int
    level: str  # injected mark this step
    size_before: float
    size_after: float


def drive_stepwise_liquidation(
    *,
    faucet: ResilientClient,
    market: str,
    entry: float,
    round_tick: Callable[[float], str],  # tick-align a price
    step_drop_pct: float,  # each step drops mark this much below entry (step * pct)
    max_steps: int,
    step_hold_s: float,  # hold each level up to this long (waiting for the scanner + ingest)
    size_of: Callable[[], float],  # current long size
    poll_s: float = 1.5,
    max_pieces: float = float("inf"),  # stop after this many reductions (1 = single-tier TC-LIQ-030)
) -> list[LiquidationStep]:
    steps: list[LiquidationStep] = []
    reductions = 0
    for n in range(1, max_steps + 1):
        size_before = size_of()
        if size_before <= 0:
            break
        level = round_tick(entry * (1 - step_drop_pct * n))
        deadline = time.monotonic() + step_hold_s
        size_after = size_before
        while time.monotonic() < deadline:
            try:
                simulate_mark_price(faucet, MarkPriceInject(market=market, mark_price=level, index_price=level))
            except Exception:
                pass
            time.sleep(poll_s)
            size_after = size_of()
            if size_after < size_before - 1e-9:
                break  # reduced at this level -> next (deeper) step
        steps.append(LiquidationStep(step=n, level=level, size_before=size_before, size_after=size_after))
        if size_after < size_before - 1e-9:
            reductions += 1
        if size_after <= 0 or reductions >= max_pieces:
            break  # flat, or reached the piece cap -> stop injecting
    return steps


@dataclass(frozen=True)
class FullLiquidationResult:
    triggered: bool  # all target legs reached ~0 (account fully liquidated) before timeout
    size_before: float
    size_after: float
    injections: list[MarkPriceInject] = field(default_factory=list)


def drive_cross_account_liquidation(
    *,
    faucet: ResilientClient,
    injections: list[MarkPriceInject],  # one per market to move; held concurrently
    total_size_of: Callable[[], float],  # abs open size across all legs (0 when fully liquidated)
    timeout_s: float,
    poll_s: float = 1.5,
) -> FullLiquidationResult:
    """drive a whole CROSS account into FULL liquidation (Stage1 batch takeover) by holding one
    or more adverse marks at once. YEN-3325 needs the MULTI-LEG batch path: a deeply-crashed
    LONG mark drags shared equity negative so a SHORT sibling leg's allocated settlement price
    goes <=0 (clamped). the takeover uses the settlement pool (no book counterparty).

    polls total_size_of until ~0 or timeout. never raises (inject is best-effort).
    caller MUST restore every injected mark after.
    """
    size_before = total_size_of()
    holder = MarkHolder(faucet, injections, interval_s=1.5)
    deadline = time.monotonic() + timeout_s
    size_after = size_before
    while time.monotonic() < deadline:
        holder.pump()  # keep every injected extreme visible to each 2s scanner window
        time.sleep(poll_s)
        size_after = total_size_of()
        if size_after <= 1e-9:
            break  # account fully liquidated
    return FullLiquidationResult(triggered=size_after <= 1e-9, size_before=size_before,
                                 size_after=size_after, injections=injections)
