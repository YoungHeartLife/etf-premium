# 溢价日 · 跨境 ETF 溢价监测

一个可直接运行的跨境（QDII）ETF 溢价监测站点：列表筛选 / 排序 / 对比，单只基金的溢价走势、历史位置、区间高低、规模份额、流动性与指数偏离，按日期范围的历史查询与 CSV 导出，以及页面内一键「数据同步」。

- 前端入口：[index.html](index.html)（原生 JS + 手写 SVG，无第三方依赖）
- 后端：[server.py](server.py)（Python 标准库 HTTP 服务 + SQLite）
- 采集器：[collect.py](collect.py) / 调度器：[scheduler.py](scheduler.py)
- 配置：[config.py](config.py)（全部环境变量可覆盖）
- 表结构：[schema.sql](schema.sql)
- 数据库：`data/etf_premium.db`（仓库内已生成，可直接启动）

## 本地开发：一条命令启动

环境要求：Python 3.10+，系统可用 `curl`。

```bash
cd /Users/chenxing/Documents/Trae-CN/temp_20261106_01
python3 run.py            # 缺库时自动全量采集，然后启动
# 或已初始化后：python3 server.py
```

打开 <http://127.0.0.1:8787/>。

常用命令：

```bash
python3 collect.py --full              # 首次全量：发现样本池 + 约 3 年历史 + 指数行情
python3 collect.py --update            # 增量：刷新日 K / 净值 / 快照 / 溢价
python3 scheduler.py --once            # 供 cron 调用的单次增量
python3 scheduler.py                   # 常驻按计划执行（Docker collector 使用）
```

也支持直接双击 `index.html`：静态资源为相对路径，接口自动指向 `http://127.0.0.1:8787`（需先启动服务）。

## 生产部署：Docker Compose（推荐）

组成：`web`（Web/API 服务）+ `collector`（定时增量采集）+ 共享 `./data` 数据卷。

### 1. 初始化

```bash
cp .env.example .env          # 按需修改端口、时区、采集时间
mkdir -p data
sudo chown -R 10001:10001 data    # 容器以 UID 10001 运行，需对挂载目录可写（本机 Docker Desktop 可跳过）
docker compose build
```

### 2. 首次全量采集（约 2–4 分钟，取决于网络）

```bash
docker compose run --rm web python3 collect.py --full
```

### 3. 启动服务

```bash
docker compose up -d
docker compose ps
curl http://127.0.0.1:8787/api/health
```

访问 `http://<服务器IP>:8787/`（端口由 `.env` 的 `ETF_PORT` 控制）。若前面有 Nginx，建议反向代理并只暴露 80/443。

> 若跳过第 2 步直接 `up`，`web` 会通过 `run.py` 自动执行首次全量采集，启动会等待采集完成；日志见 `docker compose logs -f web`。

### 4. 定时采集

`collector` 容器常驻运行，默认每个交易日 18:10（Asia/Shanghai）执行增量更新，启动时先跑一次。修改 `.env`：

```env
ETF_COLLECT_AT=18:10          # 留空则按下方间隔执行
ETF_COLLECT_INTERVAL_MINUTES=1440
ETF_COLLECT_ON_START=1
ETF_COLLECT_RETRIES=3
```

改完执行 `docker compose up -d` 生效。日志：

```bash
docker compose logs -f collector
tail -f data/collect.log
```

采集器具备：失败指数退避重试、`flock` 文件锁互斥（手动同步与定时任务不会并发）、日志落盘与滚动（容器内 `ETF_LOG_FILE`）。

### 5. 更新与备份

```bash
# 拉取新代码后重建
docker compose build && docker compose up -d

# 备份数据库（在线安全备份，无需停机）
python3 -c "import sqlite3;s=sqlite3.connect('data/etf_premium.db');d=sqlite3.connect('data/backup-20261106.db');s.backup(d);d.close();s.close()"

# 或容器内执行
docker compose exec web python3 -c "import sqlite3;s=sqlite3.connect('/data/etf_premium.db');d=sqlite3.connect('/data/backup.db');s.backup(d)"

# 恢复：停服务 -> 覆盖 -> 启动
docker compose down
cp data/backup-20261106.db data/etf_premium.db
docker compose up -d
```

建议把 `data/` 纳入主机定时备份（含 `etf_premium.db`、`collect.log`）。

