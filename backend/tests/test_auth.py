import pytest
from fastapi import HTTPException

import app.auth as auth


def test_auth_disabled_when_no_key_configured(monkeypatch):
    monkeypatch.setattr(auth, "API_KEY", None)
    auth.require_api_key(x_api_key=None)  # must not raise
    auth.require_api_key(x_api_key="anything")  # must not raise either


def test_auth_rejects_missing_header_when_key_configured(monkeypatch):
    monkeypatch.setattr(auth, "API_KEY", "secret123")
    with pytest.raises(HTTPException) as exc_info:
        auth.require_api_key(x_api_key=None)
    assert exc_info.value.status_code == 401


def test_auth_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(auth, "API_KEY", "secret123")
    with pytest.raises(HTTPException):
        auth.require_api_key(x_api_key="wrong")


def test_auth_accepts_matching_key(monkeypatch):
    monkeypatch.setattr(auth, "API_KEY", "secret123")
    auth.require_api_key(x_api_key="secret123")  # must not raise
