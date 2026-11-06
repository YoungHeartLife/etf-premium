#!/usr/bin/env python3
"""
跨境 ETF 溢价监测 · 后端服务（零依赖，Python 标准库）
启动：python3 server.py [--port 8787]
"""
from __future__ import annotations

import argparse
import bisect
import csv
import io
import json
import mimetypes
import os
import re
import sqlite3
import threading
import time as timemod
from datetime import datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import subprocess
import sys

from config import (APP_HOST, APP_PORT, BASE_DIR as CONFIG_BASE, DB_PATH,
                    CN_TZ, SYNC_TIMEOUT, LOG_FILE)

BASE_DIR = str(CONFIG_BASE)

REGION_ORDER = ["纳斯达克100", "标普500", "美国其他", "香港", "日本", "中韩",
                "欧洲", "中东", "巴西", "全球中国", "亚太/东南亚", "全球其他"]
RANGES = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1095, "all": None}
RANGE_LABEL = {"1m": "近一月", "3m": "近三月", "6m": "近六月", "1y": "近一年",
               "3y": "近三年", "all": "全部"}
TABS = {
    "position": {"label": "历史位置", "metric": "百分位", "sort": "position"},
    "range": {"label": "区间高低", "metric": "区间", "sort": "range_high"},
    "scale": {"label": "规模与份额", "metric": "规模", "sort": "scale"},
    "liquidity": {"label": "流动性", "metric": "成交额", "sort": "amount_avg20"},
    "status": {"label": "申赎状态", "metric": "申赎", "sort": "purchase_status"},
    "deviation": {"label": "指数与基金偏离", "metric": "偏离", "sort": "deviation"},
    "fee": {"label": "基金费率", "metric": "费率", "sort": "fee_total"},
}

DB_LOCK = threading.Lock()

# ---- 手动同步（后台线程执行，不阻塞 HTTP 请求）----
SYNC_LOCK = threading.Lock()
SYNC_STATE = {
    "state": "idle",          # idle | running | success | failed
    "started_at": None,
    "finished_at": None,
    "added": 0,
    "updated": 0,
    "message": "",
    "trigger": "manual",
}
SYNC_THREAD = None


def meta_get(c: sqlite3.Connection, key: str, default: str = "") -> str:
    row = c.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(c: sqlite3.Connection, key: str, value: str) -> None:
    c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def load_last_sync() -> dict:
    try:
        c = conn()
        try:
            return {
                "at": meta_get(c, "last_sync_at", ""),
                "result": meta_get(c, "last_sync_result", ""),
                "note": meta_get(c, "last_sync_note", ""),
                "added": int(meta_get(c, "last_sync_added", "0") or 0),
                "updated": int(meta_get(c, "last_sync_updated", "0") or 0),
            }
        finally:
            c.close()
    except Exception:  # noqa: BLE001
        return {"at": "", "result": "", "note": "", "added": 0, "updated": 0}


def sync_status_payload() -> dict:
    with SYNC_LOCK:
        payload = dict(SYNC_STATE)
    payload["last_sync"] = load_last_sync()
    return payload


def start_sync() -> tuple[bool, dict]:
    """启动后台同步线程；若有任务在跑则拒绝。"""
    global SYNC_THREAD
    with SYNC_LOCK:
        if SYNC_STATE["state"] == "running":
            return False, dict(SYNC_STATE)
        SYNC_STATE.update({
            "state": "running", "started_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None, "added": 0, "updated": 0, "message": "", "trigger": "manual",
        })
    SYNC_THREAD = threading.Thread(target=run_sync_job, daemon=True, name="etf-sync")
    SYNC_THREAD.start()
    return True, sync_status_payload()


def _db_counts() -> tuple[int, int]:
    c = conn()
    try:
        premium = c.execute("SELECT COUNT(*) n FROM premium_daily").fetchone()["n"]
        funds = c.execute("SELECT funds FROM collect_log ORDER BY id DESC LIMIT 1").fetchone()
        return premium, (funds["funds"] if funds else 0)
    finally:
        c.close()


