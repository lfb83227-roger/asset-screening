"""登录 / 登出。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.config import SESSION_COOKIE, SESSION_MAX_AGE
from app.database import get_db
from app.security import authenticate, client_ip, issue_token, log_operation
from app.webutils import render

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_page(request: Request):
    if request.cookies.get(SESSION_COOKIE):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", active="login")


@router.post("/login")
def do_login(request: Request,
             username: str = Form(...), password: str = Form(...),
             db: Session = Depends(get_db)):
    user = authenticate(db, username.strip(), password)
    if user is None:
        return render(request, "login.html", active="login",
                      err="账号或密码错误，或该账号已被停用")
    token = issue_token(user)
    log_operation(db, user, "login", target=user.username,
                  ip=client_ip(request))
    db.commit()
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE,
                    httponly=True, samesite="lax")
    return resp


@router.get("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp
