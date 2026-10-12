"""
Simple in-memory sliding-window rate limiter for /ask specifically - the
one endpoint that costs real money per call. /query and /schema are free
local reads and aren't limited.

In-memory and per-process by design: fine for a single-instance demo
deployment. A multi-instance production deployment would move this state
to Redis so limits are enforced across processes, not per-process.
"""

import os
import time
from collections import defaultdict

from fastapi import HTTPException, Request

WINDOW_SECONDS = 60
MAX_REQUESTS_PER_WINDOW = int(os.environ.get("ASK_RATE_LIMIT_PER_MIN", "10"))

_hits = defaultdict(list)


def enforce_rate_limit(request: Request):
    client_id = request.client.host if request.client else "unknown"
    now = time.time()
    window_start = now - WINDOW_SECONDS

    hits = _hits[client_id]
    while hits and hits[0] < window_start:
        hits.pop(0)

    if len(hits) >= MAX_REQUESTS_PER_WINDOW:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {MAX_REQUESTS_PER_WINDOW} requests per {WINDOW_SECONDS}s",
        )

    hits.append(now)