def run_sync_job() -> None:
    global SYNC_STATE
    started = timemod.time()
    try:
        before_premium, _ = _db_counts()
        proc = subprocess.run(
            [sys.executable, str(os.path.join(BASE_DIR, "collect.py")), "--update"],
            capture_output=True, text=True, cwd=BASE_DIR, timeout=SYNC_TIMEOUT)
        after_premium, funds = _db_counts()
        added = max(0, after_premium - before_premium)
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        if proc.returncode == 0:
            message = f"同步完成：新增 {added} 条溢价记录，刷新 {funds} 只基金"
            result, state = "success", "success"
        elif proc.returncode == 3:
            message = "已有其他采集任务在运行，本次未执行，请稍后再试"
            result, state = "busy", "failed"
        else:
            message = f"同步失败（exit={proc.returncode}）：{(tail[-1] if tail else '未知错误')[:180]}"
            result, state = "failed", "failed"
        with SYNC_LOCK:
            SYNC_STATE.update({
                "state": state, "finished_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                "added": added, "updated": funds, "message": message,
            })
        try:
            c = conn()
            try:
                meta_set(c, "last_sync_at", now_cn().strftime("%Y-%m-%d %H:%M:%S"))
                meta_set(c, "last_sync_result", result)
                meta_set(c, "last_sync_note", message)
                meta_set(c, "last_sync_added", str(added))
                meta_set(c, "last_sync_updated", str(funds))
                c.commit()
            finally:
                c.close()
        except Exception:  # noqa: BLE001
            pass
        if LOG_FILE:
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as fh:
                    fh.write(f"{now_cn().strftime('%Y-%m-%d %H:%M:%S')} manual {message} ({timemod.time()-started:.1f}s)\n")
            except OSError:
                pass
    except subprocess.TimeoutExpired:
        with SYNC_LOCK:
            SYNC_STATE.update({
                "state": "failed", "finished_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                "message": f"同步超时（>{SYNC_TIMEOUT}s），请查看服务器日志",
            })
    except Exception as exc:  # noqa: BLE001
        with SYNC_LOCK:
            SYNC_STATE.update({
                "state": "failed", "finished_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
                "message": f"同步异常：{exc}",
            })


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def qs_str(params: dict, key: str, default: str = "") -> str:
    v = params.get(key)
    return v[0].strip() if v and v[0] else default


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def market_status() -> str:
    now = now_cn()
    if now.weekday() >= 5:
        return "已收盘"
    t = now.time()
    if time(9, 15) <= t <= time(11, 31) or time(12, 59) <= t <= time(15, 1):
        return "交易中"
    if t < time(9, 15):
        return "未开盘"
    return "已收盘"


def pct_position(values: list[float], value: float | None) -> float | None:
    if not values or value is None:
        return None
    return round(bisect.bisect_right(values, value) / len(values) * 100, 1)


def cutoff_for(latest: str, rng: str) -> str:
    days = RANGES.get(rng, 30)
    if days is None:
        return "1900-01-01"
    y, m, d = (int(x) for x in latest.split("-"))
    return (datetime(y, m, d) - timedelta(days=days)).strftime("%Y-%m-%d")


def load_meta(c: sqlite3.Connection) -> dict:
    meta = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM meta")}
    n_etf = c.execute("SELECT COUNT(*) v FROM etf").fetchone()["v"]
    n_index = c.execute("SELECT COUNT(DISTINCT index_code) v FROM etf").fetchone()["v"]
    latest = c.execute("SELECT MAX(trade_date) v FROM premium_daily").fetchone()["v"] or ""
    quote_latest = c.execute("SELECT MAX(trade_date) v FROM quote_daily").fetchone()["v"] or ""
    nav_latest = c.execute("SELECT MAX(trade_date) v FROM nav_daily").fetchone()["v"] or ""
    return {
        "last_collect": meta.get("last_collect", ""),
        "trade_date": quote_latest or latest,
        "premium_date": latest,
        "nav_date": nav_latest,
        "etf_count": n_etf,
        "index_count": n_index,
        "market_status": market_status(),
        "server_time": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "东方财富公开行情 / 基金档案（ETF 日 K、官方单位净值、当日均价、场内份额）",
        "methodology": "日度历史溢价 = (收盘价 - 当日官方单位净值) ÷ 单位净值；参考溢价 = (最新价 - 最近已披露单位净值) ÷ 单位净值。公开行情接口未提供交易所 IOPV，页面不使用均价代替。",
    }


def load_all_series(c: sqlite3.Connection, start: str | None = None):
    sql = "SELECT code, trade_date, premium_pct, close, nav FROM premium_daily"
    args = []
    if start:
        sql += " WHERE trade_date >= ?"
        args.append(start)
    sql += " ORDER BY code, trade_date"
    series: dict[str, list[dict]] = {}
    for r in c.execute(sql, args):
        if r["premium_pct"] is None:   # 拆分/折算异常点已剔除，不进入图表与统计
            continue
        series.setdefault(r["code"], []).append({
            "date": r["trade_date"], "premium": r["premium_pct"],
            "close": r["close"], "nav": r["nav"]})
    return series


def load_amounts(c: sqlite3.Connection, start: str):
    out: dict[str, list[tuple]] = {}
    for r in c.execute(
            "SELECT code, trade_date, amount FROM quote_daily WHERE trade_date >= ? ORDER BY code, trade_date",
            (start,)):
        out.setdefault(r["code"], []).append((r["trade_date"], r["amount"] or 0.0))
    return out


def fund_deviation(c: sqlite3.Connection, code: str, index_code: str | None):
    """近 20 个交易日基金净值涨幅 - 跟踪指数涨幅（百分点）。"""
    if not index_code:
        return None
    navs = {r["trade_date"]: r["unit_nav"]
            for r in c.execute("SELECT trade_date, unit_nav FROM nav_daily WHERE code=?", (code,))}
    idx = {r["trade_date"]: r["close"]
           for r in c.execute("SELECT trade_date, close FROM index_quote WHERE index_code=?", (index_code,))}
    common = sorted(set(navs) & set(idx))
    if len(common) < 21:
        return None
    dates = common[-21:]
    fund_ret = (navs[dates[-1]] / navs[dates[0]] - 1) * 100
    idx_ret = (idx[dates[-1]] / idx[dates[0]] - 1) * 100
    return {"value": round(fund_ret - idx_ret, 2), "fund_return": round(fund_ret, 2),
            "index_return": round(idx_ret, 2), "fund_date": dates[-1],
            "index_date": dates[-1], "index_code": index_code, "window": 20}


