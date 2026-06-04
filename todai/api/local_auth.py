"""Local username/password accounts stored in Supabase (not Supabase Auth)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import HTTPException

from todai.api.service import bootstrap_user_profile
from todai.database.config import local_auth_secret, supabase_configured
from todai.database.repositories.supabase.helpers import get_supabase_client

log = logging.getLogger("todai.local_auth")

LOCAL_JWT_ISS = "todai-local"
LOCAL_JWT_AUD = "todai-api"
TOKEN_TTL_DAYS = 7


def normalize_login_name(name: str) -> str:
    return "".join(ch for ch in name.strip().lower() if ch.isalnum())


def local_auth_enabled() -> bool:
    return bool(local_auth_secret()) and supabase_configured()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def issue_local_token(
    *,
    user_id: str,
    login_name: str,
    display_name: str,
    email: str | None = None,
) -> str:
    secret = local_auth_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="LOCAL_AUTH_SECRET not configured")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iss": LOCAL_JWT_ISS,
        "aud": LOCAL_JWT_AUD,
        "provider": "local",
        "login_name": login_name,
        "display_name": display_name,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=TOKEN_TTL_DAYS)).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_local_token(token: str) -> dict[str, Any]:
    secret = local_auth_secret()
    if not secret:
        raise HTTPException(status_code=503, detail="LOCAL_AUTH_SECRET not configured")
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=LOCAL_JWT_AUD,
            issuer=LOCAL_JWT_ISS,
        )
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from e
    uid = str(payload.get("sub") or "")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid session")
    return {
        "id": uid,
        "email": payload.get("email"),
        "display_name": payload.get("display_name") or payload.get("login_name"),
        "provider": "local",
    }


def _login_row_by_username(client: Any, username: str) -> dict[str, Any] | None:
    key = normalize_login_name(username)
    if key:
        rows = (
            client.table("local_auth_users")
            .select("user_id, login_name, email, password_hash, display_name")
            .eq("login_name", key)
            .limit(1)
            .execute()
        )
        if rows.data:
            return rows.data[0]
    if "@" in username:
        mail = username.strip().lower()
        rows = (
            client.table("local_auth_users")
            .select("user_id, login_name, email, password_hash, display_name")
            .eq("email", mail)
            .limit(1)
            .execute()
        )
        if rows.data:
            return rows.data[0]
    return None


def register_local_user(
    *,
    display_name: str,
    password: str,
    email: str = "",
) -> dict[str, Any]:
    if not local_auth_enabled():
        raise HTTPException(status_code=503, detail="Local auth not configured")
    name = (display_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    pwd = password or ""
    if len(pwd) < 1:
        raise HTTPException(status_code=400, detail="Password is required")
    login_name = normalize_login_name(name)
    if not login_name:
        raise HTTPException(status_code=400, detail="Name must contain letters or numbers")
    mail = (email or "").strip().lower() or None

    client = get_supabase_client()
    existing = (
        client.table("local_auth_users")
        .select("id")
        .eq("login_name", login_name)
        .limit(1)
        .execute()
    )
    if existing.data:
        raise HTTPException(status_code=400, detail="Username already taken")

    user_id = str(uuid.uuid4())
    bootstrap_user_profile(user_id, email=mail, display_name=name)

    try:
        client.table("local_auth_users").insert(
            {
                "user_id": user_id,
                "login_name": login_name,
                "email": mail,
                "password_hash": _hash_password(pwd),
                "display_name": name,
            }
        ).execute()
    except Exception as e:
        log.exception("local_auth insert failed login=%s", login_name)
        raise HTTPException(status_code=500, detail="Could not create account") from e

    token = issue_local_token(
        user_id=user_id,
        login_name=login_name,
        display_name=name,
        email=mail,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_DAYS * 86400,
        "user": {
            "id": user_id,
            "login_name": login_name,
            "display_name": name,
            "email": mail,
        },
    }


def login_local_user(*, username: str, password: str) -> dict[str, Any]:
    if not local_auth_enabled():
        raise HTTPException(status_code=503, detail="Local auth not configured")
    login = (username or "").strip()
    if not login or not password:
        raise HTTPException(status_code=400, detail="Username and password required")

    client = get_supabase_client()
    row = _login_row_by_username(client, login)
    if not row or not _verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    api_user_id = str(row["user_id"])

    token = issue_local_token(
        user_id=api_user_id,
        login_name=row["login_name"],
        display_name=row["display_name"],
        email=row.get("email"),
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_DAYS * 86400,
        "user": {
            "id": api_user_id,
            "login_name": row["login_name"],
            "display_name": row["display_name"],
            "email": row.get("email"),
        },
    }
