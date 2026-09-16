"""
Auth: password hashing (stdlib PBKDF2, no extra dependency) and a
FastAPI dependency for reading the logged-in user out of the session
cookie. Sessions are Starlette's built-in SessionMiddleware (signed,
client-side cookie -- no server-side session table needed for this scale).
"""
import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import Request, HTTPException
from .db import get_conn

_PBKDF2_ITERATIONS = 200_000
_RESET_TOKEN_TTL_MINUTES = 30

# ---------------------------------------------------------------------------
# Login rate limiting: in-memory, per-email sliding window. Same tradeoff as
# the session secret -- resets on server restart, which is fine for a single
# process at this scale and adds no new moving part (no Redis, no DB table
# of timestamps to prune). Keyed by lowercased email, not IP, since the
# threat this stops is credential-stuffing a specific account, and IP-based
# limiting would need to know about proxies/load balancers this project
# doesn't have yet.
# ---------------------------------------------------------------------------
_LOGIN_ATTEMPT_LIMIT = 5
_LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60
_login_attempts: dict[str, list[float]] = {}


def _prune_attempts(email: str) -> list[float]:
    cutoff = time.time() - _LOGIN_ATTEMPT_WINDOW_SECONDS
    attempts = [t for t in _login_attempts.get(email, []) if t > cutoff]
    _login_attempts[email] = attempts
    return attempts


def check_login_rate_limit(email: str) -> None:
    """Raises 429 if this email has failed to log in too many times
    recently. Called before verifying the password, so lockout applies
    whether or not the account even exists (avoids leaking existence via
    timing/behavior differences)."""
    email = email.strip().lower()
    attempts = _prune_attempts(email)
    if len(attempts) >= _LOGIN_ATTEMPT_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed login attempts. Try again in a few minutes.",
        )


def record_failed_login(email: str) -> None:
    email = email.strip().lower()
    _prune_attempts(email)
    _login_attempts.setdefault(email, []).append(time.time())


def clear_login_attempts(email: str) -> None:
    _login_attempts.pop(email.strip().lower(), None)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest_hex = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return hmac.compare_digest(candidate.hex(), digest_hex)


def create_user(email: str, password: str) -> dict:
    email = email.strip().lower()
    with get_conn() as conn:
        existing = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            raise ValueError("An account with this email already exists.")
        cur = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email, hash_password(password)),
        )
        return {"id": cur.lastrowid, "email": email}


def authenticate(email: str, password: str) -> dict | None:
    email = email.strip().lower()
    with get_conn() as conn:
        row = conn.execute("SELECT id, email, password_hash FROM users WHERE email = ?", (email,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        return {"id": row["id"], "email": row["email"]}


def get_user_by_id(user_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT id, email FROM users WHERE id = ?", (user_id,)).fetchone()
        return {"id": row["id"], "email": row["email"]} if row else None


def get_current_user(request: Request) -> dict:
    """FastAPI dependency: raises 401 if there's no valid session, otherwise
    returns {id, email}. Re-checks the user still exists in the DB rather
    than trusting the session payload alone, so a deleted account can't
    keep acting as a valid session."""
    user_id = request.session.get("user_id")
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    user = get_user_by_id(user_id)
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not signed in.")
    return user


def get_current_user_optional(request: Request) -> dict | None:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return get_user_by_id(user_id)


def change_password(user_id: int, current_password: str, new_password: str) -> None:
    with get_conn() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not verify_password(current_password, row["password_hash"]):
            raise ValueError("Current password is incorrect.")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), user_id),
        )


def change_email(user_id: int, new_email: str, current_password: str) -> dict:
    """Changing the address you log in with is a credential change, so it
    re-verifies the password the same way change_password does."""
    new_email = new_email.strip().lower()
    with get_conn() as conn:
        row = conn.execute("SELECT password_hash, email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not verify_password(current_password, row["password_hash"]):
            raise ValueError("Password is incorrect.")
        if new_email == row["email"]:
            raise ValueError("That's already your email address.")
        taken = conn.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?", (new_email, user_id)
        ).fetchone()
        if taken:
            raise ValueError("That email is already in use.")
        conn.execute("UPDATE users SET email = ? WHERE id = ?", (new_email, user_id))
        return {"id": user_id, "email": new_email}


def get_account_overview(user_id: int) -> dict:
    """Profile plus a rollup of what this account actually holds -- shown
    on the account page so "delete everything" isn't an abstract button."""
    with get_conn() as conn:
        user = conn.execute(
            "SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not user:
            raise ValueError("Account not found.")
        totals = conn.execute(
            """SELECT COUNT(*) AS repo_count,
                      COALESCE(SUM(file_count), 0) AS file_count,
                      COALESCE(SUM(symbol_count), 0) AS symbol_count,
                      COALESCE(SUM(dependency_count), 0) AS dependency_count
               FROM repositories WHERE user_id = ?""",
            (user_id,),
        ).fetchone()
        return {**dict(user), **dict(totals)}


def delete_account(user_id: int, current_password: str) -> None:
    """Deletes the user row; repositories (and their files/symbols/
    dependencies, by cascade) go with it via ON DELETE CASCADE. Requires
    the password because it's irreversible."""
    with get_conn() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not verify_password(current_password, row["password_hash"]):
            raise ValueError("Password is incorrect.")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_password_reset_token(email: str) -> str | None:
    """Returns a raw one-time reset token if the email belongs to an
    account, or None if it doesn't. The caller decides what to do with
    None -- the HTTP layer always responds the same way either way, so a
    client can't use this to enumerate which emails have accounts."""
    email = email.strip().lower()
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not row:
            return None
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=_RESET_TOKEN_TTL_MINUTES)).isoformat()
        conn.execute(
            "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
            (row["id"], _hash_token(token), expires_at),
        )
        return token


def reset_password_with_token(token: str, new_password: str) -> None:
    token_hash = _hash_token(token)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, user_id, expires_at, used_at FROM password_reset_tokens "
            "WHERE token_hash = ? ORDER BY id DESC LIMIT 1",
            (token_hash,),
        ).fetchone()
        if not row:
            raise ValueError("This reset link is invalid.")
        if row["used_at"] is not None:
            raise ValueError("This reset link has already been used.")
        expires_at = datetime.fromisoformat(row["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires_at:
            raise ValueError("This reset link has expired. Request a new one.")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), row["user_id"]),
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used_at = datetime('now') WHERE id = ?",
            (row["id"],),
        )


def get_session_secret() -> str:
    """A secret is required for signing session cookies. Reads
    CODENAV_SECRET_KEY if set (needed for sessions to survive a server
    restart); otherwise generates a random one for this process only,
    which is fine for local/dev use but means everyone gets logged out
    whenever the server restarts."""
    env_secret = os.environ.get("CODENAV_SECRET_KEY")
    if env_secret:
        return env_secret
    print("[codenav] CODENAV_SECRET_KEY not set -- using a random session "
          "secret for this run. Sessions will not survive a server restart. "
          "Set CODENAV_SECRET_KEY for a stable secret.")
    return secrets.token_hex(32)