def build_rows(c: sqlite3.Connection):
    meta = load_meta(c)
    latest = meta["trade_date"]
    funds = {r["code"]: dict(r) for r in c.execute("SELECT * FROM etf")}
    snap = {r["code"]: dict(r) for r in c.execute("SELECT * FROM snapshot")}
    series = load_all_series(c)
    amounts = load_amounts(c, cutoff_for(latest, "3y") if latest else "1900-01-01")
    latest_nav = {}
    for r in c.execute("SELECT code, unit_nav, trade_date FROM nav_daily WHERE trade_date >= ?",
                       (cutoff_for(latest, "1m") if latest else "1900-01-01",)):
        if r["code"] not in latest_nav or r["trade_date"] > latest_nav[r["code"]][1]:
            latest_nav[r["code"]] = (r["unit_nav"], r["trade_date"])

    rows = []
    for code, f in funds.items():
        s = series.get(code, [])
        cur = s[-1] if s else None
        daily_premium = cur["premium"] if cur else None
        sn = snap.get(code) or {}
        live = sn.get("premium_live")
        premium = daily_premium if daily_premium is not None else live
        premium_source = "净值" if daily_premium is not None else "参考（净值滞后）"
        nav, nav_date = latest_nav.get(code, (None, None))
        shares = sn.get("shares")
        scale = (shares * nav / 1e8) if (shares and nav) else (
            (f.get("quarter_scale") / 1e8) if f.get("quarter_scale") else None)
        am = amounts.get(code, [])
        valid = [a for _, a in am if a]
        amount_latest = valid[-1] / 1e8 if valid else None
        amount_avg20 = sum(valid[-20:]) / len(valid[-20:]) / 1e8 if valid else None
        total_fee = None
        if f.get("fee_mgmt") is not None or f.get("fee_custody") is not None:
            total_fee = round((f.get("fee_mgmt") or 0) + (f.get("fee_custody") or 0), 2)
        rows.append({
            "code": code,
            "name": f["name"],
            "full_name": f.get("full_name"),
            "index_code": f.get("index_code"),
            "index_label": f.get("index_label") or f.get("index_name") or "",
            "index_name": f.get("index_name"),
            "benchmark": f.get("benchmark"),
            "pinyin": f.get("pinyin") or "",
            "py_initials": f.get("py_initials") or "",
            "region": f.get("region") or "全球其他",
            "market": f.get("market"),
            "premium": round(premium, 2) if premium is not None else None,
            "premium_date": (cur["date"] if cur else None) if daily_premium is not None else sn.get("quote_date"),
            "premium_source": premium_source,
            "live_premium": round(live, 2) if live is not None else None,
            "daily_premium": round(daily_premium, 2) if daily_premium is not None else None,
            "daily_date": cur["date"] if cur else None,
            "close": sn.get("price") or (cur["close"] if cur else None),
            "nav": nav,
            "nav_date": nav_date,
            "position": None,
            "position_samples": 0,
            "range_low": None,
            "range_low_date": None,
            "range_high": None,
            "range_high_date": None,
            "scale": round(scale, 2) if scale is not None else None,
            "shares_m": round(shares / 1e6, 1) if shares else None,
            "shares_date": sn.get("quote_date"),
            "amount_latest": round(amount_latest, 3) if amount_latest is not None else None,
            "amount_avg20": round(amount_avg20, 3) if amount_avg20 is not None else None,
            "amount_date": am[-1][0] if am else None,
            "purchase_status": f.get("purchase_status") or "—",
            "redeem_status": f.get("redeem_status") or "—",
            "fee_total": total_fee,
            "fee_mgmt": f.get("fee_mgmt"),
            "fee_custody": f.get("fee_custody"),
            "change_pct": sn.get("change_pct"),
            "avg_price": sn.get("avg_price"),
            "iopv": None,
            "_series": s,
            "_index_code": f.get("index_code"),
        })

    for row in rows:
        row["spark"] = [round(p["premium"], 2) for p in row["_series"][-40:]]
        row["deviation"] = fund_deviation(c, row["code"], row["_index_code"])
    return rows


def slice_stats(row: dict, rng: str) -> dict:
    s = row["_series"]
    cutoff = cutoff_for(row["daily_date"] or "2026-01-01", rng)
    sample = [p for p in s if p["date"] > cutoff]
    current = s[-1]["premium"] if s else None
    values = sorted(p["premium"] for p in sample)
    out = {
        "position": pct_position(values, current),
        "samples": len(sample),
        "cutoff": cutoff,
        "low": None, "low_date": None, "high": None, "high_date": None,
    }
    if sample:
        lo = min(sample, key=lambda p: p["premium"])
        hi = max(sample, key=lambda p: p["premium"])
        out.update({"low": round(lo["premium"], 2), "low_date": lo["date"],
                    "high": round(hi["premium"], 2), "high_date": hi["date"]})
    return out


# ------------------------------------------------------------------ API

def api_overview(c: sqlite3.Connection) -> dict:
    meta = load_meta(c)
    rows = build_rows(c)
    for r in rows:
        st = slice_stats(r, "1m")
        r["position"] = st["position"]
        r["position_samples"] = st["samples"]
        r["range_low"], r["range_high"] = st["low"], st["high"]
        r["range_low_date"], r["range_high_date"] = st["low_date"], st["high_date"]

    regions = []
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["region"]] = counts.get(r["region"], 0) + 1
    for reg in REGION_ORDER:
        if counts.get(reg):
            regions.append({"key": reg, "label": reg, "count": counts[reg]})
    for reg, n in counts.items():
        if reg not in REGION_ORDER:
            regions.append({"key": reg, "label": reg, "count": n})

    themes: dict[str, dict] = {}
    for r in rows:
        key = r["index_code"] or r["index_label"]
        if key not in themes:
            themes[key] = {"key": key, "label": r["index_label"], "region": r["region"], "count": 0}
        themes[key]["count"] += 1
    theme_list = sorted(themes.values(), key=lambda x: (-x["count"], x["label"]))

    live = [r["premium"] for r in rows if r["premium"] is not None]
    stats = {
        "avg_premium": round(sum(live) / len(live), 2) if live else None,
        "premium_count": sum(1 for v in live if v > 0),
        "discount_count": sum(1 for v in live if v < 0),
        "max_fund": max(rows, key=lambda r: (r["premium"] if r["premium"] is not None else -999), default=None),
    }
    if stats["max_fund"]:
        mf = stats["max_fund"]
        stats["max_fund"] = {"code": mf["code"], "name": mf["name"], "premium": mf["premium"]}
    return {"meta": meta, "regions": regions, "themes": theme_list, "stats": stats}


def api_list(c: sqlite3.Connection, params: dict) -> dict:
    region = qs_str(params, "region", "all")
    theme = qs_str(params, "theme", "all")
    query = qs_str(params, "q", "").lower()
    rng = qs_str(params, "range", "1m")
    rng = rng if rng in RANGES else "1m"
    tab = qs_str(params, "tab", "position")
    tab = tab if tab in TABS else "position"
    sort = qs_str(params, "sort", "")
    order = qs_str(params, "order", "")
    meta = load_meta(c)
    rows = build_rows(c)

    filtered = []
    for r in rows:
        if region != "all" and r["region"] != region:
            continue
        if theme != "all" and (r["index_code"] or r["index_label"]) != theme:
            continue
        if query:
            q = query.replace(" ", "").lower()
            hay = " ".join(str(r.get(k) or "") for k in
                           ("code", "name", "full_name", "index_label", "index_name",
                            "benchmark", "pinyin", "py_initials")).replace(" ", "").lower()
            if q not in hay:
                continue
        st = slice_stats(r, rng)
        r["position"] = st["position"]
        r["position_samples"] = st["samples"]
        r["range_low"], r["range_high"] = st["low"], st["high"]
        r["range_low_date"], r["range_high_date"] = st["low_date"], st["high_date"]
        r["deviation_value"] = r["deviation"]["value"] if r["deviation"] else None
        filtered.append(r)

    sort_key = sort or TABS[tab]["sort"]
    numeric = {"premium", "position", "range_high", "range_low", "scale", "amount_avg20",
               "amount_latest", "fee_total", "deviation_value"}
    if order not in ("asc", "desc"):
        if sort_key in ("code", "name", "index_label", "purchase_status"):
            order = "asc"
        elif sort_key == "deviation_value":
            order = "desc"
        else:
            order = "desc"

    def key_of(r):
        if sort_key == "range_high":
            return r["range_high"]
        if sort_key == "deviation":
            return r["deviation_value"]
        if sort_key == "deviation_value":
            return abs(r["deviation_value"]) if r["deviation_value"] is not None else None
        return r.get(sort_key)

    present = [r for r in filtered if key_of(r) is not None]
    missing = [r for r in filtered if key_of(r) is None]
    present.sort(key=lambda r: (key_of(r).lower() if isinstance(key_of(r), str) else key_of(r)),
                 reverse=(order == "desc"))
    filtered = present + missing

    out_rows = []
    for r in filtered:
        out_rows.append({k: v for k, v in r.items() if not k.startswith("_")})
    return {
        "meta": meta, "range": rng, "range_label": RANGE_LABEL[rng],
        "tab": tab, "tab_label": TABS[tab]["label"], "metric_label": TABS[tab]["metric"],
        "sort": sort_key, "order": order, "total": len(out_rows),
        "rows": out_rows,
    }


def api_detail(c: sqlite3.Connection, code: str, rng: str) -> dict | None:
    row = c.execute("SELECT * FROM etf WHERE code=?", (code,)).fetchone()
    if not row:
        return None
    f = dict(row)
    sn = c.execute("SELECT * FROM snapshot WHERE code=?", (code,)).fetchone()
    sn = dict(sn) if sn else {}
    s = load_all_series(c, None).get(code, [])
    nav_rows = c.execute(
        "SELECT trade_date, unit_nav, accum_nav, nav_change FROM nav_daily WHERE code=? ORDER BY trade_date",
        (code,)).fetchall()
    amount_rows = c.execute(
        "SELECT trade_date, amount, close, volume FROM quote_daily WHERE code=? ORDER BY trade_date DESC LIMIT 60",
        (code,)).fetchall()
    # 溢价曲线悬浮提示需要每个交易日的成交额（亿元）
    amount_map = {r["trade_date"]: (r["amount"] or 0.0) for r in c.execute(
        "SELECT trade_date, amount FROM quote_daily WHERE code=?", (code,))}
    for p in s:
        p["amount"] = round(amount_map.get(p["date"], 0.0) / 1e8, 3)
    latest_nav = nav_rows[-1] if nav_rows else None
    shares = sn.get("shares")
    scale = (shares * latest_nav["unit_nav"] / 1e8) if (shares and latest_nav) else (
        f.get("quarter_scale") / 1e8 if f.get("quarter_scale") else None)
    row_for_stats = {"_series": s, "daily_date": s[-1]["date"] if s else (latest_nav["trade_date"] if latest_nav else "")}
    stats = {r: slice_stats(row_for_stats, r) for r in RANGES}
    lo = min(s, key=lambda p: p["premium"]) if s else None
    hi = max(s, key=lambda p: p["premium"]) if s else None
    peers = [dict(x) for x in c.execute(
        "SELECT code, name, region FROM etf WHERE index_code=? AND code<>? ORDER BY code",
        (f.get("index_code"), code))]

    total_fee = None
    if f.get("fee_mgmt") is not None or f.get("fee_custody") is not None:
        total_fee = round((f.get("fee_mgmt") or 0) + (f.get("fee_custody") or 0), 2)

    return {
        "meta": load_meta(c),
        "profile": {
            "code": code, "name": f["name"], "full_name": f.get("full_name"),
            "market": f.get("market"), "company": f.get("company"),
            "index_code": f.get("index_code"), "index_name": f.get("index_name"),
            "index_label": f.get("index_label"), "region": f.get("region"),
            "benchmark": f.get("benchmark"), "list_date": f.get("list_date"),
            "establish_date": f.get("establish_date"), "risk_level": f.get("risk_level"),
            "purchase_status": f.get("purchase_status"), "redeem_status": f.get("redeem_status"),
            "fee_mgmt": f.get("fee_mgmt"), "fee_custody": f.get("fee_custody"), "fee_total": total_fee,
            "quarter_scale": f.get("quarter_scale"), "quarter_shares": f.get("quarter_shares"),
            "quarter_date": f.get("quarter_date"),
        },
        "live": {
            "premium": round(sn["premium_live"], 2) if sn.get("premium_live") is not None else None,
            "price": sn.get("price"), "avg_price": sn.get("avg_price"),
            "prev_close": sn.get("prev_close"),
            "change_pct": sn.get("change_pct"), "quote_date": sn.get("quote_date"),
        },
        "daily": {
            "premium": round(s[-1]["premium"], 2) if s else None,
            "date": s[-1]["date"] if s else None,
            "close": s[-1]["close"] if s else None,
            "nav": (latest_nav["unit_nav"] if latest_nav else None),
            "nav_date": latest_nav["trade_date"] if latest_nav else None,
        },
        "stats": stats, "series": s,
        "all_time": {
            "low": round(lo["premium"], 2) if lo else None, "low_date": lo["date"] if lo else None,
            "high": round(hi["premium"], 2) if hi else None, "high_date": hi["date"] if hi else None,
            "count": len(s), "start": s[0]["date"] if s else None,
        },
        "scale": {
            "value": round(scale, 2) if scale is not None else None,
            "shares_m": round(shares / 1e6, 1) if shares else None,
            "shares_date": sn.get("quote_date"), "nav_date": latest_nav["trade_date"] if latest_nav else None,
            "quarter_scale": (round(f["quarter_scale"] / 1e8, 2) if f.get("quarter_scale") else None),
            "quarter_shares_m": (round(f["quarter_shares"] / 1e6, 1) if f.get("quarter_shares") else None),
            "quarter_date": f.get("quarter_date"),
        },
        "liquidity": {
            "latest_amount": round(amount_rows[0]["amount"] / 1e8, 3) if amount_rows else None,
            "latest_date": amount_rows[0]["trade_date"] if amount_rows else None,
            "avg20": round(sum(r["amount"] for r in amount_rows[:20]) / max(len(amount_rows[:20]), 1) / 1e8, 3),
            "series": [{"date": r["trade_date"], "amount": round((r["amount"] or 0) / 1e8, 3),
                        "close": r["close"]} for r in reversed(amount_rows)],
        },
        "deviation": fund_deviation(c, code, f.get("index_code")),
        "data_quality": {
            "suspect_count": c.execute(
                "SELECT COUNT(*) n FROM premium_daily WHERE code=? AND suspect=1",
                (code,)).fetchone()["n"],
            "samples": len(s),
        },
        "peers": peers,
        "range": rng, "range_label": RANGE_LABEL.get(rng, "近一月"),
    }


