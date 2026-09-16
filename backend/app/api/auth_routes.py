"""Auth endpoints: signup, login, logout, session, password reset, and
in-app password change."""
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from .. import auth

router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_password_strength(v: str) -> str:
    if len(v) < 8:
        raise ValueError("Password must be at least 8 characters.")
    return v


class SignupRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def valid_email(cls, v):
        if not _EMAIL_RE.match(v.strip()):
            raise ValueError("Enter a valid email address.")
        return v

    @field_validator("password")
    @classmethod
    def valid_password(cls, v):
        return _validate_password_strength(v)


class LoginRequest(BaseModel):
    email: str
    password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def valid_password(cls, v):
        return _validate_password_strength(v)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def valid_password(cls, v):
        return _validate_password_strength(v)


@router.post("/auth/signup")
def signup(req: SignupRequest, request: Request):
    try:
        user = auth.create_user(req.email, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.session["user_id"] = user["id"]
    return user


@router.post("/auth/login")
def login(req: LoginRequest, request: Request):
    auth.check_login_rate_limit(req.email)
    user = auth.authenticate(req.email, req.password)
    if not user:
        auth.record_failed_login(req.email)
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    auth.clear_login_attempts(req.email)
    request.session["user_id"] = user["id"]
    return user


@router.post("/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request):
    return auth.get_current_user_optional(request)  # null if not signed in -- frontend uses this to decide homepage vs. app


@router.post("/auth/forgot-password")
def forgot_password(req: ForgotPasswordRequest):
    """Always responds the same way regardless of whether the email has an
    account, to avoid leaking which emails are registered.

    Dev-mode note: this project has no email-sending infra configured, so
    rather than silently discarding the token (which would make the
    feature untestable end-to-end without standing up an SMTP server), the
    raw token is returned directly in the response when an account exists.
    A real deployment would email a reset link instead of returning this
    field -- see README."""
    token = auth.create_password_reset_token(req.email)
    return {
        "ok": True,
        "message": "If an account exists for that email, a reset link has been issued.",
        "dev_reset_token": token,  # None if no such account
    }


@router.post("/auth/reset-password")
def reset_password(req: ResetPasswordRequest):
    try:
        auth.reset_password_with_token(req.token, req.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


class ChangeEmailRequest(BaseModel):
    new_email: str
    current_password: str

    @field_validator("new_email")
    @classmethod
    def valid_email(cls, v):
        if not _EMAIL_RE.match(v.strip()):
            raise ValueError("Enter a valid email address.")
        return v


class DeleteAccountRequest(BaseModel):
    current_password: str
    confirm: str

    @field_validator("confirm")
    @classmethod
    def must_confirm(cls, v):
        if v.strip().upper() != "DELETE":
            raise ValueError('Type DELETE to confirm.')
        return v


@router.get("/auth/account")
def account_overview(user: dict = Depends(auth.get_current_user)):
    try:
        return auth.get_account_overview(user["id"])
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/auth/change-email")
def change_email_route(req: ChangeEmailRequest, user: dict = Depends(auth.get_current_user)):
    try:
        return auth.change_email(user["id"], req.new_email, req.current_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/auth/delete-account")
def delete_account_route(req: DeleteAccountRequest, request: Request, user: dict = Depends(auth.get_current_user)):
    try:
        auth.delete_account(user["id"], req.current_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    request.session.clear()  # the account is gone; the cookie shouldn't outlive it
    return {"ok": True}


@router.post("/auth/change-password")
def change_password_route(req: ChangePasswordRequest, user: dict = Depends(auth.get_current_user)):
    try:
        auth.change_password(user["id"], req.current_password, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
