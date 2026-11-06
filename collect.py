#!/usr/bin/env python3
"""
跨境 ETF 溢价监测 · 数据采集器
数据来源：东方财富公开行情与基金档案接口（无需登录、公开可访问）
用法：
    python3 collect.py --full      # 首次全量：发现样本池 + 3 年历史
    python3 collect.py --update    # 日常增量：刷新行情/净值/快照
    python3 collect.py --full --limit 12   # 小样本调试
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import re
import sqlite3
import statistics
import pinyin as py
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from config import (BASE_DIR, DB_PATH, SCHEMA_PATH, HISTORY_START, USER_AGENT,
                    CN_TZ, acquire_lock)

UA = USER_AGENT

# 名称中出现这些词的 ETF 才进入候选池（再按基金类型二次校验）
CROSS_BORDER_KEYWORDS = [
    "纳斯达克", "纳指", "标普", "道琼斯", "恒生", "港股", "香港", "日经", "东证", "TOPIX",
    "德国", "法国", "欧洲", "沙特", "巴西", "东南亚", "亚太", "中概", "海外", "全球",
    "美国", "印度", "越南", "韩国", "日本", "MSCI", "富时", "新兴", "中华", "亚洲",
]
EXCLUDE_KEYWORDS = [
    "黄金", "原油", "豆粕", "有色", "白银", "商品",
]

# 常见跟踪指数的展示名与地区归类
INDEX_META = {
    "NDX100": ("Nasdaq-100", "纳斯达克100"),
    "NDX": ("Nasdaq-100", "纳斯达克100"),
    "SPX": ("S&P 500", "标普500"),
    "SP5CSSUP": ("标普消费", "美国其他"),
    "NDXTMC": ("纳指科技", "美国其他"),
    "SPSIBI": ("标普生物科技", "美国其他"),
    "SPSIOP": ("标普油气", "美国其他"),
    "SPSCS": ("标普消费", "美国其他"),
    "NBI": ("纳指生物科技", "美国其他"),
    "DJIA": ("道琼斯", "美国其他"),
    "750108": ("MSCI 美国50", "美国其他"),
    "HSI": ("恒生指数", "香港"),
    "HSTECH": ("恒生科技", "香港"),
    "HSCEI": ("恒生中国企业", "香港"),
    "H11153": ("恒生中国企业", "香港"),
    "HSIII": ("恒生互联网", "香港"),
    "HSHKBIO": ("恒生生物科技", "香港"),
    "HSHCI": ("恒生医疗保健", "香港"),
    "HSCGSI": ("恒生消费", "香港"),
    "HSIDI": ("恒生创新药", "香港"),
    "HSHYLV": ("恒生红利低波", "香港"),
    "HSCSOE": ("恒生中国央企", "香港"),
    "931574": ("香港科技", "香港"),
    "H30106": ("港股通金融", "香港"),
    "930914": ("港股通高股息", "香港"),
    "N225": ("日经225", "日本"),
    "TOPIX": ("TOPIX", "日本"),
    "TPX": ("TOPIX", "日本"),
    "DAX": ("德国DAX", "欧洲"),
    "GDAXI": ("德国DAX", "欧洲"),
    "FCHI": ("法国CAC40", "欧洲"),
    "CAC40": ("法国CAC40", "欧洲"),
    "FISAULMU": ("沙特", "中东"),
    "IBOV": ("巴西", "巴西"),
    "BVSP": ("巴西", "巴西"),
    "KOSPI": ("韩国", "中韩"),
    "H30533": ("中概互联50", "全球中国"),
    "930604": ("中概互联30", "全球中国"),
    "930796": ("全球中概互联", "全球中国"),
    "ASIATECP": ("东南亚科技", "亚太/东南亚"),
    "IMIT50": ("东南亚科技", "亚太/东南亚"),
    "GPCSP006": ("富时亚太精选", "亚太/东南亚"),
}

INDEX_CODE_ALIASES = {
    "NDX100": ["NDX", "NDX100"],
    "SPX": ["SPX"],
    "DJIA": ["DJIA"],
    "HSI": ["HSI"],
    "HSTECH": ["HSTECH"],
    "HSCEI": ["HSCEI"],
    "N225": ["N225"],
    "TOPIX": ["TOPIX"],
    "DAX": ["GDAXI", "DAX"],
    "GDAXI": ["GDAXI"],
    "CAC40": ["CAC40"],
}


def now_cn() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def today_cn() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d")


def get(url: str, referer: str | None = None, timeout: int = 25, retries: int = 3) -> str | None:
    """通过系统 curl 抓取（使用系统证书链），失败重试。"""
    cmd = ["curl", "-sS", "-L", "-m", str(timeout), "-H", f"User-Agent: {UA}",
           "-H", "Accept: */*"]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    cmd.append(url)
    for attempt in range(retries):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5).stdout
            if out and out.strip():
                return out
        except Exception:
            pass
        time.sleep(0.4 * (attempt + 1))
    return None


def get_json(url: str, referer: str | None = None) -> dict | None:
    raw = get(url, referer=referer)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\})", raw, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None


def db_connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    # 轻量迁移：给旧库补上 suspect 列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(premium_daily)")}
    if "suspect" not in cols:
        conn.execute("ALTER TABLE premium_daily ADD COLUMN suspect INTEGER NOT NULL DEFAULT 0")
    etf_cols = {r[1] for r in conn.execute("PRAGMA table_info(etf)")}
    for col in ("pinyin", "py_initials"):
        if col not in etf_cols:
            conn.execute(f"ALTER TABLE etf ADD COLUMN {col} TEXT")
    # 迁移：快照增加当日均价列；历史 iopv 实际是 f71 均价，纠正语义并清空误标字段
    snap_cols = {r[1] for r in conn.execute("PRAGMA table_info(snapshot)")}
    if "avg_price" not in snap_cols:
        conn.execute("ALTER TABLE snapshot ADD COLUMN avg_price REAL")
    conn.execute("UPDATE snapshot SET avg_price = iopv WHERE avg_price IS NULL AND iopv IS NOT NULL")
    conn.execute("UPDATE snapshot SET iopv = NULL WHERE iopv IS NOT NULL")
    conn.commit()
    return conn


# ---------------------------------------------------------------- 样本池

def fetch_etf_universe() -> dict[str, dict]:
    """抓取沪深全部 ETF 列表，按名称筛出跨境候选。"""
    rows: dict[str, dict] = {}
    base = ("https://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1"
            "&fltt=2&invt=2&fid=f3&fs=b:MK0021,b:MK0022,b:MK0023,b:MK0024"
            "&fields=f12,f13,f14,f2,f3,f6,f18,f15,f16")
    for pn in range(1, 20):
        data = get_json(base.format(pn=pn))
        if not data or not data.get("data") or not data["data"].get("diff"):
            break
        for item in data["data"]["diff"]:
            code = str(item.get("f12") or "")
            name = str(item.get("f14") or "")
            if not code or "ETF" not in name:
                continue
            if any(k in name for k in EXCLUDE_KEYWORDS):
                continue
            if not any(k in name for k in CROSS_BORDER_KEYWORDS):
                continue
            rows[code] = {
                "code": code,
                "name": name,
                "market_no": item.get("f13", 0),
                "market": "SH" if item.get("f13") == 1 else "SZ",
                "price": item.get("f2"),
                "change_pct": item.get("f3"),
                "amount": item.get("f6"),
            }
        total = data["data"].get("total", 0)
        if pn * 100 >= total:
            break
    return rows


def fetch_fund_basic(code: str) -> dict | None:
    url = ("https://fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation"
           f"?FCODE={code}&deviceid=trae&plat=Android&product=EFund&version=6.2.8&_={int(time.time()*1000)}")
    data = get_json(url)
    if not data or not isinstance(data.get("Datas"), dict):
        return None
    return data["Datas"]


def parse_profile_html(code: str) -> dict:
    """解析基金基本概况页：全称、费率、跟踪标的、季报规模。"""
    html = get(f"https://fundf10.eastmoney.com/jbgk_{code}.html")
    out: dict = {}
    if not html:
        return out

    def cell(label: str) -> str | None:
        m = re.search(r"<th>" + re.escape(label) + r"</th><td[^>]*>(.*?)</td>", html, re.S)
        if not m:
            return None
        txt = re.sub(r"<[^>]+>", "", m.group(1))
        return re.sub(r"\s+", " ", txt).strip() or None

    out["full_name"] = cell("基金全称")
    out["benchmark"] = cell("跟踪标的")
    out["company"] = cell("基金管理人")
    out["list_date"] = cell("上市日期")

    fee = cell("管理费率")
    if fee:
        m = re.search(r"([\d.]+)", fee)
        out["fee_mgmt"] = float(m.group(1)) if m else None
    fee = cell("托管费率")
    if fee:
        m = re.search(r"([\d.]+)", fee)
        out["fee_custody"] = float(m.group(1)) if m else None

    m = re.search(r"净资产规模</th>(.*?)</tr>", html, re.S)
    if m:
        seg = re.sub(r"<[^>]+>", " ", m.group(1))
        mm = re.search(r"([\d.]+)\s*亿元", seg)
        if mm:
            out["quarter_scale"] = float(mm.group(1)) * 1e8
        mm = re.search(r"截止至：(\d{4}年\d{2}月\d{2}日)", seg)
        if mm:
            out["quarter_date"] = mm.group(1).replace("年", "-").replace("月", "-").replace("日", "")
    m = re.search(r"份额规模</th>(.*?)</tr>", html, re.S)
    if m:
        seg = re.sub(r"<[^>]+>", " ", m.group(1))
        mm = re.search(r"([\d.]+)\s*亿份", seg)
        if mm:
            out["quarter_shares"] = float(mm.group(1)) * 1e8
    m = re.search(r"成立日期/规模</th>(.*?)</tr>", html, re.S)
    if m:
        seg = re.sub(r"<[^>]+>", " ", m.group(1))
        mm = re.search(r"(\d{4})年(\d{2})月(\d{2})日", seg)
        if mm:
            out["establish_date"] = f"{mm.group(1)}-{mm.group(2)}-{mm.group(3)}"
    return out


def classify(index_code: str | None, index_name: str | None, name: str) -> tuple[str, str]:
    """返回 (指数/主题展示名, 地区分组)。"""
    code = (index_code or "").strip().upper()
    iname = index_name or ""
    if code in INDEX_META:
        label, region = INDEX_META[code]
        return label, region

    if "纳斯达克100" in iname or "纳斯达克 100" in iname:
        return "Nasdaq-100", "纳斯达克100"
    if "标普500" in iname or "标准普尔500" in iname:
        return "S&P 500", "标普500"
    if any(k in iname for k in ("纳斯达克", "纳指", "标普", "道琼斯", "MSCI美国", "罗素")):
        return iname.replace("指数", "").replace("（", "(")[:14], "美国其他"
    if any(k in iname for k in ("恒生", "香港", "港股", "H股")):
        short = (iname.replace("指数", "").replace("人民币", "")
                 .replace("（", "(").replace("）", ")")).strip()
        return short[:14] or "恒生指数", "香港"
    if any(k in iname for k in ("日经", "东京", "TOPIX", "东证")):
        return ("日经225" if "225" in iname else "TOPIX"), "日本"
    if any(k in iname for k in ("韩国", "中韩", "KOSPI")):
        return iname.replace("指数", "")[:14], "中韩"
    if any(k in iname for k in ("DAX", "CAC", "欧洲", "STOXX", "德国", "法国", "英国", "富时100")):
        if "DAX" in iname or "德国" in iname:
            return "德国DAX", "欧洲"
        if "CAC" in iname or "法国" in iname:
            return "法国CAC40", "欧洲"
        return iname.replace("指数", "")[:14], "欧洲"
    if any(k in iname for k in ("沙特", "阿联酋", "中东")):
        return "沙特", "中东"
    if any(k in iname for k in ("巴西", "IBOV")):
        return "巴西", "巴西"
    if any(k in iname for k in ("中国", "中概", "海外中国", "中华")):
        short = (iname.replace("指数", "").replace("人民币", "")
                 .replace("中证", "").replace("（", "(").replace("）", ")")).strip()
        return short[:14] or "全球中国", "全球中国"
    if any(k in iname for k in ("亚太", "东南亚", "印度", "越南", "亚洲", "东盟")):
        short = iname.replace("指数", "").replace("（", "(").replace("）", ")")
        return short[:14], "亚太/东南亚"
    short = iname.replace("指数", "").replace("（", "(").replace("）", ")").strip()
    return (short[:14] or (name.replace("ETF", "")[:10]), "全球其他")


# ---------------------------------------------------------------- 历史序列

def fetch_kline(secid: str, beg: str = HISTORY_START) -> list[tuple]:
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
           f"&klt=101&fqt=0&beg={beg}&end=20500101")
    data = get_json(url)
    if not data or not data.get("data") or not data["data"].get("klines"):
        # 东财主源失败 → 腾讯备用源降级（经实测覆盖 A 股 ETF/沪深指数/港美股）
        return fetch_kline_tx(secid, beg)
    out = []
    for line in data["data"]["klines"]:
        p = line.split(",")
        if len(p) < 7:
            continue
        try:
            # 统一返回 (date, open, high, low, close, volume, amount)，
            # 与 store_fund 的 INSERT 列序 (open, high, low, close) 严格对齐
            out.append((p[0], float(p[1]), float(p[3]), float(p[4]), float(p[2]),
                        float(p[5] or 0), float(p[6] or 0)))
        except ValueError:
            continue
    return out


# ---- 腾讯备用行情源（东财失败时降级）----
# 实测可用：A 股 ETF（sh/sz+代码）、恒生系指数（hkHSI/hkHSTECH/hkHSCEI）、道指（usDJI）；
# 返回格式 [日期, 开, 收, 高, 低, 量]，无成交额 → 备用源下 amount 记 0（premium 不受影响）
TX_INDEX_MAP = {
    "DJIA": "usDJI", "HSI": "hkHSI", "HSTECH": "hkHSTECH", "HSCEI": "hkHSCEI",
}


def _tx_kline(sym: str, beg: str) -> list[list]:
    data = get_json("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                    f"?param={sym},day,,,640,qfq")
    node = ((data or {}).get("data") or {}).get(sym) or {}
    # beg 为 YYYYMMDD（HISTORY_START），腾讯日期是 YYYY-MM-DD，统一去掉连字符再比较
    return [r for r in (node.get("qfqday") or node.get("day") or [])
            if len(r) >= 6 and r[0].replace("-", "") >= beg]


def fetch_kline_tx(secid: str, beg: str) -> list[tuple]:
    try:
        mkt, code = secid.split(".", 1)
        sym = ("sh" if mkt == "1" else "sz") + code
    except ValueError:
        return []
    out = []
    for r in _tx_kline(sym, beg):
        try:
            # 腾讯数组 [date, open, close, high, low, volume] → 同样归一为
            # (date, open, high, low, close, volume, amount)
            out.append((r[0], float(r[1]), float(r[3]), float(r[4]), float(r[2]),
                        float(r[5] or 0), 0.0))
        except (TypeError, ValueError):
            continue
    return out


def fetch_nav(code: str) -> list[tuple]:
    url = ("https://fundmobapi.eastmoney.com/FundMNewApi/FundMNHisNetList"
           f"?FCODE={code}&pageIndex=1&pageSize=1000&deviceid=trae&plat=Android&product=EFund&version=6.2.8&_=1")
    data = get_json(url)
    if not data or not isinstance(data.get("Datas"), list):
        return []
    out = []
    for row in data["Datas"]:
        d = row.get("FSRQ")
        if not d:
            continue
        try:
            nav = float(row.get("DWJZ"))
        except (TypeError, ValueError):
            continue
        try:
            acc = float(row.get("LJJZ")) if row.get("LJJZ") else nav
        except (TypeError, ValueError):
            acc = nav
        try:
            chg = float(row.get("JZZZL")) if row.get("JZZZL") not in (None, "", "--") else None
        except (TypeError, ValueError):
            chg = None
        out.append((d, nav, acc, chg))
    return out


def fetch_snapshot(secid: str) -> dict:
    url = ("https://push2.eastmoney.com/api/qt/stock/get"
           f"?secid={secid}&fltt=2&invt=2"
           "&fields=f43,f44,f45,f46,f47,f48,f57,f58,f60,f71,f84,f85,f169,f170,f171,f292")
    data = get_json(url)
    if not data or not isinstance(data.get("data"), dict):
        return {}
    d = data["data"]

    def num(key):
        v = d.get(key)
        if isinstance(v, (int, float)):
            return float(v)
        return None

    price = num("f43")
    # 注意：f71 是当日均价（成交额/成交量），不是 IOPV；公开行情接口未提供 IOPV
    avg_price = num("f71")
    if avg_price is not None and price and (avg_price < price * 0.5 or avg_price > price * 2):
        avg_price = None
    return {
        "price": price,
        "avg_price": avg_price,
        "shares": num("f84"),
        "volume": num("f47"),
        "amount": num("f48"),
        "prev_close": num("f60"),
        "change_pct": num("f170"),
    }


def fetch_index_series(index_code: str) -> tuple[str, list[tuple]] | None:
    """跟踪指数日行情。

    东方财富不同市场的指数使用不同的 secid 前缀：
      100 = 全球/海外指数（NDX、SPX、HSI、N225 等）
      124 = 港股细分指数（恒生科技、恒生互联网、恒生医疗等）
      2   = 中证/国证系列（931574、930604、H30533 等）
      251 = 纳斯达克行业指数（NDXTMC、NBI 等）
    逐个前缀尝试，命中即返回。
    """
    aliases = INDEX_CODE_ALIASES.get(index_code.upper(), [index_code.upper()])
    for alias in aliases:
        for market in ("100", "124", "2", "251"):
            data = get_json("https://push2his.eastmoney.com/api/qt/stock/kline/get"
                            f"?secid={market}.{alias}&fields1=f1,f2,f3,f4,f5&fields2=f51,f53"
                            f"&klt=101&fqt=0&beg={HISTORY_START}&end=20500101")
            if data and data.get("data") and data["data"].get("klines"):
                pts = []
                for line in data["data"]["klines"]:
                    p = line.split(",")
                    if len(p) >= 2:
                        try:
                            pts.append((p[0], float(p[1])))
                        except ValueError:
                            continue
                if pts:
                    return alias, pts
    # 东财未覆盖 → 腾讯备用源（仅映射表内指数，实测可用）
    tx = TX_INDEX_MAP.get(index_code.upper())
    if tx:
        try:
            pts = [(r[0], float(r[2])) for r in _tx_kline(tx, HISTORY_START)]
            if pts:
                return tx, pts
        except (TypeError, ValueError):
            pass
    return None


# ---------------------------------------------------------------- 入库

def upsert_etf(conn: sqlite3.Connection, rec: dict):
    search_src = " ".join(str(rec.get(k) or "") for k in
                          ("name", "index_label", "index_name", "benchmark"))
    conn.execute(
        """INSERT INTO etf (code, secid, name, full_name, market, company, index_code, index_name,
                           index_label, region, benchmark, list_date, establish_date, fee_mgmt,
                           fee_custody, purchase_status, redeem_status, risk_level, quarter_scale,
                           quarter_shares, quarter_date, pinyin, py_initials, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET
             secid=excluded.secid, name=excluded.name, full_name=excluded.full_name,
             market=excluded.market, company=excluded.company, index_code=excluded.index_code,
             index_name=excluded.index_name, index_label=excluded.index_label, region=excluded.region,
             benchmark=excluded.benchmark, list_date=excluded.list_date,
             establish_date=excluded.establish_date, fee_mgmt=excluded.fee_mgmt,
             fee_custody=excluded.fee_custody, purchase_status=excluded.purchase_status,
             redeem_status=excluded.redeem_status, risk_level=excluded.risk_level,
             quarter_scale=excluded.quarter_scale, quarter_shares=excluded.quarter_shares,
             quarter_date=excluded.quarter_date, pinyin=excluded.pinyin,
             py_initials=excluded.py_initials, updated_at=excluded.updated_at""",
        (rec["code"], rec["secid"], rec["name"], rec.get("full_name"), rec["market"],
         rec.get("company"), rec.get("index_code"), rec.get("index_name"), rec["index_label"],
         rec["region"], rec.get("benchmark"), rec.get("list_date"), rec.get("establish_date"),
         rec.get("fee_mgmt"), rec.get("fee_custody"), rec.get("purchase_status"),
         rec.get("redeem_status"), rec.get("risk_level"), rec.get("quarter_scale"),
         rec.get("quarter_shares"), rec.get("quarter_date"),
         py.full_pinyin(search_src), py.initials(search_src), now_cn()))


def store_fund(conn: sqlite3.Connection, meta_row: dict, kline: list[tuple], nav: list[tuple], snap: dict):
    code = meta_row["code"]
    conn.executemany(
        "INSERT OR REPLACE INTO quote_daily (code, trade_date, open, high, low, close, volume, amount)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(code, *k) for k in kline])
    conn.executemany(
        "INSERT OR REPLACE INTO nav_daily (code, trade_date, unit_nav, accum_nav, nav_change)"
        " VALUES (?,?,?,?,?)",
        [(code, *n) for n in nav])
    if snap and snap.get("price") is not None:
        # 实时参考溢价：最新价 vs 最近已披露官方净值（公开源无 IOPV，不使用均价代替）
        live = None
        if nav:
            live = (snap["price"] - nav[0][1]) / nav[0][1] * 100
        conn.execute(
            """INSERT OR REPLACE INTO snapshot
               (code, price, avg_price, iopv, premium_live, shares, volume, amount, prev_close, change_pct, quote_date, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, snap.get("price"), snap.get("avg_price"), None, live, snap.get("shares"),
             snap.get("volume"), snap.get("amount"), snap.get("prev_close"),
             snap.get("change_pct"), today_cn(), now_cn()))
    conn.commit()