def api_history(c: sqlite3.Connection, params: dict) -> dict:
    code = qs_str(params, "code", "")
    if not code:
        raise ValueError("code required")
    f = c.execute("SELECT code, name, index_label FROM etf WHERE code=?", (code,)).fetchone()
    if not f:
        raise ValueError("fund not found")
    today = qs_str(params, "to", "") or now_cn().strftime("%Y-%m-%d")
    default_from = (now_cn() - timedelta(days=90)).strftime("%Y-%m-%d")
    start = qs_str(params, "from", "") or default_from
    limit = min(int(qs_str(params, "limit", "600") or 600), 2000)
    rows = c.execute(
        """SELECT p.trade_date, p.close, p.nav, p.premium_pct, p.suspect, q.amount
           FROM premium_daily p LEFT JOIN quote_daily q ON q.code=p.code AND q.trade_date=p.trade_date
           WHERE p.code=? AND p.trade_date BETWEEN ? AND ?
           ORDER BY p.trade_date""", (code, start, today)).fetchall()
    values = sorted(r["premium_pct"] for r in rows if r["premium_pct"] is not None)
    latest = [r for r in rows if r["premium_pct"] is not None]
    current = latest[-1]["premium_pct"] if latest else None
    out = [{
        "date": r["trade_date"], "close": r["close"], "nav": r["nav"],
        "premium": round(r["premium_pct"], 2) if r["premium_pct"] is not None else None,
        "suspect": bool(r["suspect"]),
        "position": pct_position(values, r["premium_pct"]),
        "amount": round((r["amount"] or 0) / 1e8, 3),
    } for r in rows[-limit:]]
    return {
        "code": code, "name": f["name"], "index_label": f["index_label"],
        "from": start, "to": today, "count": len(rows), "rows": out,
        "stats": {
            "current": round(current, 2) if current is not None else None,
            "position": pct_position(values, current),
            "low": round(min(values), 2) if values else None,
            "high": round(max(values), 2) if values else None,
            "avg": round(sum(values) / len(values), 2) if values else None,
            "samples": len(values),
        },
        "methodology": "收盘价与当日官方单位净值计算的日度溢价率；百分位为该记录在所选区间样本中的排名。",
    }


def api_coverage(c: sqlite3.Connection) -> dict:
    """数据覆盖与缺口：只报告库内真实记录，不用推测值填充。"""
    etf_total = c.execute("SELECT COUNT(*) n FROM etf").fetchone()["n"]
    total = etf_total or 1

    quote_max = c.execute("SELECT MAX(trade_date) v FROM quote_daily").fetchone()["v"] or ""
    quote_cov = c.execute("SELECT COUNT(DISTINCT code) n FROM quote_daily WHERE trade_date=?",
                          (quote_max,)).fetchone()["n"] if quote_max else 0

    nav_max = c.execute("SELECT MAX(trade_date) v FROM nav_daily").fetchone()["v"] or ""
    nav_cov = c.execute("SELECT COUNT(DISTINCT code) n FROM nav_daily WHERE trade_date=?",
                        (nav_max,)).fetchone()["n"] if nav_max else 0
    nav_prev = c.execute("SELECT MAX(trade_date) v FROM nav_daily WHERE trade_date<?",
                         (nav_max,)).fetchone()["v"] or ""
    nav_prev_cov = c.execute("SELECT COUNT(DISTINCT code) n FROM nav_daily WHERE trade_date=?",
                             (nav_prev,)).fetchone()["n"] if nav_prev else 0

    # 净值滞后的基金：最新净值日期早于最新已披露净值日
    lagging = []
    if nav_max:
        for r in c.execute(
                """SELECT e.code, e.name, e.region, MAX(n.trade_date) last_nav
                   FROM etf e LEFT JOIN nav_daily n ON n.code = e.code
                   GROUP BY e.code HAVING last_nav < ? OR last_nav IS NULL
                   ORDER BY last_nav DESC, e.code""", (nav_max,)):
            lagging.append({"code": r["code"], "name": r["name"], "region": r["region"],
                            "last_nav": r["last_nav"] or ""})

    # 指数覆盖：以 etf.index_code 去重为总数
    idx_rows = c.execute(
        """SELECT e.index_code, e.index_name, e.index_label, e.region,
                  COUNT(*) funds, MAX(iq.trade_date) last_date, COUNT(iq.trade_date) samples
           FROM etf e LEFT JOIN index_quote iq ON iq.index_code = e.index_code
           GROUP BY e.index_code ORDER BY samples, funds DESC""").fetchall()
    idx_total = len(idx_rows)
    idx_covered = sum(1 for r in idx_rows if (r["samples"] or 0) > 0)
    idx_missing = [{"code": r["index_code"], "name": r["index_name"] or "",
                    "label": r["index_label"] or "", "region": r["region"] or "",
                    "funds": r["funds"]} for r in idx_rows if not (r["samples"] or 0)]

    snap_row = c.execute(
        """SELECT COUNT(*) n,
                  SUM(CASE WHEN avg_price IS NOT NULL AND avg_price > 0 THEN 1 ELSE 0 END) avg_n,
                  SUM(CASE WHEN shares IS NOT NULL AND shares > 0 THEN 1 ELSE 0 END) shares_n,
                  MAX(quote_date) qd FROM snapshot""").fetchone()
    snap_cov = snap_row["n"] or 0
    snap_avg = snap_row["avg_n"] or 0
    snap_shares = snap_row["shares_n"] or 0

    prem_row = c.execute(
        """SELECT COUNT(*) n, MIN(trade_date) a, MAX(trade_date) b,
                  COUNT(DISTINCT code) funds,
                  SUM(CASE WHEN suspect = 1 THEN 1 ELSE 0 END) suspect_n
           FROM premium_daily""").fetchone()

    # 净值历史起点晚于库内最早溢价日的基金（多为上市较晚的新基金）
    base_start = prem_row["a"] or "2000-01-01"
    nav_starts = c.execute(
        """SELECT e.code, e.name, MIN(n.trade_date) first_nav
           FROM etf e JOIN nav_daily n ON n.code = e.code
           GROUP BY e.code HAVING first_nav > ? ORDER BY first_nav DESC""",
        (base_start,)).fetchall()
    late_funds = [{"code": r["code"], "name": r["name"], "first_nav": r["first_nav"],
                   "missing_from": base_start} for r in nav_starts]

    # 最新与次新净值的差额，帮助判断是"尚未披露"而非"采集失败"
    reason = ("QDII 基金官方单位净值通常在交易日收盘后由各基金公司陆续披露（多数为 T+1），"
              "因此最新行情日已有收盘价，但当日官方净值可能尚未发布；页面会保留最近一个已披露净值日，不做推测填充。")

    groups = [
        {"key": "quote", "label": "日 K 行情", "covered": quote_cov, "total": etf_total,
         "date": quote_max, "note": "收盘价 / 成交额"},
        {"key": "nav", "label": "官方单位净值", "covered": nav_cov, "total": etf_total,
         "date": nav_max, "note": f"上一披露日 {nav_prev} 覆盖 {nav_prev_cov} 只"},
        {"key": "index", "label": "跟踪指数行情", "covered": idx_covered, "total": idx_total,
         "date": max([r["last_date"] for r in idx_rows if r["last_date"]] or [""]),
         "note": "按去重后的跟踪指数计算"},
        {"key": "snapshot", "label": "实时快照", "covered": snap_cov, "total": etf_total,
         "date": snap_row["qd"] or "",
         "note": f"均价 {snap_avg} 只 / 份额 {snap_shares} 只；IOPV 公开源未提供"},
        {"key": "premium", "label": "日度溢价历史", "covered": prem_row["funds"] or 0,
         "total": etf_total, "date": prem_row["b"] or "",
         "note": f"{prem_row['a'] or '—'} 起 · {prem_row['n'] or 0} 条 · 剔除异常 {prem_row['suspect_n'] or 0} 条"},
    ]
    for g in groups:
        g["pct"] = round(g["covered"] / g["total"] * 100, 1) if g["total"] else 0

    gaps = []
    if lagging:
        gaps.append(f"{len(lagging)} 只基金最新官方净值仍停留在 {nav_prev}，"
                    f"其余 {nav_cov} 只已更新至 {nav_max}；行情已覆盖 {quote_cov}/{etf_total} 只、最新 {quote_max}。")
    if nav_max and quote_max and nav_max < quote_max:
        gaps.append(f"全部基金官方净值整体晚于行情一天（行情 {quote_max} / 净值 {nav_max}），"
                    "页面日度溢价保留最近已披露净值日，不推测当日净值。")
    gaps.append("公开行情接口未提供交易所 IOPV（接口 f71 实际是当日均价），"
                "参考溢价改为「最新价 ÷ 最近已披露净值」；IOPV 一栏如实标为未提供，不用均价代替。")
    if idx_missing:
        gaps.append(f"{len(idx_missing)} 个跟踪指数暂未采集到公开行情（共 {idx_total} 个），"
                    f"这些基金的「指数偏离」显示为空。")
    if late_funds:
        gaps.append(f"{len(late_funds)} 只基金成立/上市较晚，净值历史起点晚于 {base_start}，"
                    "其历史位置样本相应较少。")
    if not gaps:
        gaps.append("当前未发现数据缺口。")

    return {
        "generated_at": now_cn().strftime("%Y-%m-%d %H:%M:%S"),
        "trade_date": quote_max,
        "etf_total": etf_total,
        "groups": groups,
        "lagging": lagging,
        "lagging_count": len(lagging),
        "index_missing": idx_missing,
        "index_missing_count": len(idx_missing),
        "late_funds": late_funds,
        "late_funds_count": len(late_funds),
        "premium": {"rows": prem_row["n"] or 0, "start": prem_row["a"] or "",
                    "end": prem_row["b"] or "", "suspect": prem_row["suspect_n"] or 0},
        "reason": reason,
        "gaps": gaps,
    }


