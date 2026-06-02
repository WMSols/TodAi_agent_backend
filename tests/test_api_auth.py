"""API auth helpers."""

from unittest.mock import MagicMock, patch

import pytest

from todai.api.auth import auth_required, normalize_login_name, resolve_user_id, verify_supabase_access_token


def test_auth_not_required_when_local(monkeypatch):
    monkeypatch.setenv("LOCAL", "true")
    assert auth_required() is False
    assert resolve_user_id(authorization=None, fallback_user_id="default") == "default"


def test_auth_required_when_supabase_mode(monkeypatch):
    monkeypatch.setenv("LOCAL", "false")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    assert auth_required() is True
    with pytest.raises(Exception):
        resolve_user_id(authorization=None, fallback_user_id="default")


def test_normalize_login_name():
    assert normalize_login_name("Ali Khan") == "alikhan"
    assert normalize_login_name("  x-y  ") == "xy"


def test_verify_token_accepts_gotrue_user_shape(monkeypatch):
    monkeypatch.setenv("LOCAL", "false")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": "uuid-1", "email": "a@b.com"}

    with patch("todai.api.auth.httpx.get", return_value=mock_resp):
        user = verify_supabase_access_token("fake-jwt")
    assert user["id"] == "uuid-1"
