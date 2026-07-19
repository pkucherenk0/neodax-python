"""FE (browser) E2E config — port of config/e2e.ts. see TEST_STRATEGY.md.

suite under test = UAT deployment of yellow-neodax-client (Next.js). browser E2E reuses the
API auth flow to inject session, so app boots authenticated without driving wallet-connect UI.
"""
from __future__ import annotations

import os

# UAT FE origin. override with NEODAX_FE_BASE to point at other deployment/preview.
fe_base_url = os.environ.get("NEODAX_FE_BASE", "https://yellow-neodax-client-uat.openware-account.workers.dev")

# FE persists auth as RAW strings in localStorage under these keys (yellow-neodax-client
# tokenStorage). seeding them before boot IS the whole session-injection mechanism.
FE_ACCESS_TOKEN_KEY = os.environ.get("NEODAX_FE_ACCESS_TOKEN_KEY", "access_token")
FE_REFRESH_TOKEN_KEY = os.environ.get("NEODAX_FE_REFRESH_TOKEN_KEY", "refresh_token")