def api_compare(c: sqlite3.Connection, params: dict) -> dict:
    """多基金对比：2–4 只，按共同交易日对齐后再计算区间指标。

    设计口径：
    - 结束日取所有基金中最早的最后一个交易日，避免“有的基金已更新、有的没更新”；
    - 区间起点按结束日向前推，区间内只保留所有基金都拥有的交易日（交集）；
    - 每只基金的区间低/高、分位、样本数都基于同一批对齐日期，避免自相矛盾。
    """
    codes = list(dict.fromkeys(x.strip() for x in qs_str(params, "codes", "").split(",") if x.strip()))
    if len(codes) < 2:
        raise ValueError("请选择 2–4 只基金进行对比")
    if len(codes) > 4:
        raise ValueError("最多同时对比 4 只基金")
    rng = qs_str(params, "range", "3m")
    rng = rng if rng in RANGES else "3m"
    series_map = load_all_series(c)

    palette = ["#0F6B4F", "#B07A2A", "#33608C", "#A4453B"]
    items, skipped = [], []
    for code in codes:
        f = c.execute("SELECT name, index_label, region FROM etf WHERE code=?", (code,)).fetchone()
        s = series_map.get(code, [])
        if not f or not s:
            skipped.append({"code": code, "reason": "缺少溢价历史"})
            continue
        items.append({"code": code, "name": f["name"], "index_label": f["index_label"],
                      "region": f["region"], "color": palette[len(items) % len(palette)], "series": s})

    if len(items) < 2:
        return {"range": rng, "range_label": RANGE_LABEL[rng], "start": None, "end": None,
                "items": [], "skipped": skipped, "aligned_dates": 0,
                "note": "有效样本不足 2 只，无法进行日期对齐"}

    end = min(it["series"][-1]["date"] for it in items)
    start_limit = min(it["series"][0]["date"] for it in items)
    if rng != "all":
        start_limit = max(start_limit, cutoff_for(end, rng))
    date_sets = [{p["date"] for p in it["series"] if start_limit < p["date"] <= end} for it in items]
    common_dates = sorted(set.intersection(*date_sets)) if date_sets else []

    out_items = []
    for it in items:
        by_date = {p["date"]: p for p in it["series"]}
        pts = [{"date": d, "premium": round(by_date[d]["premium"], 2)} for d in common_dates]
        vals = [p["premium"] for p in pts]
        last = pts[-1] if pts else None
        stats = {
            "latest": last["premium"] if last else None,
            "latest_date": last["date"] if last else None,
            "avg": round(sum(vals) / len(vals), 2) if vals else None,
            "low": min(vals) if vals else None,
            "low_date": min(pts, key=lambda p: p["premium"])["date"] if pts else None,
            "high": max(vals) if vals else None,
            "high_date": max(pts, key=lambda p: p["premium"])["date"] if pts else None,
            "position": pct_position(sorted(vals), last["premium"]) if last else None,
            "samples": len(pts),
        }
        # 参考溢价（最新价 ÷ 最近已披露净值）：不受净值披露节奏限制，
        # 即使日度溢价停在上一披露日，也能看到最新行情对应的溢价水平
        sn = c.execute("SELECT price, premium_live, quote_date FROM snapshot WHERE code=?", (it["code"],)).fetchone()
        live = None
        if sn and sn["premium_live"] is not None:
            live = {"premium": round(sn["premium_live"], 2), "price": sn["price"],
                    "quote_date": sn["quote_date"] or ""}
        out_items.append({"code": it["code"], "name": it["name"], "index_label": it["index_label"],
                          "region": it["region"], "color": it["color"],
                          "points": pts, "stats": stats, "live": live})

    return {"range": rng, "range_label": RANGE_LABEL[rng], "start": common_dates[0] if common_dates else None,
            "end": common_dates[-1] if common_dates else None, "items": out_items, "skipped": skipped,
            "aligned_dates": len(common_dates),
            "note": "区间起点按共同结束日回溯，且只保留所选基金都拥有的交易日"}


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "PremiumMonitor/1.0"

    def log_message(self, fmt, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: str):
        full = os.path.abspath(os.path.join(BASE_DIR, path))
        if not full.startswith(BASE_DIR) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if full.endswith("index.html"):
            # 入口 HTML 绝不缓存，确保每次打开都拿到最新构建引用
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            return self._file("index.html")
        if path.startswith("/assets/"):
            # 支持 /assets/v-<build>/xxx 版本化路径：新版本一定走新 URL，彻底绕开陈旧缓存
            m = re.match(r"^/assets/v-[^/]+/(.+)$", path)
            rel = ("assets/" + m.group(1)) if m else path.lstrip("/")
            return self._file(rel)
        if path == "/favicon.ico":
            return self._json({"ok": False}, 404)

        if not path.startswith("/api/"):
            return self._json({"error": "not found"}, 404)

        try:
            with DB_LOCK:
                c = conn()
                try:
                    if path == "/api/overview":
                        return self._json(api_overview(c))
                    if path == "/api/etfs":
                        return self._json(api_list(c, params))
                    if path.startswith("/api/etf/"):
                        code = path.split("/")[-1]
                        data = api_detail(c, code, qs_str(params, "range", "1m"))
                        if not data:
                            return self._json({"error": "fund not found"}, 404)
                        return self._json(data)
                    if path == "/api/history":
                        return self._json(api_history(c, params))
                    if path == "/api/history.csv":
                        return self._csv(c, params)
                    if path == "/api/coverage":
                        return self._json(api_coverage(c))
                    if path == "/api/compare":
                        return self._json(api_compare(c, params))
                    if path == "/api/sync/status":
                        return self._json(sync_status_payload())
                    if path == "/api/health":
                        return self._json({"ok": True, "time": now_cn().isoformat()})
                    return self._json({"error": "not found"}, 404)
                finally:
                    c.close()
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            return self._json({"error": repr(exc)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/sync":
            ok, payload = start_sync()
            return self._json(payload, 202 if ok else 409)
        return self._json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept")
        self.end_headers()

    def _csv(self, c, params):
        data = api_history(c, params)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["日期", "收盘价", "单位净值", "溢价率(%)", "区间百分位(%)", "成交额(亿元)"])
        for r in data["rows"]:
            w.writerow([r["date"], r["close"], r["nav"], r["premium"], r["position"], r["amount"]])
        body = ("\ufeff" + buf.getvalue()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{data["code"]}_premium_{data["from"]}_{data["to"]}.csv"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=APP_PORT,
                    help=f"监听端口（环境变量 ETF_PORT，默认 {APP_PORT}）")
    ap.add_argument("--host", default=APP_HOST,
                    help=f"监听地址（环境变量 ETF_HOST，默认 {APP_HOST}）")
    args = ap.parse_args()
    if not os.path.exists(DB_PATH):
        raise SystemExit("数据库不存在，请先运行: python3 collect.py --full 或 python3 run.py")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"溢价日 · 服务已启动  http://{args.host}:{args.port}  (DB: {DB_PATH})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