def rebuild_premium(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """INSERT OR REPLACE INTO premium_daily (code, trade_date, close, nav, premium_pct)
           SELECT q.code, q.trade_date, q.close, n.unit_nav,
                  (q.close - n.unit_nav) / n.unit_nav * 100.0
           FROM quote_daily q
           JOIN nav_daily n ON n.code = q.code AND n.trade_date = q.trade_date
           WHERE n.unit_nav > 0 AND q.close > 0""")
    conn.commit()
    return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0


def sanitize_premium(conn: sqlite3.Connection) -> int:
    """清理拆分/份额折算造成的"单日孤立极值"。

    基金发生份额拆分或折算时，官方净值与场内价格各差一个交易日切换，
    会在切换当天产生一个 +90% / -50% 的假溢价。判定条件：
    该日溢价与前后 5 个交易日中位数的差 > 25 个百分点，且邻域中位数 < 12%。
    命中后把 premium_pct 置空并标记 suspect=1，避免污染排序、分位与图表。
    """
    series: dict[str, list[tuple[str, float | None]]] = {}
    for r in conn.execute(
            "SELECT code, trade_date, premium_pct FROM premium_daily ORDER BY code, trade_date"):
        series.setdefault(r["code"], []).append((r["trade_date"], r["premium_pct"]))

    flagged = 0
    for code, rows in series.items():
        vals = [v for _, v in rows]
        for i, (d, v) in enumerate(rows):
            if v is None:
                continue
            nb = [x for x in vals[max(0, i - 5):i] + vals[i + 1:i + 6] if x is not None]
            if len(nb) < 3:
                continue
            med = statistics.median(nb)
            if abs(v - med) > 25 and abs(med) < 12:
                conn.execute(
                    "UPDATE premium_daily SET premium_pct = NULL, suspect = 1 "
                    "WHERE code = ? AND trade_date = ?", (code, d))
                flagged += 1
    conn.commit()
    return flagged


