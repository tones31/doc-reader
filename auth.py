import os
import secrets
import time
import urllib.parse
from typing import Any

import requests
from fastapi import Depends, HTTPException, Request
from jose import jwt
from dotenv import load_dotenv

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
JWT_SECRET = os.getenv("JWT_SECRET")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 days
STATE_EXPIRY_SECONDS = 600  # 10 minutes


def auth_enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and JWT_SECRET)


def encode_state() -> str:
    payload = {
        "rnd": secrets.token_urlsafe(16),
        "exp": int(time.time()) + STATE_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_state(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


# Return the authorization URL and the state
def build_google_auth_url(redirect_uri: str) -> tuple[str, str]:
    state = encode_state()
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
    }
    url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)
    return url, state

# Validate state, exchange code for tokens, fetch userinfo. Return {"sub", "email", "name"} or None.
def exchange_code_for_user(code: str, state: str, redirect_uri: str) -> dict[str, Any] | None:
    if not decode_state(state):
        return None
    data = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    resp = requests.post(GOOGLE_TOKEN_URL, data=data, headers={"Accept": "application/json"}, timeout=10)
    if resp.status_code != 200:
        return None
    tok = resp.json()
    access_token = tok.get("access_token")
    if not access_token:
        return None
    user_resp = requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if user_resp.status_code != 200:
        return None
    info = user_resp.json()
    return {
        "sub": info.get("id"),
        "email": info.get("email") or "",
        "name": info.get("name") or info.get("email") or "User",
    }


def create_session_token(user: dict[str, Any]) -> str:
    payload = {
        "sub": user.get("sub"),
        "email": user.get("email"),
        "name": user.get("name"),
        "exp": int(time.time()) + JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except Exception:
        return None


# FastAPI dependency: require Authorization Bearer JWT and return user payload. Raise 401 if auth disabled or invalid.
def get_current_user(request: Request) -> dict[str, Any]:
    if not auth_enabled():
        raise HTTPException(status_code=501, detail="Auth not configured")
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth[7:].strip()
    user = decode_session_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user

# When auth is enabled: require Bearer JWT and return user or 401. When disabled: return None (no auth required).
def get_current_user_optional(request: Request) -> dict[str, Any] | None:
    if not auth_enabled():
        return None
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth[7:].strip()
    user = decode_session_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user
