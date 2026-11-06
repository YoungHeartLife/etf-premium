#!/usr/bin/env python3
"""集中配置：全部通过环境变量覆盖，便于容器与服务器部署。"""
from __future__ import annotations

import os
import fcntl
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()

def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default

# ---- 路径 ----
DB_PATH = os.path.abspath(os.path.expanduser(
    _env("ETF_DB_PATH", str(BASE_DIR / "data" / "etf_premium.db"))))
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
LOCK_PATH = _env("ETF_LOCK_PATH", DB_PATH + ".lock")

# ---- 服务 ----
APP_HOST = _env("ETF_HOST", "127.0.0.1")
APP_PORT = _int("ETF_PORT", 8787)

# ---- 时区与采集 ----
TZ_NAME = _env("ETF_TZ", "Asia/Shanghai")
try:
    from zoneinfo import ZoneInfo
    CN_TZ = ZoneInfo(TZ_NAME)
except Exception:  # pragma: no cover - 兜底
    CN_TZ = timezone(timedelta(hours=8))

COLLECT_AT = _env("ETF_COLLECT_AT", "18:10")          # HH:MM，空值表示按间隔
COLLECT_INTERVAL_MINUTES = _int("ETF_COLLECT_INTERVAL_MINUTES", 1440)
COLLECT_ON_START = _env("ETF_COLLECT_ON_START", "1") not in ("0", "false", "False")
COLLECT_WEEKDAYS_ONLY = _env("ETF_COLLECT_WEEKDAYS_ONLY", "1") not in ("0", "false", "False")
COLLECT_RETRIES = _int("ETF_COLLECT_RETRIES", 3)
COLLECT_RETRY_BACKOFF = _int("ETF_COLLECT_RETRY_BACKOFF", 120)   # 秒，指数退避基数
SYNC_TIMEOUT = _int("ETF_SYNC_TIMEOUT", 1800)

# ---- 日志 ----
LOG_FILE = _env("ETF_LOG_FILE", "")     # 留空则只输出到 stdout

# ---- 采集口径 ----
HISTORY_START = _env("ETF_HISTORY_START", (date.today() - timedelta(days=1095)).strftime("%Y%m%d"))
USER_AGENT = _env(
    "ETF_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def now_cn() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_cn() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


def acquire_lock(path: str | None = None):
    """非阻塞独占锁；已被占用时返回 None，用于防止采集任务并发执行。"""
    lock_path = path or LOCK_PATH
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def release_lock(fh) -> None:
    if not fh:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()
