-- 跨境 ETF 溢价监测 · 数据库结构
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS etf (
    code            TEXT PRIMARY KEY,
    secid           TEXT,              -- 东方财富行情 secid，如 0.159502
    name            TEXT NOT NULL,     -- 基金简称
    full_name       TEXT,              -- 基金全称
    market          TEXT,              -- SH / SZ
    company         TEXT,
    index_code      TEXT,
    index_name      TEXT,
    index_label     TEXT,              -- 展示用指数/主题短名
    region          TEXT,              -- 纳斯达克100 / 标普500 / 美国其他 / 香港 ...
    benchmark       TEXT,              -- 跟踪标的
    list_date       TEXT,
    establish_date  TEXT,
    fee_mgmt        REAL,
    fee_custody     REAL,
    purchase_status TEXT,
    redeem_status   TEXT,
    risk_level      TEXT,
    quarter_scale   REAL,              -- 季报净资产规模（元）
    quarter_shares  REAL,              -- 季报份额（份）
    quarter_date    TEXT,
    pinyin          TEXT,              -- 名称/指数全拼，用于搜索
    py_initials     TEXT,              -- 拼音首字母
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS nav_daily (
    code       TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    unit_nav   REAL,
    accum_nav  REAL,
    nav_change REAL,
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_nav_date ON nav_daily (trade_date);

CREATE TABLE IF NOT EXISTS quote_daily (
    code       TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    volume     REAL,     -- 手
    amount     REAL,     -- 元
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_quote_date ON quote_daily (trade_date);

-- 日度溢价率 = (收盘价 - 当日官方单位净值) / 单位净值
CREATE TABLE IF NOT EXISTS premium_daily (
    code         TEXT NOT NULL,
    trade_date   TEXT NOT NULL,
    close        REAL,
    nav          REAL,
    premium_pct  REAL,
    suspect      INTEGER NOT NULL DEFAULT 0,   -- 1 = 疑似拆分/折算导致的价格净值错位，已剔除
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_premium_date ON premium_daily (trade_date);

-- 最新快照：最新价 / 当日均价 / 场内份额（公开接口未提供 IOPV）
CREATE TABLE IF NOT EXISTS snapshot (
    code         TEXT PRIMARY KEY,
    price        REAL,
    avg_price    REAL,
    iopv         REAL,
    premium_live REAL,
    shares       REAL,
    volume       REAL,
    amount       REAL,
    prev_close   REAL,
    change_pct   REAL,
    quote_date   TEXT,
    updated_at   TEXT
);

-- 跟踪指数日行情（用于"指数与基金偏离"）
CREATE TABLE IF NOT EXISTS index_quote (
    index_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    close      REAL,
    PRIMARY KEY (index_code, trade_date)
);

CREATE TABLE IF NOT EXISTS collect_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT,
    finished_at   TEXT,
    mode          TEXT,
    funds         INTEGER,
    nav_rows      INTEGER,
    quote_rows    INTEGER,
    premium_rows  INTEGER,
    status        TEXT,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- ------------------------------------------------------------------ 用户

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,          -- 统一小写存储，用于登录与唯一性
    nickname      TEXT NOT NULL,
    password_hash TEXT NOT NULL,                 -- pbkdf2_sha256$<iterations>$<salt>$<hash>
    phone         TEXT,
    status        TEXT NOT NULL DEFAULT 'active',-- active / disabled
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    last_login_at TEXT
);

-- 登录态存服务端，浏览器只持有随机会话令牌；库里只存令牌的 SHA-256
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen  TEXT,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires_at);

-- 登录失败记录：用于同一账号的短时锁定
CREATE TABLE IF NOT EXISTS login_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account    TEXT NOT NULL,
    ip         TEXT,
    ok         INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_account ON login_attempts (account, created_at);
