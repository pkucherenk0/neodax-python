"""HTTP client + env plumbing: how a spec gets a rate-limited, retrying client for a given
host, and the resolved --env config to build one against.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from playwright.sync_api import Playwright, sync_playwright

from lib.env import resolve_env
from lib.http import DEFAULT_MAX_RETRIES, DEFAULT_RPS, RateLimiter, ResilientClient, ResilientOptions
from lib.types import EnvConfig

# one rate limiter per worker process. all clients (auth + trading) share it,
# so bursts across tests in a worker are throttled together. see lib/http.py.
_limiter = RateLimiter(DEFAULT_RPS)


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
        see fixtures/accounts.py."""
        return self._factory.make(base_url or self.cfg.base_url, jwt).client


@pytest.fixture(scope="session")
def env_cfg(request: pytest.FixtureRequest) -> EnvConfig:
    return resolve_env(request.config.getoption("--env"))


@pytest.fixture
def env(env_cfg: EnvConfig, clients: ClientFactory) -> EnvContext:
    return EnvContext(cfg=env_cfg, _factory=clients)