def process_fund(code: str, base: dict, since: str | None = None) -> dict:
    """单个基金：概况 + 日 K + 净值 + 快照。since 用于增量拉取日 K。"""
    res = {"code": code, "ok": False, "error": None}
    try:
        basic = fetch_fund_basic(code)
        if not basic:
            res["error"] = "basic info unavailable"
            return res
        ftype = basic.get("FTYPE") or ""
        if "海外" not in ftype and "QDII" not in ftype.upper():
            res["error"] = f"skip type {ftype}"
            return res

        index_code = basic.get("INDEXCODE")
        index_name = basic.get("INDEXNAME")
        label, region = classify(index_code, index_name, base["name"])
        profile = parse_profile_html(code)

        secid = f"{base['market_no']}.{code}"
        kline = fetch_kline(secid, since or HISTORY_START)
        nav = fetch_nav(code)
        snap = fetch_snapshot(secid)

        meta_row = {
            "code": code,
            "secid": secid,
            "name": basic.get("SHORTNAME") or base["name"],
            "full_name": profile.get("full_name"),
            "market": base["market"],
            "company": profile.get("company") or basic.get("JJGS"),
            "index_code": index_code,
            "index_name": index_name,
            "index_label": label,
            "region": region,
            "benchmark": profile.get("benchmark") or index_name,
            "list_date": profile.get("list_date"),
            "establish_date": profile.get("establish_date") or basic.get("ESTABDATE"),
            "fee_mgmt": profile.get("fee_mgmt"),
            "fee_custody": profile.get("fee_custody"),
            "purchase_status": basic.get("SGZT"),
            "redeem_status": basic.get("SHZT"),
            "risk_level": basic.get("RISKLEVEL"),
            "quarter_scale": profile.get("quarter_scale"),
            "quarter_shares": profile.get("quarter_shares"),
            "quarter_date": profile.get("quarter_date"),
        }
        res.update({"ok": True, "kline": kline, "nav": nav, "snap": snap,
                    "meta_row": meta_row, "index_code": index_code})
        return res
    except Exception as exc:  # noqa: BLE001
        res["error"] = repr(exc)
        return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="全量：重新发现样本池并回补历史")
    ap.add_argument("--update", action="store_true", help="增量：刷新已有样本的最新数据")
    ap.add_argument("--limit", type=int, default=0, help="调试用，限制基金数量")
    args = ap.parse_args()

    lock = acquire_lock()
    if lock is None:
        print("另一个采集任务正在运行，本次跳过。")
        return 3

    mode = "full" if args.full else "update"
    started = now_cn()
    conn = db_connect()
    since_map: dict[str, str] = {}
    if mode == "update":
        for r in conn.execute("SELECT code, MAX(trade_date) d FROM quote_daily GROUP BY code"):
            if r["d"]:
                d = datetime.strptime(r["d"], "%Y-%m-%d") - timedelta(days=7)
                since_map[r["code"]] = d.strftime("%Y%m%d")
    try:
        return run_collect(args, conn, mode, started, since_map)
    finally:
        conn.close()
        lock.close()


