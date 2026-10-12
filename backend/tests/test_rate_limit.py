from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.rate_limit as rate_limit


def fake_request(ip="1.2.3.4"):
    return SimpleNamespace(client=SimpleNamespace(host=ip))


@pytest.fixture(autouse=True)
def clear_hits():
    rate_limit._hits.clear()
    yield
    rate_limit._hits.clear()


def test_requests_under_limit_pass(monkeypatch):
    monkeypatch.setattr(rate_limit, "MAX_REQUESTS_PER_WINDOW", 3)
    req = fake_request()
    rate_limit.enforce_rate_limit(req)
    rate_limit.enforce_rate_limit(req)
    rate_limit.enforce_rate_limit(req)  # 3rd call, still at the limit


def test_request_over_limit_is_rejected(monkeypatch):
    monkeypatch.setattr(rate_limit, "MAX_REQUESTS_PER_WINDOW", 2)
    req = fake_request()
    rate_limit.enforce_rate_limit(req)
    rate_limit.enforce_rate_limit(req)
    with pytest.raises(HTTPException) as exc_info:
        rate_limit.enforce_rate_limit(req)
    assert exc_info.value.status_code == 429


def test_different_clients_have_independent_limits(monkeypatch):
    monkeypatch.setattr(rate_limit, "MAX_REQUESTS_PER_WINDOW", 1)
    rate_limit.enforce_rate_limit(fake_request("1.1.1.1"))
    rate_limit.enforce_rate_limit(fake_request("2.2.2.2"))  # different IP, own budget

