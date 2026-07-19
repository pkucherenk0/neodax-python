"""Wallet + trading-account fixtures. specs get accounts, JWTs, and API clients from THESE
fixtures — never construct wallets or request contexts inline in a spec (CONVENTIONS §4).

safety: NO retries anywhere (pytest has none by default — keep it that way on @trades:
a retry re-places live orders -> double volume / lost funds).
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from configs.competition import competition_slug, funding
from fixtures.clients import ClientFactory
from lib.artifacts import record_account
from lib.funding import (
    faucet_deposit,
    get_perp_available,
    get_spot_available,
    transfer_spot_to_perp,
    wait_for_balance,
)
from lib.http import ResilientClient
from lib.schemas import AuthChallenge, AuthVerify
from lib.types import EnvConfig
from lib.validate import parsed_json

# funding setup take more 5xx than hot path. 6 retries ~ up to ~60s backoff.
# cover transient "validation_unavailable" 503 on faucet/transfer.
PATIENT_RETRIES = 6


@dataclass(frozen=True)
class FreshWallet:
    address: str
    jwt: str  # auth service access_token
    app_session_id: str  # == address. used as app_session_id in trading/faucet calls


@dataclass(frozen=True)
class TradingAccount:
    """funded + ENROLLED + ready-to-trade account (uat). competition subject."""

    address: str
    jwt: str
    app_session_id: str
    trading_client: ResilientClient  # reads/funding (retry 5xx)
    order_client: ResilientClient  # order place. NO retry 5xx. avoid double execution

    def spot_available(self, asset: str = "USDT") -> float:
        return get_spot_available(self.trading_client, self.app_session_id, asset)

    def perp_available(self, asset: str = "USDT") -> float:
        return get_perp_available(self.trading_client, self.app_session_id, asset)


@dataclass(frozen=True)
class MakerAccount:
    """counterparty. spot flavor holds base inventory (ETH); perp flavor holds USDT collateral."""

    app_session_id: str
    trading_client: ResilientClient
    order_client: ResilientClient


def _sig_hex(signature: bytes) -> str:
    hexed = signature.hex()
    return hexed if hexed.startswith("0x") else "0x" + hexed


def _authenticate(cfg: EnvConfig, factory: ClientFactory, wallet) -> FreshWallet:
    """auth: wallet challenge -> sign -> verify -> access_token. one place to point at live auth."""
    handle = factory.make(cfg.auth_base)
    try:
        challenge_res = handle.client.post("/auth/challenge", data={"wallet_address": wallet.address})
        challenge = parsed_json(challenge_res, AuthChallenge).challenge
        signed = Account.sign_message(encode_defunct(text=challenge), private_key=wallet.key)
        verify_res = handle.client.post("/auth/verify", data={
            "wallet_address": wallet.address, "challenge": challenge, "signature": _sig_hex(signed.signature),
        })
        token = parsed_json(verify_res, AuthVerify).access_token
        # app_session_id == wallet address in trading/faucet APIs.
        return FreshWallet(address=wallet.address, jwt=token, app_session_id=wallet.address)
    finally:
        handle.dispose()


def _mint_account(cfg: EnvConfig, factory: ClientFactory, role: str) -> FreshWallet:
    """mint random wallet, auth, SAVE creds (addr + private key + jwt) to artifacts
    (CONVENTIONS §12). throwaway UAT wallet. private key saved on purpose."""
    wallet = Account.create()
    fresh = _authenticate(cfg, factory, wallet)
    record_account(role=role, address=fresh.address, private_key=_sig_hex(wallet.key),
                   jwt=fresh.jwt, app_session_id=fresh.app_session_id)
    return fresh


def _fund_spot_then_perp(cfg: EnvConfig, factory: ClientFactory, fresh: FreshWallet,
                         trading: ResilientClient, spot_usdt: str, perp_usdt: str) -> None:
    """faucet spot USDT (async credit), wait settle, then move collateral spot -> perps."""
    faucet = factory.make(cfg.faucet_url, max_retries=PATIENT_RETRIES)
    try:
        faucet_deposit(faucet.client, fresh.app_session_id, spot_usdt)
        spot = wait_for_balance(lambda: get_spot_available(trading, fresh.app_session_id),
                                float(spot_usdt) * 0.9, funding.settle_timeout_s)
        if spot <= 0:
            raise AssertionError(f"spot faucet did not settle for {fresh.address} (available {spot})")
    finally:
        faucet.dispose()
    transfer_spot_to_perp(trading, fresh.app_session_id, perp_usdt)
    perp = wait_for_balance(lambda: get_perp_available(trading, fresh.app_session_id),
                            float(perp_usdt) * 0.9, funding.settle_timeout_s)
    if perp <= 0:
        raise AssertionError(f"perp transfer did not settle for {fresh.address} (available {perp})")


def _provision_funded(cfg: EnvConfig, factory: ClientFactory, *, spot_usdt: str, perp_usdt: str,
                      role: str) -> TradingAccount:
    """FRESH funded account: faucet spot USDT -> move to perps. NO enroll (liquidation tests
    don't need competition). cross margin (account default). uat only."""
    if not cfg.has_faucet or not cfg.faucet_url:
        raise RuntimeError("new_funded_account requires a faucet (uat).")
    fresh = _mint_account(cfg, factory, role)
    trading = factory.make(cfg.trading_base, fresh.jwt, max_retries=PATIENT_RETRIES)
    order = factory.make(cfg.trading_base, fresh.jwt, retry_on_5xx=False)
    _fund_spot_then_perp(cfg, factory, fresh, trading.client, spot_usdt, perp_usdt)
    return TradingAccount(address=fresh.address, jwt=fresh.jwt, app_session_id=fresh.app_session_id,
                          trading_client=trading.client, order_client=order.client)


@pytest.fixture
def fresh_wallet(env_cfg: EnvConfig, clients: ClientFactory):
    """factory: mint brand-new authenticated (but unfunded) wallet. for auth/enroll negative paths."""

    def _make() -> FreshWallet:
        return _mint_account(env_cfg, clients, "freshWallet")

    return _make


@pytest.fixture
def new_funded_account(env_cfg: EnvConfig, clients: ClientFactory):
    """factory: fresh FUNDED cross account per call (own wallet). disposable liquidation
    subjects/makers so a liquidated account never touches the shared `account`. uat only."""

    def _make(*, spot_usdt: str | None = None, perp_usdt: str | None = None, role: str = "disposable") -> TradingAccount:
        return _provision_funded(env_cfg, clients, spot_usdt=spot_usdt or funding.spot_usdt,
                                 perp_usdt=perp_usdt or funding.perp_usdt, role=role)

    return _make


@pytest.fixture(scope="session")
def account(env_cfg: EnvConfig, clients: ClientFactory) -> TradingAccount:
    """funded cross account, one per worker process (session scope == worker scope under
    xdist). parallel workers never share balances. uat only: faucet spot USDT, move collateral
    to perps. NOT enrolled in any competition — trading itself doesn't require it. use
    `enrolled_account` for suites/competition/ tests that assert fee-overlay/volume behavior.
    lazy."""
    if not env_cfg.has_faucet or not env_cfg.faucet_url:
        raise RuntimeError("The funded 'account' fixture requires a faucet (uat). "
                           "Stage pre-funded pool lease is not implemented yet.")
    return _provision_funded(env_cfg, clients, spot_usdt=funding.spot_usdt,
                             perp_usdt=funding.perp_usdt, role="account")


@pytest.fixture(scope="session")
def enrolled_account(account: TradingAccount, env_cfg: EnvConfig, clients: ClientFactory) -> TradingAccount:
    """`account`, additionally enrolled in the configured competition (idempotent: 201 new /
    200 already-enrolled). competition-scoped tests only — see suites/competition/."""
    hub = clients.make(env_cfg.base_url, account.jwt, max_retries=PATIENT_RETRIES)
    try:
        enroll = hub.client.post(f"/api/v1/competitions/{competition_slug}/enroll",
                                 data={"address": account.address, "terms_accepted": True})
        if enroll.status not in (200, 201):
            raise AssertionError(f"enroll into {competition_slug} failed: HTTP {enroll.status} {enroll.text()}")
    finally:
        hub.dispose()
    return account


@pytest.fixture(scope="session")
def spot_maker(env_cfg: EnvConfig, clients: ClientFactory) -> MakerAccount:
    """spot maker funded with base inventory (ETH) to rest limit orders. lazy, uat only."""
    if not env_cfg.has_faucet or not env_cfg.faucet_url:
        raise RuntimeError("The 'spot_maker' fixture requires a faucet (uat).")
    fresh = _mint_account(env_cfg, clients, "spotMaker")
    trading = clients.make(env_cfg.trading_base, fresh.jwt, max_retries=PATIENT_RETRIES)
    order = clients.make(env_cfg.trading_base, fresh.jwt, retry_on_5xx=False)
    faucet = clients.make(env_cfg.faucet_url, max_retries=PATIENT_RETRIES)
    try:
        faucet_deposit(faucet.client, fresh.app_session_id, funding.maker_eth, "ETH")
        eth = wait_for_balance(lambda: get_spot_available(trading.client, fresh.app_session_id, "ETH"),
                               float(funding.maker_eth) * 0.9, funding.settle_timeout_s)
        if eth <= 0:
            raise AssertionError(f"maker ETH faucet did not settle for {fresh.address} (available {eth})")
    finally:
        faucet.dispose()
    return MakerAccount(app_session_id=fresh.app_session_id, trading_client=trading.client, order_client=order.client)


@pytest.fixture(scope="session")
def perp_maker(env_cfg: EnvConfig, clients: ClientFactory) -> MakerAccount:
    """perp maker funded with USDT collateral (moved spot->perps) to rest limit orders.
    not enrolled — negative-control counterparty. lazy, uat only."""
    if not env_cfg.has_faucet or not env_cfg.faucet_url:
        raise RuntimeError("The 'perp_maker' fixture requires a faucet (uat).")
    fresh = _mint_account(env_cfg, clients, "perpMaker")
    trading = clients.make(env_cfg.trading_base, fresh.jwt, max_retries=PATIENT_RETRIES)
    order = clients.make(env_cfg.trading_base, fresh.jwt, retry_on_5xx=False)
    _fund_spot_then_perp(env_cfg, clients, fresh, trading.client, funding.spot_usdt, funding.perp_maker_usdt)
    return MakerAccount(app_session_id=fresh.app_session_id, trading_client=trading.client, order_client=order.client)
