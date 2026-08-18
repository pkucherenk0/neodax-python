"""reference tests. copy to add new stateless case.

@stateless: independent, parallel-safe. no funding, no trades.
pattern: arrange via fixtures -> act via api client -> assert status AND validated shape.

deterministic contracts (terms, not-enrollable) asserted always. configured comp is always-on
test comp, so MUST be enrollable. non-enrollable = failure (misconfigured/ended comp), not skip.
"""
import pytest

from configs.competition import competition_slug, is_enrollable, settled_competition_slug
from lib.schemas import CompetitionSchedule, EnrollResponse, ErrorResponse
from lib.validate import parsed_json


@pytest.mark.stateless
class TestCompetitionEnrollment:
    def test_rejects_enrollment_when_terms_are_not_accepted_422(self, fresh_wallet, env):
        # arrange: new authed wallet, terms NOT accepted.
        wallet = fresh_wallet()
        client = env.client_for(wallet.jwt)

        # act: terms checked before enrollability, so holds on any comp.
        res = client.post(f"/api/v1/competitions/{competition_slug}/enroll",
                          data={"address": wallet.address, "terms_accepted": False})

        # assert: status + validated error contract. error nested under error field.
        assert res.status == 422, "terms-not-accepted rejected 422"
        body = parsed_json(res, ErrorResponse)
        assert body.error.code == "TERMS_NOT_ACCEPTED", "error code identifies the missing-terms reason"

    def test_rejects_enrollment_for_a_settled_competition_409(self, fresh_wallet, env):
        # arrange: valid terms-accepted request vs known-settled comp.
        wallet = fresh_wallet()
        client = env.client_for(wallet.jwt)

        # act
        res = client.post(f"/api/v1/competitions/{settled_competition_slug}/enroll",
                          data={"address": wallet.address, "terms_accepted": True})

        # assert: closed comp rejects enroll with specific business-rule error.
        assert res.status == 409, "settled competition rejects enroll 409"
        body = parsed_json(res, ErrorResponse)
        assert body.error.code == "COMPETITION_NOT_ENROLLABLE", "error code identifies the not-enrollable reason"

    # note: enroll-before-start is known api bug (not-started comps wrongly enrollable),
    # tracked in notion, not asserted here.

    def test_accepts_enrollment_for_the_competition(self, fresh_wallet, env):
        # arrange: configured comp must be enrollable. fail (not skip) if not.
        schedule = parsed_json(env.client_for().get(f"/api/v1/competitions/{competition_slug}"),
                               CompetitionSchedule)
        assert is_enrollable(schedule.status), \
            f'competition "{competition_slug}" must be enrollable but is "{schedule.status}"'

        wallet = fresh_wallet()
        client = env.client_for(wallet.jwt)

        # act
        res = client.post(f"/api/v1/competitions/{competition_slug}/enroll",
                          data={"address": wallet.address, "terms_accepted": True})

        # assert: 201 enrolled / 200 already-enrolled. owner is this wallet.
        assert res.status in (200, 201), "enroll accepted (201 new / 200 already-enrolled)"
        body = parsed_json(res, EnrollResponse)
        assert body.owner_address.lower() == wallet.address.lower(), "enrollment owner is this wallet"
