"""harness fixtures. port of fixtures/index.ts + globalSetup.ts + reporters/detailed.ts.

specs get accounts, JWTs, and API clients from THESE fixtures — never construct wallets or
request contexts inline in a spec (CONVENTIONS §4). env is chosen with --env=uat|stage.

safety: NO retries anywhere (pytest has none by default — keep it that way on @trades:
a retry re-places live orders -> double volume / lost funds).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct
from playwright.sync_api import Playwright, sync_playwright

from config.competition import competition_slug, funding
from lib import report
from lib.artifacts import record_account, set_current_test
from lib.env import resolve_env
from lib.funding import (
    faucet_deposit,
    get_perp_available,
    get_spot_available,
    transfer_spot_to_perp,
    wait_for_balance,
)
from lib.http import DEFAULT_MAX_RETRIES, DEFAULT_RPS, RateLimiter, ResilientClient, ResilientOptions
from lib.run_context import create_run_context, run_dir
from lib.schemas import AuthChallenge, AuthVerify
from lib.types import EnvConfig
from lib.validate import parsed_json

load_dotenv()

# funding setup take more 5xx than hot path. 6 retries ~ up to ~60s backoff.
# cover transient "validation_unavailable" 503 on faucet/transfer.
PATIENT_RETRIES = 6

# one rate limiter per worker process. all clients (auth + trading) share it,
# so bursts across tests in a worker are throttled together. see lib/http.py.
_limiter = RateLimiter(DEFAULT_RPS)


# ---------- cli / markers ----------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--env", action="store", default="uat", choices=["uat", "stage"],
                     help="target environment (uat = fresh auto-funded wallets, stage = pre-funded pool)")


def pytest_configure(config: pytest.Config) -> None:
    # mint run dir once in the controlling process; xdist workers read it back via env/pointer.
    if not hasattr(config, "workerinput"):
        run_id, run_path = create_run_context()
        print(f"\nrun id: {run_id}  ->  {run_path}")


# ---------- playwright request-context plumbing ----------


@pytest.fixture(scope="session")
def _pw() -> Playwright:
    with sync_playwright() as pw:
        yield pw


@dataclass
class ClientHandle:
    client: ResilientClient
    dispose: object  # zero-arg callable


class ClientFactory:
    """make resilient clients for a host. tracks contexts for session-end disposal."""

    def __init__(self, pw: Playwright) -> None:
        self._pw = pw
        self._disposers: list = []

    def make(self, base_url: str, jwt: str | None = None, *, max_retries: int | None = None,
             retry_on_5xx: bool = True) -> ClientHandle:
        headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}
        raw = self._pw.request.new_context(base_url=base_url, extra_http_headers=headers)
        client = ResilientClient(raw, _limiter, ResilientOptions(
            max_retries=max_retries if max_retries is not None else DEFAULT_MAX_RETRIES,
            retry_on_5xx=retry_on_5xx,
        ))
        handle = ClientHandle(client=client, dispose=raw.dispose)
        self._disposers.append(raw.dispose)
        return handle

    def dispose_all(self) -> None:
        for d in self._disposers:
            try:
                d()
            except Exception:
                pass


@pytest.fixture(scope="session")
def clients(_pw: Playwright) -> ClientFactory:
    factory = ClientFactory(_pw)
    yield factory
    factory.dispose_all()


# ---------- env ----------


@dataclass
class EnvContext:
    """port of the TS EnvContext: EnvConfig + client_for()."""

    cfg: EnvConfig
    _factory: ClientFactory = field(repr=False, default=None)

    # convenience pass-throughs so specs read like the TS ones
    @property
    def name(self) -> str:
        return self.cfg.name

    @property
    def base_url(self) -> str:
        return self.cfg.base_url

    @property
    def auth_base(self) -> str:
        return self.cfg.auth_base

    @property
    def trading_base(self) -> str:
        return self.cfg.trading_base

    @property
    def faucet_url(self) -> str | None:
        return self.cfg.faucet_url

    @property
    def has_faucet(self) -> bool:
        return self.cfg.has_faucet

    def client_for(self, jwt: str | None = None, base_url: str | None = None) -> ResilientClient:
        """resilient client (rate-limit + retry 429/5xx/network). reads + idempotent enroll safe
        to retry on 5xx. @trades order-place client must use retry_on_5xx=False instead —
        see the account fixtures."""
        return self._factory.make(base_url or self.cfg.base_url, jwt).client


@pytest.fixture(scope="session")
def env_cfg(request: pytest.FixtureRequest) -> EnvConfig:
    return resolve_env(request.config.getoption("--env"))


@pytest.fixture
def env(env_cfg: EnvConfig, clients: ClientFactory) -> EnvContext:
    return EnvContext(cfg=env_cfg, _factory=clients)


# ---------- auth / accounts ----------


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


# ---------- per-test bookkeeping (artifacts + detailed report) ----------


@pytest.fixture(autouse=True)
def _artifacts(request: pytest.FixtureRequest):
    """auto per-test. stamp artifact records with the running test name. clear report buffer."""
    set_current_test(request.node.name)
    report.reset()
    yield
    set_current_test(None)


_test_reports: list[dict] = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    rep = outcome.get_result()
    if rep.when != "call":
        return
    detail = report.harvest()
    entry = {
        "suite": item.parent.name if item.parent else "",
        "title": item.name,
        "status": "passed" if rep.passed else ("skipped" if rep.skipped else "failed"),
        "durationMs": int(rep.duration * 1000),
        "markers": sorted({m.name for m in item.iter_markers()}),
        **detail,
    }
    if rep.failed and call.excinfo is not None:
        entry["error"] = "\n".join(str(call.excinfo.value).split("\n")[:4])
    _test_reports.append(entry)


def _safe_name(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:120]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """write results/runs/<runId>/detailed-report.md + detailed/<test>.json (port of
    reporters/detailed.ts). per-worker under xdist: workers append their own tests."""
    if not _test_reports:
        return
    base = Path(run_dir())
    detailed = base / "detailed"
    detailed.mkdir(parents=True, exist_ok=True)
    for r in _test_reports:
        (detailed / f"{_safe_name(r['suite'] + '-' + r['title'])}.json").write_text(json.dumps(r, indent=2, default=str))

    def sec(ms: int) -> str:
        return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"

    icon = {"passed": "✅", "skipped": "⏭️", "failed": "❌"}
    out: list[str] = [f"# Detailed execution report", "", f"{len(_test_reports)} tests (worker pid {os.getpid()})", ""]
    for r in _test_reports:
        out.append(f"## {icon.get(r['status'], '?')} {r['suite']} › {r['title']}")
        tags = " · ".join(r["markers"])
        out.append(f"_{r['status']} · {sec(r['durationMs'])}{' · ' + tags if tags else ''}_")
        out.append("")
        if r["actions"]:
            out.append("**Actions**")
            for a in r["actions"]:
                pad = "  " * a.get("depth", 0)
                err = f" — {a['error']}" if a.get("error") else ""
                out.append(f"{pad}- {'✓' if a['ok'] else '✗'} {a['title']} _({sec(a['durationMs'])})_{err}")
            out.append("")
        if r["checks"]:
            out.append("**Checks**")
            for c in r["checks"]:
                mark = "ℹ️" if c["info"] else ("✓" if c["pass"] else "✗")
                detail_str = f"  `{json.dumps(c['detail'], default=str)}`" if c["detail"] is not None else ""
                out.append(f"- {mark} {c['name']}{detail_str}")
            out.append("")
        if r["records"]:
            out.append("**Records**")
            for rec in r["records"]:
                out.append(f"- `{rec['name']}`: `{json.dumps(rec['body'], default=str)}`")
            out.append("")
        if r["notes"]:
            out.append("**Notes**")
            out.extend(f"- {n}" for n in r["notes"])
            out.append("")
        if r.get("error"):
            out.extend(["**Error**", "```", r["error"], "```", ""])
        out.append("")
    # per-worker file under xdist, single file otherwise.
    suffix = f"-{os.getpid()}" if os.environ.get("PYTEST_XDIST_WORKER") else ""
    (base / f"detailed-report{suffix}.md").write_text("\n".join(out))
