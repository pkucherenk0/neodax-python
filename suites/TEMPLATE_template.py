"""COPY ME to add test. rename to suites/<domain>/test_<feature>.py. delete this banner.
file is `TEMPLATE_template.py` not `test_*.py` -> pytest not discover it.
canonical shape. see CONVENTIONS.md for rules.
"""
import pytest


# mark class with exactly ONE lane: stateless | trades | serial.
@pytest.mark.stateless
class TestFeature:
    # name = observable behavior. present tense. cause -> effect.
    def test_does_x_when_condition_y(self, fresh_wallet, env):
        # arrange — inputs via fixtures only. no asserts here.
        wallet = fresh_wallet()
        client = env.client_for(wallet.jwt)

        # act — single action under test.
        res = client.get("/api/v1/<resource>")

        # assert — check outcome vs ground truth. every test asserts once min.
        assert res.ok, "request succeeded"

        # wait on condition? use bounded poll. NEVER time.sleep:
        #   from lib.poll import poll_until
        #   poll_until(lambda: client.get('/x').status, lambda s: s == 200,
        #              timeout_s=10, message='endpoint became ready')
