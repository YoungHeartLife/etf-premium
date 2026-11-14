#!/usr/bin/env python3
"""用户注册 / 登录 / 会话（零依赖：sqlite3 + hashlib + hmac）。

设计要点：
- 密码用 PBKDF2-HMAC-SHA256 + 每用户随机盐；
- 登录态存服务端 sessions 表，浏览器只持有随机会话令牌（HttpOnly Cookie），
  登出、改密都会让旧令牌立即失效；
- 同一邮箱连续输错密码达到阈值后短时锁定，防止暴力尝试；
- 登录失败提示不区分「账号不存在」与「密码错误」，避免账号探测；
- 不发送验证码：注册成功即创建会话，直接处于登录状态。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from config import (APP_HOST, SESSION_COOKIE, SESSION_DAYS, PBKDF2_ITERATIONS,
                    LOGIN_MAX_FAILS, LOGIN_LOCK_MINUTES, CN_TZ)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+$")
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")

NICK_MAX = 20
# 常见弱口令：注册时直接拒绝
WEAK_PASSWORDS = {
    "12345678", "123456789", "1234567890", "password", "password1", "qwerty123",
    "11111111", "88888888", "abc12345", "a1234567", "admin123", "iloveyou",
    "00000000", "66666666", "12341234", "asdfghjk",
}


class AuthError(ValueError):
    """参数或业务校验失败；message 直接返回给前端。"""


# ------------------------------------------------------------------ 密码

def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations, base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expect = base64.b64decode(hash_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expect)


def check_password_strength(password: str) -> None:
    if not isinstance(password, str) or len(password) < 8:
        raise AuthError("密码至少 8 位")
    if len(password) > 128:
        raise AuthError("密码不能超过 128 位")
    if password.lower() in WEAK_PASSWORDS:
        raise AuthError("该密码过于常见，请换一个")
    kinds = sum(bool(re.search(p, password))
                for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if kinds < 2:
        raise AuthError("密码需至少包含字母、数字、符号中的两类")


# ------------------------------------------------------------------ 入参校验

def normalize_email(email) -> str:
    value = str(email or "").strip().lower()
    if not value:
        raise AuthError("请输入邮箱")
    if len(value) > 254 or not EMAIL_RE.match(value):
        raise AuthError("邮箱格式不正确")
    return value


def normalize_nickname(nickname, email: str) -> str:
    value = str(nickname or "").strip()
    if not value:
        return email.split("@")[0][:NICK_MAX] or "用户"
    if len(value) > NICK_MAX:
        raise AuthError(f"昵称不能超过 {NICK_MAX} 个字符")
    if any(ch in value for ch in "\r\n\t<>"):
        raise AuthError("昵称包含不支持的字符")
    return value


def normalize_phone(phone) -> str:
    value = str(phone or "").strip()
    if not value:
        return ""
    if not PHONE_RE.match(value):
        raise AuthError("手机号格式不正确")
    return value


# ------------------------------------------------------------------ 时间与会话

def _now() -> datetime:
    return datetime.now(CN_TZ)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def public_user(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "email": row["email"], "nickname": row["nickname"],
            "phone": row["phone"] or "", "created_at": row["created_at"],
            "last_login_at": row["last_login_at"]}


# ------------------------------------------------------------------ 注册 / 登录

def register(c: sqlite3.Connection, payload: dict, ip: str = "",
             user_agent: str = "") -> dict:
    """注册成功后直接创建会话：不发送验证码，注册即登录。"""
    email = normalize_email(payload.get("email"))
    password = payload.get("password") or ""
    password2 = payload.get("password2")
    nickname = normalize_nickname(payload.get("nickname"), email)
    phone = normalize_phone(payload.get("phone"))

    check_password_strength(password)
    if password2 is not None and password2 != password:
        raise AuthError("两次输入的密码不一致")
    if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise AuthError("该邮箱已注册，请直接登录")

    now = _fmt(_now())
    try:
        cur = c.execute(
            """INSERT INTO users (email, nickname, password_hash, phone, status,
                                  created_at, updated_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?)""",
            (email, nickname, hash_password(password), phone, now, now))
    except sqlite3.IntegrityError:
        # 并发注册同一邮箱时由唯一索引兜底
        raise AuthError("该邮箱已注册，请直接登录") from None

    user_id = cur.lastrowid
    token = create_session(c, user_id, user_agent)
    c.commit()
    row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return {"token": token, "user": public_user(row)}


def login(c: sqlite3.Connection, payload: dict, ip: str = "",
          user_agent: str = "") -> dict:
    email = normalize_email(payload.get("email"))
    password = payload.get("password") or ""
    if not isinstance(password, str) or not password:
        raise AuthError("请输入密码")

    if is_locked(c, email):
        raise AuthError(f"密码错误次数过多，请 {LOGIN_LOCK_MINUTES} 分钟后再试")

    row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    ok = bool(row) and verify_password(password, row["password_hash"])
    # 账号不存在也记录，锁定逻辑对两者一致，无法据此判断账号是否存在
    c.execute("INSERT INTO login_attempts (account, ip, ok, created_at) VALUES (?, ?, ?, ?)",
              (email, ip, 1 if ok else 0, _fmt(_now())))

    if not ok:
        left = LOGIN_MAX_FAILS - recent_fails(c, email)
        c.commit()
        if left > 0:
            raise AuthError(f"邮箱或密码错误，还可尝试 {left} 次")
        raise AuthError(f"密码错误次数过多，请 {LOGIN_LOCK_MINUTES} 分钟后再试")

    if row["status"] != "active":
        c.commit()
        raise AuthError("账号已被停用，请联系客服")

    now = _fmt(_now())
    c.execute("UPDATE users SET last_login_at=?, updated_at=? WHERE id=?", (now, now, row["id"]))
    c.execute("DELETE FROM login_attempts WHERE account=? AND ok=0", (email,))
    token = create_session(c, row["id"], user_agent)
    c.commit()
    row = c.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
    return {"token": token, "user": public_user(row)}


def recent_fails(c: sqlite3.Connection, email: str) -> int:
    since = _fmt(_now() - timedelta(minutes=LOGIN_LOCK_MINUTES))
    return c.execute(
        "SELECT COUNT(*) n FROM login_attempts WHERE account=? AND ok=0 AND created_at>=?",
        (email, since)).fetchone()["n"]


def is_locked(c: sqlite3.Connection, email: str) -> bool:
    return recent_fails(c, email) >= LOGIN_MAX_FAILS


def create_session(c: sqlite3.Connection, user_id: int, user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    c.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen, user_agent)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (_token_hash(token), user_id, _fmt(now), _fmt(now + timedelta(days=SESSION_DAYS)),
         _fmt(now), (user_agent or "")[:200]))
    # 顺手清理过期会话，避免表无限增长
    c.execute("DELETE FROM sessions WHERE expires_at < ?", (_fmt(now),))
    return token


def user_for_token(c: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    row = c.execute(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.token_hash=? AND s.expires_at >= ?""",
        (_token_hash(token), _fmt(_now()))).fetchone()
    if row and row["status"] != "active":
        return None
    return row