def run_collect(args, conn, mode: str, started: str, since_map: dict[str, str]) -> int:
    if mode == "update" and conn.execute("SELECT COUNT(*) c FROM etf").fetchone()["c"] > 0:
        rows = conn.execute("SELECT code, market, index_code FROM etf").fetchall()
        universe = [{"code": r["code"], "name": "", "market": r["market"],
                     "market_no": 1 if r["market"] == "SH" else 0} for r in rows]
    else:
        print("[1/4] 发现跨境 ETF 样本池 ...")
        cand = fetch_etf_universe()
        universe = list(cand.values())
        print(f"      名称初筛 {len(universe)} 只")
    if args.limit:
        universe = universe[:args.limit]

    print(f"[2/4] 抓取 {len(universe)} 只基金的行情 / 净值 / 档案 ...")
    results = []
    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        tasks = {pool.submit(process_fund, item["code"], item, since_map.get(item["code"])): item["code"]
                 for item in universe}
        done = 0
        for fut in futures.as_completed(tasks):
            done += 1
            try:
                res = fut.result()
            except Exception as exc:  # noqa: BLE001
                res = {"code": tasks[fut], "ok": False, "error": repr(exc)}
            results.append(res)
            if done % 10 == 0 or done == len(tasks):
                ok = sum(1 for r in results if r["ok"])
                print(f"      {done}/{len(tasks)} 完成，有效样本 {ok}")
            time.sleep(0.12)

    print("[3/4] 写入数据库 ...")
    ok_count = 0
    for res in results:
        if not res.get("ok"):
            continue
        ok_count += 1
        upsert_etf(conn, res["meta_row"])
        store_fund(conn, res["meta_row"], res["kline"], res["nav"], res["snap"])
    skipped = [(r["code"], r["error"]) for r in results if not r.get("ok")]
    if skipped:
        print(f"      跳过 {len(skipped)} 只：{skipped[:6]}")

    premium_rows = rebuild_premium(conn)
    flagged = sanitize_premium(conn)
    if flagged:
        print(f"      数据校准：剔除 {flagged} 个拆分/折算异常点")

    # 跟踪指数日行情（用于"指数与基金偏离"），仅抓取有映射的指数
    print("[4/4] 抓取跟踪指数行情 ...")
    idx_rows = conn.execute(
        "SELECT DISTINCT index_code FROM etf WHERE index_code IS NOT NULL AND index_code != ''").fetchall()
    index_found = 0
    for row in idx_rows:
        code = row["index_code"]
        got = fetch_index_series(code)
        if not got:
            continue
        alias, pts = got
        conn.executemany(
            "INSERT OR REPLACE INTO index_quote (index_code, trade_date, close) VALUES (?,?,?)",
            [(code, d, c) for d, c in pts])
        conn.commit()
        index_found += 1
        time.sleep(0.2)
    print(f"      指数覆盖 {index_found}/{len(idx_rows)}")

    trade_date = conn.execute("SELECT MAX(trade_date) d FROM premium_daily").fetchone()["d"] or ""
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_collect', ?)", (now_cn(),))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('trade_date', ?)", (trade_date,))
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('universe_note', ?)",
                 ("跨境 ETF · 公开行情与官方净值",))
    conn.execute(
        "INSERT INTO collect_log (started_at, finished_at, mode, funds, nav_rows, quote_rows,"
        " premium_rows, status, note) VALUES (?,?,?,?,?,?,?,?,?)",
        (started, now_cn(), mode, ok_count,
         conn.execute("SELECT COUNT(*) c FROM nav_daily").fetchone()["c"],
         conn.execute("SELECT COUNT(*) c FROM quote_daily").fetchone()["c"],
         premium_rows, "ok", f"skipped={len(skipped)}"))
    conn.commit()
    n_etf = conn.execute("SELECT COUNT(*) c FROM etf").fetchone()["c"]
    print(f"完成：{n_etf} 只 ETF，最新交易日 {trade_date}，溢价记录 {premium_rows} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
