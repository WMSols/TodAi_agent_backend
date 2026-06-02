"""Supabase Auth — JWT verification and user id resolution for API routes."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import Header, HTTPException

from todai.database.config import (
    supabase_anon_key,
    supabase_configured,
    supabase_url,
    use_local_storage,
)

log = logging.getLogger("todai.auth")


def auth_required() -> bool:
    """When True, API routes require Authorization: Bearer <access_token>."""
    if use_local_storage():
        return False
    return supabase_configured()


def public_auth_config() -> dict[str, Any]:
    return {
        "auth_required": auth_required(),
        "local_json": use_local_storage(),
        "storage": "local" if use_local_storage() else "supabase",
        "supabase_url": supabase_url() if supabase_configured() else None,
        "supabase_anon_key": supabase_anon_key() if supabase_configured() else None,
    }


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def verify_supabase_access_token(token: str) -> dict[str, Any]:
    """Validate JWT via Supabase Auth API; returns the user object."""
    if not supabase_configured():
        raise HTTPException(status_code=503, detail="Supabase not configured")
    base = supabase_url().rstrip("/")
    try:
        r = httpx.get(
            f"{base}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": supabase_anon_key(),
            },
            timeout=15.0,
        )
    except httpx.HTTPError as e:
        log.warning("auth verify network error: %s", e)
        raise HTTPException(status_code=503, detail="Auth service unavailable") from e
    if r.status_code != 200:
        log.warning("auth verify failed status=%s body=%s", r.status_code, r.text[:200])
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    data = r.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=401, detail="Invalid session")
    # GoTrue returns the user object directly; some clients wrap as { "user": ... }.
    user = data.get("user") if isinstance(data.get("user"), dict) else data
    if not user.get("id"):
        raise HTTPException(status_code=401, detail="Invalid session")
    return user


def normalize_login_name(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch.isalnum())


def admin_register_user(
    *,
    display_name: str,
    email: str,
    password: str,
) -> dict[str, Any]:
    """Create auth user via service role — no confirmation email sent."""
    if not supabase_configured():
        raise HTTPException(status_code=503, detail="Supabase not configured")
    from supabase import create_client

    from todai.database.config import supabase_service_role_key, supabase_url

    client = create_client(supabase_url(), supabase_service_role_key())
    login_key = normalize_login_name(display_name)
    try:
        resp = client.auth.admin.create_user(
            {
                "email": email.strip(),
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "full_name": display_name.strip(),
                    "display_name": display_name.strip(),
                    "login_name": login_key,
                },
            }
        )
    except Exception as e:
        msg = str(e)
        log.warning("admin create_user failed: %s", msg)
        raise HTTPException(status_code=400, detail=msg or "Could not create user") from e
    user = resp.user if hasattr(resp, "user") else (resp.get("user") if isinstance(resp, dict) else None)
    if user is None:
        user = getattr(resp, "model_dump", lambda: {})() if hasattr(resp, "model_dump") else {}
    uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    if not uid:
        raise HTTPException(status_code=500, detail="User created but id missing")
    return {
        "user_id": str(uid),
        "email": email.strip(),
        "login_name": login_key,
        "display_name": display_name.strip(),
    }


def resolve_user_id(
    *,
    authorization: str | None,
    fallback_user_id: str = "default",
) -> str:
    """Return authenticated user id or fallback when local JSON mode."""
    if not auth_required():
        return fallback_user_id
    token = _bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Login required")
    user = verify_supabase_access_token(token)
    return str(user["id"])


def require_user_from_header(
    authorization: str | None = Header(None, alias="Authorization"),
) -> str:
    return resolve_user_id(authorization=authorization)


def require_user_with_fallback(
    fallback_user_id: str,
    authorization: str | None = None,
) -> str:
    return resolve_user_id(authorization=authorization, fallback_user_id=fallback_user_id)