## 配置项（环境变量）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ETF_HOST` | `127.0.0.1`（容器内 `0.0.0.0`） | 监听地址 |
| `ETF_PORT` | `8787` | 监听端口 |
| `ETF_DB_PATH` | `./data/etf_premium.db`（容器 `/data/...`） | SQLite 路径 |
| `ETF_TZ` | `Asia/Shanghai` | 时区（调度与页面时间） |
| `ETF_COLLECT_AT` | `18:10` | 每日执行时刻，空值改按间隔 |
| `ETF_COLLECT_INTERVAL_MINUTES` | `1440` | 间隔模式下的分钟数 |
| `ETF_COLLECT_ON_START` | `1` | 调度器启动先跑一次 |
| `ETF_COLLECT_WEEKDAYS_ONLY` | `1` | 只在工作日执行 |
| `ETF_COLLECT_RETRIES` | `3` | 失败重试次数 |
| `ETF_COLLECT_RETRY_BACKOFF` | `120` | 重试退避基数（秒） |
| `ETF_SYNC_TIMEOUT` | `1800` | 手动同步超时（秒） |
| `ETF_LOG_FILE` | 空（仅 stdout） | 日志落盘路径 |
| `ETF_HISTORY_START` | 三年前 | 全量历史起始 `YYYYMMDD` |
| `ETF_USER_AGENT` | 浏览器 UA | 采集请求头 |

## 裸机部署（systemd，备选）

不使用 Docker 时，仓库提供 [deploy](deploy) 下的单元文件：

```bash
sudo useradd -r -u 10001 -s /bin/false etf
sudo mkdir -p /opt/etf-premium /etc/etf-premium
sudo cp -r . /opt/etf-premium/
sudo chown -R etf:etf /opt/etf-premium
sudo cp deploy/premium.env.example /etc/etf-premium/env   # 修改路径/端口

sudo -u etf python3 /opt/etf-premium/collect.py --full    # 首次全量

sudo cp deploy/premium-web.service deploy/premium-collect.service deploy/premium-collect.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now premium-web.service
sudo systemctl enable --now premium-collect.timer
systemctl list-timers premium-collect.timer
```

定时采集由 `premium-collect.timer` 触发 `premium-collect.service`（`scheduler.py --once`），时区跟随系统；请确保服务器时区为 `Asia/Shanghai` 或按需调整 `OnCalendar`。

## 数据源与口径

全部来自东方财富公开、免登录接口，采集后只读本地 SQLite：

| 数据 | 接口 |
| --- | --- |
| ETF 样本池 | `push2.eastmoney.com/api/qt/clist/get`（沪深四市场，分页） |
| ETF 日 K | `push2his.eastmoney.com/api/qt/stock/kline/get`（主源）；失败时自动降级腾讯 `web.ifzq.gtimg.cn` 备用源（备用源无成交额，记 0） |
| 基金档案 / 类型 | `fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation` |
| 官方单位净值历史 | `fundmobapi.eastmoney.com/FundMNewApi/FundMNHisNetList` |
| 最新价 / 当日均价 / 份额 | `push2.eastmoney.com/api/qt/stock/get` |
| 基金概况 / 费率 / 季报规模 | `fundf10.eastmoney.com/jbgk_<code>.html` |
| 跟踪指数日行情 | `push2his.eastmoney.com/...&secid=<市场>.<指数代码>`（主源）；东财未覆盖时对映射表内指数（道指、恒生系）降级腾讯备用源 |

公开行情接口不提供交易所 IOPV（`f71` 实际是「成交额 ÷ 成交量」的当日均价），因此本项目不展示 IOPV，也不用均价冒充 IOPV。

指标口径：

- **日度溢价率** =（收盘价 − 当日官方单位净值）÷ 单位净值。QDII 净值通常 T+1 披露，跨境市场与 A 股交易时段错位，溢价/折价放大是其真实特征。
- **参考溢价** =（最新价 − 最近已披露单位净值）÷ 净值，仅作参考；因净值滞后，该值不等同于盘中 IOPV 溢价。
- **历史位置** = 当前溢价在所选区间样本中的百分位排名（0–100）。
- **规模** = 场内份额 × 当日单位净值；季度规模作为交叉校验。
- **指数与基金偏离** = 近 20 个共同交易日基金净值涨幅 − 跟踪指数涨幅（百分点）。

数据质量处理：

- 拆分 / 份额折算会让官方净值与场内价格各差一个交易日切换，产生单日 +90% / −50% 的假溢价。采集器自动识别「单日孤立极值」（与前后 5 日中位数差 > 25pp 且邻域中位数 < 12%），标记 `suspect=1` 并从统计、排序与图表中剔除，详情页会提示校准条数。
- 指数行情按 `100 / 124 / 2 / 251` 多种市场前缀回退，当前覆盖 26/35 个跟踪指数；未覆盖时页面明确显示「指数行情未覆盖」，不做猜测填充。

数据覆盖与缺口（首页「数据覆盖与缺口」卡片，接口 `GET /api/coverage`）：

