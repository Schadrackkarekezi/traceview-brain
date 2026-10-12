"""
Minimal shared-secret auth.

If API_KEY isn't set in the environment, auth is disabled - so local dev
and the existing test setup keep working with zero config. Setting API_KEY
(e.g. at deploy time) turns on real protection: every protected endpoint
then requires a matching X-API-Key header.

This is deliberately a single shared key, not per-user accounts - this is
a demo tool with no login system, and a shared key is the honest scope for
that. A real multi-tenant product would use per-user JWTs instead.
"""

import os

from fastapi import Header, HTTPException

API_KEY = os.environ.get("API_KEY")


def require_api_key(x_api_key: str = Header(default=None)):
    if API_KEY is None:
        return  # auth disabled - no key configured
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")
