"""JWT verification (Firebase + local login) and user id resolution for API routes."""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from fastapi import Header, HTTPException

from todai.api.local_auth import LOCAL_JWT_ISS, verify_local_token
from todai.database.config import (
    auth_dev_allow_default,
    firebase_configured,
    firebase_project_id,
    local_auth_configured,
)

log = logging.getLogger("todai.auth")

_firebase_app = None


def auth_required() -> bool:
    """When True, API routes require Authorization: Bearer <token> (unless dev default)."""
    if auth_dev_allow_default():
        return False
    return firebase_configured() or local_auth_configured()


def public_auth_config() -> dict[str, Any]:
    return {
        "auth_required": auth_required(),
        "auth_dev_allow_default": auth_dev_allow_default(),
        "storage": "supabase",
        "providers": {
            "firebase": firebase_configured(),
            "local": local_auth_configured(),
        },
        "firebase_project_id": firebase_project_id() if firebase_configured() else None,
    }


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def _jwt_payload_unverified(token: str) -> dict[str, Any] | None:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        segment = parts[1]
        pad = segment + "=" * (-len(segment) % 4)
        raw = base64.urlsafe_b64decode(pad.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _token_provider_hint(token: str) -> str:
    payload = _jwt_payload_unverified(token) or {}
    iss = str(payload.get("iss") or "").lower()
    if iss == LOCAL_JWT_ISS.lower() or payload.get("provider") == "local":
        return "local"
    if "securetoken.google.com" in iss:
        return "firebase"
    project = firebase_project_id().lower()
    if project and project in iss:
        return "firebase"
    aud = payload.get("aud")
    if aud == project or (isinstance(aud, list) and project in [str(a).lower() for a in aud]):
        return "firebase"
    return "unknown"


def _init_firebase_app() -> None:
    global _firebase_app
    if _firebase_app is not None:
        return
    if not firebase_configured():
        raise HTTPException(status_code=503, detail="Firebase not configured")
    try:
        import firebase_admin
        from firebase_admin import credentials
    except ImportError as e:
        raise HTTPException(
            status_code=503,
            detail="firebase-admin not installed — pip install firebase-admin",
        ) from e
    from todai.database.config import firebase_service_account_info

    info = firebase_service_account_info()
    if not info:
        raise HTTPException(status_code=503, detail="Firebase service account missing")
    cred = credentials.Certificate(info)
    options = {"projectId": firebase_project_id()}
    try:
        _firebase_app = firebase_admin.initialize_app(cred, options)
    except ValueError:
        _firebase_app = firebase_admin.get_app()


def verify_firebase_id_token(token: str) -> dict[str, Any]:
    _init_firebase_app()
    try:
        from firebase_admin import auth as firebase_auth
    except ImportError as e:
        raise HTTPException(status_code=503, detail="firebase-admin not installed") from e
    try:
        decoded = firebase_auth.verify_id_token(token, check_revoked=False)
    except Exception as e:
        log.warning("firebase verify failed: %s", e)
        raise HTTPException(status_code=401, detail="Invalid or expired Firebase token") from e
    uid = str(decoded.get("uid") or decoded.get("sub") or "")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid Firebase token")
    return {
        "id": uid,
        "email": decoded.get("email"),
        "display_name": decoded.get("name") or "",
        "provider": "firebase",
    }


def verify_access_token(token: str) -> dict[str, Any]:
    """Verify Bearer token from Firebase (Flutter) or local login (web)."""
    hint = _token_provider_hint(token)
    if hint == "local" and local_auth_configured():
        return verify_local_token(token)
    if hint == "firebase" and firebase_configured():
        return verify_firebase_id_token(token)
    errors: list[str] = []
    if local_auth_configured():
        try:
            return verify_local_token(token)
        except HTTPException as e:
            errors.append(f"local:{e.detail}")
    if firebase_configured():
        try:
            return verify_firebase_id_token(token)
        except HTTPException as e:
            errors.append(f"firebase:{e.detail}")
    if errors:
        log.warning("token verify failed hint=%s errors=%s", hint, errors)
    raise HTTPException(status_code=401, detail="Invalid or expired token")


def profile_from_auth_user(user: dict[str, Any]) -> tuple[str | None, str | None]:
    email = user.get("email")
    if email:
        email = str(email).strip() or None
    display_name = (user.get("display_name") or "").strip()
    if not display_name and email:
        display_name = email.split("@")[0]
    return email, display_name or None


def normalize_login_name(name: str) -> str:
    from todai.api.local_auth import normalize_login_name as _norm

    return _norm(name)


def resolve_user_id(
    *,
    authorization: str | None,
    fallback_user_id: str = "default",
) -> str:
    token = _bearer_token(authorization)
    if not token:
        if auth_dev_allow_default() or not auth_required():
            return fallback_user_id
        raise HTTPException(status_code=401, detail="Login required")
    user = verify_access_token(token)
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