def touch_session(c: sqlite3.Connection, token: str | None) -> None:
    """记录最后活跃时间；写入频率按 1 小时收敛，避免每次请求都写库。"""
    if not token:
        return
    h = _token_hash(token)
    row = c.execute("SELECT last_seen FROM sessions WHERE token_hash=?", (h,)).fetchone()
    if not row:
        return
    try:
        last = datetime.strptime(row["last_seen"] or "", "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN_TZ)
    except ValueError:
        last = None
    if not last or (_now() - last).total_seconds() > 3600:
        c.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (_fmt(_now()), h))
        c.commit()


def destroy_session(c: sqlite3.Connection, token: str | None) -> None:
    if token:
        c.execute("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))
        c.commit()


def change_password(c: sqlite3.Connection, user: sqlite3.Row, payload: dict,
                    keep_token: str | None) -> None:
    old = payload.get("old_password") or ""
    new = payload.get("new_password") or ""
    if not verify_password(old, user["password_hash"]):
        raise AuthError("当前密码不正确")
    if old == new:
        raise AuthError("新密码不能与当前密码相同")
    check_password_strength(new)
    now = _fmt(_now())
    c.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
              (hash_password(new), now, user["id"]))
    # 改密后其他设备上的登录态全部失效，仅保留当前浏览器
    if keep_token:
        c.execute("DELETE FROM sessions WHERE user_id=? AND token_hash<>?",
                  (user["id"], _token_hash(keep_token)))
    else:
        c.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    c.commit()


# ------------------------------------------------------------------ Cookie

def _use_secure() -> bool:
    """仅在本机 http 调试时省略 Secure；对外监听地址一律加 Secure。"""
    return APP_HOST not in ("127.0.0.1", "localhost", "0.0.0.0", "::1", "")


def make_cookie(token: str) -> str:
    parts = [f"{SESSION_COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Lax",
             f"Max-Age={SESSION_DAYS * 86400}"]
    if _use_secure():
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie() -> str:
    parts = [f"{SESSION_COOKIE}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if _use_secure():
        parts.append("Secure")
    return "; ".join(parts)


def token_from_cookie(header: str | None) -> str | None:
    if not header:
        return None
    for chunk in header.split(";"):
        name, _, value = chunk.strip().partition("=")
        if name == SESSION_COOKIE and value:
            return value
    return None


def ensure_schema(c: sqlite3.Connection) -> None:
    """补齐用户相关表：旧库只跑过数据表，首次访问登录接口时自动建表。"""
    c.executescript((Path(__file__).resolve().parent / "schema.sql").read_text(encoding="utf-8"))
    c.commit()
