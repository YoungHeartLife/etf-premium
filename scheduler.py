#!/usr/bin/env python3
"""定时增量采集调度器。

用法：
    python3 scheduler.py              # 常驻循环（Docker 的 collector 服务）
    python3 scheduler.py --once       # 只跑一次（供 cron / systemd timer 调用）

配置（环境变量）：
    ETF_COLLECT_AT=18:10              每天固定时刻执行（空值则按间隔）
    ETF_COLLECT_INTERVAL_MINUTES=1440  固定时刻为空时的执行间隔
    ETF_COLLECT_ON_START=1            启动后先跑一次
    ETF_COLLECT_WEEKDAYS_ONLY=1       只在周一到周五执行
    ETF_COLLECT_RETRIES=3             失败重试次数
    ETF_COLLECT_RETRY_BACKOFF=120     重试退避基数（秒，指数增长）
    ETF_LOG_FILE=                     日志文件（留空只写 stdout）
"""
from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from config import (BASE_DIR, CN_TZ, COLLECT_AT, COLLECT_INTERVAL_MINUTES,
                    COLLECT_ON_START, COLLECT_RETRIES, COLLECT_RETRY_BACKOFF,
                    COLLECT_WEEKDAYS_ONLY, LOG_FILE)

log = logging.getLogger("collector")
_stop = False


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if LOG_FILE:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def run_once() -> bool:
    """执行一次增量采集；返回 True 表示成功或已有任务在跑（视为无需重试）。"""
    cmd = [sys.executable, str(BASE_DIR / "collect.py"), "--update"]
    for attempt in range(1, COLLECT_RETRIES + 1):
        log.info("开始增量采集（第 %s/%s 次尝试）", attempt, COLLECT_RETRIES)
        started = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE_DIR))
        for line in (proc.stdout or "").splitlines():
            log.info("collect | %s", line)
        for line in (proc.stderr or "").splitlines():
            log.warning("collect! %s", line)
        cost = time.monotonic() - started
        if proc.returncode == 0:
            log.info("采集完成，用时 %.1f 秒", cost)
            return True
        if proc.returncode == 3:
            log.warning("已有采集任务在运行，本次跳过")
            return True
        log.error("采集失败（exit=%s）", proc.returncode)
        if attempt < COLLECT_RETRIES:
            wait = COLLECT_RETRY_BACKOFF * (2 ** (attempt - 1))
            log.info("等待 %s 秒后重试", wait)
            sleep_with_stop(wait)
    return False


def sleep_with_stop(seconds: float) -> None:
    end = time.monotonic() + seconds
    while not _stop and time.monotonic() < end:
        time.sleep(min(5, max(0.1, end - time.monotonic())))


def next_run_at(now: datetime) -> datetime:
    if COLLECT_AT:
        try:
            hh, mm = (int(x) for x in COLLECT_AT.split(":", 1))
        except ValueError:
            log.error("ETF_COLLECT_AT 格式无效（应为 HH:MM），回退为间隔模式")
        else:
            candidate = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            if COLLECT_WEEKDAYS_ONLY:
                while candidate.weekday() >= 5:
                    candidate += timedelta(days=1)
            return candidate
    return now + timedelta(minutes=max(1, COLLECT_INTERVAL_MINUTES))


def handle_signal(signum, _frame):
    global _stop
    _stop = True
    log.info("收到信号 %s，准备退出", signum)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只执行一次后退出")
    args = ap.parse_args()

    setup_logging()
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    if args.once:
        return 0 if run_once() else 1

    log.info("调度器启动：%s，时区 %s，日志 %s",
             f"每天 {COLLECT_AT}" if COLLECT_AT else f"每 {COLLECT_INTERVAL_MINUTES} 分钟",
             CN_TZ, LOG_FILE or "stdout")

    if COLLECT_ON_START and not _stop:
        run_once()

    while not _stop:
        now = datetime.now(CN_TZ)
        nxt = next_run_at(now)
        log.info("下次采集时间：%s", nxt.strftime("%Y-%m-%d %H:%M %Z"))
        sleep_with_stop(max(1, (nxt - now).total_seconds()))
        if _stop:
            break
        run_once()

    log.info("调度器已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