- 覆盖率全部按库内真实记录统计：日 K 行情、官方净值、跟踪指数、实时快照（均价 / 份额）、日度溢价历史，分别展示「已覆盖 / 总数 / 百分比 / 最新日期」。
- QDII 官方单位净值通常为 T+1 披露，因此最新行情日可能只有收盘价、没有当日官方净值；页面保留最近一个已披露净值日并标注日期，不推测当日净值。卡片会列出仍停留在上一披露日的基金清单。
- 未采集到公开行情的跟踪指数、净值历史起点晚于库内最早交易日的基金，都会在「查看明细」中逐只列出，便于判断缺口来源。

## 页面「数据同步」

- 页面顶部「同步最新数据」触发 `POST /api/sync`，后台线程执行 `collect.py --update`，**不阻塞页面请求**；接口立即返回 `202`。
- 前端每 1.5 秒轮询 `/api/sync/status`，展示同步中动画、上次同步时间、本次新增溢价条数、刷新基金数与失败原因。
- 后端以内存状态 + `meta` 表持久化，刷新页面后仍能看到上次结果；运行中重复点击返回 `409`，并与定时采集共用 `flock` 文件锁，跨进程也不会并发。

## 目录结构

```
.
├── index.html                  # 页面入口（交付物）
├── assets/
│   ├── app.css                # 设计系统、响应式布局
│   ├── app.js                 # 状态、路由、列表/详情/对比/历史查询/同步
│   └── charts.js              # 手写 SVG 图表
├── server.py                  # HTTP 服务 + JSON API + CSV 导出 + 手动同步
├── collect.py                 # 采集、入库、溢价重建、数据校准
├── scheduler.py               # 定时增量调度（重试/互斥/日志）
├── config.py                  # 环境变量配置
├── run.py                     # 本地一键启动
├── schema.sql                 # SQLite 表结构与索引
├── Dockerfile
├── docker-compose.yml
├── deploy/                    # systemd 单元与裸机环境变量示例
├── .env.example
└── data/etf_premium.db        # 本地数据库（WAL）
```

数据表：`etf`、`nav_daily`、`quote_daily`、`premium_daily`、`snapshot`、`index_quote`、`collect_log`、`meta`。

## API

| 接口 | 说明 | 主要参数 |
| --- | --- | --- |
| `GET /api/overview` | 地区 / 主题分布、样本数、平均与最高溢价 | — |
| `GET /api/etfs` | 列表，支持筛选、排序 | `region` `theme` `q` `range` `tab` `sort` `order` |
| `GET /api/etf/<code>` | 单只详情：档案、实时、历史序列、四区间统计、规模、流动性、偏离 | `range` |
| `GET /api/history` | 按日期范围查询历史 | `code` `from` `to` `limit` |
| `GET /api/history.csv` | 同上，返回 CSV（带 BOM） | `code` `from` `to` |
| `GET /api/coverage` | 数据覆盖与缺口：行情 / 净值 / 指数 / 快照 / 溢价历史覆盖率及缺口清单 | — |
| `GET /api/compare` | 2–4 只基金对比（按共同交易日对齐） | `codes` `range` |
| `POST /api/sync` | 触发一次后台增量采集（`202`；运行中 `409`） | — |
| `GET /api/sync/status` | 同步状态、上次同步结果、新增/更新条数 | — |
| `GET /api/health` | 健康检查 | — |

示例：

```bash
curl "http://127.0.0.1:8787/api/history?code=159509&from=2026-01-01&to=2026-11-06"
curl "http://127.0.0.1:8787/api/compare?codes=159509,513100&range=6m"
curl -X POST "http://127.0.0.1:8787/api/sync"
```

## 相对参考截图补齐的功能

参考截图只覆盖「地区 + 主题 + 指标 Tab + 区间 + 列表」和「单只详情」的静态形态，以下能力在实现中补齐，并沿用同一视觉语言：

1. **搜索与全字段排序**：支持代码、简称、指数名模糊搜索；表头排序，空值置底。
2. **详情历史曲线 + 20–80 分位带 + 高低点标注**：判断当前溢价的历史位置。
3. **日期范围历史查询 + CSV 导出**：起止日期、快捷区间与下载。
4. **多基金对比页**：勾选 2–4 只后对比溢价曲线与区间指标。
5. **费率 / 跟踪偏离 / 申赎状态**：用公开档案补齐，帮助判断持有成本与跟踪质量。
6. **加载、空态、错误态、数据校准提示与手动同步**：真实接口会缺失或延迟，页面明确反馈。
7. **桌面端布局**：980px 以上三列详情网格与完整列列表。
8. **数据来源与免责声明**：底部与「数据说明」弹层标注来源、口径与风险提示。

## 合规与免责

数据仅用于研究与信息展示，可能存在延迟、缺失或接口口径变化；不构成投资建议，也不应作为交易依据。请遵守数据源使用条款并控制采集频率（采集器已内置重试与请求间隔）。
