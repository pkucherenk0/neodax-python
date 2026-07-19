"""smoke. fail fast. port of suites/health.spec.ts.

env unreachable / misconfigured -> downstream suites fail confusing. this fail first, clear.
"""
import pytest

from configs.competition import competition_slug


@pytest.mark.smoke
@pytest.mark.stateless
class TestEnvironmentHealth:
    def test_the_configured_competition_is_reachable_and_served_200(self, env):
        # arrange
        client = env.client_for()

        # act — public GET of the configured competition (the dedicated test competition MUST exist).
        res = client.get(f"/api/v1/competitions/{competition_slug}")

        # assert — 200, not just "< 500". a 404 here = misconfigured slug (worth failing), a 5xx = env down.
        # (§13: specific status, not a wide band that a broken/missing endpoint sails past.)
        assert res.status == 200, f"{env.name} ({env.base_url}) GET competition {competition_slug} -> {res.status}"
