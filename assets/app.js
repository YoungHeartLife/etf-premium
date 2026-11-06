/* 溢价日 · 前端应用 */
(function () {
  "use strict";

  const TABS = [
    { k: "position", label: "历史位置", metric: "溢价率" },
    { k: "range", label: "区间高低", metric: "区间高位" },
    { k: "scale", label: "规模份额", metric: "基金规模" },
    { k: "liquidity", label: "流动性", metric: "成交额" },
    { k: "status", label: "申赎", metric: "申赎" },
    { k: "deviation", label: "指数偏离", metric: "偏离" },
    { k: "fee", label: "费率", metric: "综合费率" },
  ];
  const RANGES = [
    { k: "1m", label: "近一月" },
    { k: "3m", label: "近三月" },
    { k: "6m", label: "近六月" },
    { k: "1y", label: "近一年" },
    { k: "3y", label: "近三年" },
    { k: "all", label: "全部" },
  ];
  const DAYS = { "1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1095 };
  const MAX_SEL = 4;

  const S = {
    overview: null, meta: null,
    region: "all", theme: "all", q: "", range: "1m", tab: "position",
    sort: "", order: "", sel: new Set(), themeExpanded: false,
    detailRange: "1m", detail: null, compareRange: "3m", compare: null,
    route: "home", loadingCount: 0, coverage: null, covExpanded: false, lastSyncAt: null,
  };

  const $ = (s, root) => (root || document).querySelector(s);
  const $$ = (s, root) => Array.from((root || document).querySelectorAll(s));

  /* ------------------------------------------------------------ 工具 */

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }
  function fmtPct(v, d) {
    if (v === null || v === undefined || Number.isNaN(v)) return "—";
    return (v > 0 ? "+" : "") + Number(v).toFixed(d === undefined ? 2 : d) + "%";
  }
  function cls(v) {
    if (v === null || v === undefined) return "flat";
    return v > 0 ? "up" : (v < 0 ? "down" : "flat");
  }
  function fmtYi(v, d) {
    if (v === null || v === undefined) return "—";
    return Number(v).toFixed(d === undefined ? 2 : d) + "亿";
  }
  function fmtNum(v, d) {
    if (v === null || v === undefined) return "—";
    return Number(v).toFixed(d === undefined ? 2 : d);
  }
  function mmdd(d) { return d ? d.slice(5).replace("-", "-") : "—"; }

  // 直接双击打开 index.html 时，静态资源相对加载，接口指向本机服务
  const API_BASE = location.protocol === "file:" ? "http://127.0.0.1:8787" : "";

  async function api(path) {
    let res;
    try {
      res = await fetch(API_BASE + path, { headers: { Accept: "application/json" } });
    } catch (e) {
      throw new Error("无法连接本地服务，请先运行 python3 server.py --port 8787");
    }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) throw new Error((data && data.error) || ("HTTP " + res.status));
    return data;
  }

  let loadingTimer = null;
  function loading(on) {
    S.loadingCount += on ? 1 : -1;
    if (S.loadingCount < 0) S.loadingCount = 0;
    const line = $("#loadingLine");
    clearTimeout(loadingTimer);
    if (S.loadingCount > 0) line.classList.add("on");
    else loadingTimer = setTimeout(() => line.classList.remove("on"), 220);
  }

  let toastTimer = null;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 2200);
  }

  function showModal(title, html, cls) {
    $("#modalTitle").textContent = title;
    $("#modalBody").innerHTML = html;
    const card = $(".modal-card");
    card.classList.toggle("wide", cls === "wide");
    $("#modal").hidden = false;
  }
  function closeModal() { $("#modal").hidden = true; }

  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => toast("已复制：" + text)).catch(() => toast(text));
    } else {
      toast(text);
    }
  }

  function setFreshness(meta) {
    if (!meta) return;
    S.meta = meta;
    // 顶栏同步时间：取「手动/页面同步」与「采集器定时同步」中较新的一次
    const latest = [S.lastSyncAt, meta.last_collect].filter(Boolean).sort().pop();
    $("#freshnessText").textContent = "同步于 " + (latest ? latest.slice(5, 16) : "—");
    $("#freshness").title = "最近同步 " + (latest || "—") + (S.lastSyncAt ? "（含手动同步）" : "");
    $("#marketStatus").textContent = meta.market_status || "已收盘";
    $("#marketStatus").classList.toggle("live", meta.market_status === "交易中");
    $("#tradeDate").textContent = meta.trade_date
      ? (meta.nav_date && meta.nav_date !== meta.trade_date
        ? `行情 ${meta.trade_date} · 净值 ${meta.nav_date}`
        : `数据日 ${meta.trade_date}`)
      : "";
    $("#footUpdated").textContent = "最近同步 " + (latest || "—");
  }

  /* ------------------------------------------------------------ 首页 */

  async function ensureOverview() {
    if (S.overview) return S.overview;
    loading(true);
    try {
      const data = await api("/api/overview");
      S.overview = data;
      setFreshness(data.meta);
      return data;
    } finally { loading(false); }
  }

  function renderHero(overview) {
    const m = overview.meta, st = overview.stats;
    $("#heroSub").textContent = `${overview.regions.reduce((a, b) => a + b.count, 0)} 只跨境 ETF · ${overview.themes.length} 个指数 / 主题 · ${overview.regions.length} 个地区`;
    const stats = [
      { label: "平均溢价", value: fmtPct(st.avg_premium), tone: cls(st.avg_premium), note: "全样本最新交易日" },
      { label: "溢价基金", value: `${st.premium_count} / ${m.etf_count}`, tone: "flat", note: "溢价率大于 0" },
      { label: "最高溢价", value: st.max_fund ? fmtPct(st.max_fund.premium) : "—", tone: "up", note: st.max_fund ? `${st.max_fund.code} ${st.max_fund.name}` : "" },
    ];
    $("#heroStats").innerHTML = stats.map(s => `
      <div class="hero-stat">
        <span>${s.label}</span>
        <b class="${s.tone}">${esc(s.value)}</b>
        <small>${esc(s.note)}</small>
      </div>`).join("");
  }

  function renderRegions(overview) {
    const total = overview.meta.etf_count;
    const items = [{ key: "all", label: "全部指数", count: total }].concat(overview.regions);
    $("#regionPills").innerHTML = items.map(r => `
      <button class="pill ${S.region === r.key ? "active" : ""}" data-region="${esc(r.key)}">
        ${esc(r.label)}<span style="opacity:.55;font-size:11.5px">${r.count}</span>
      </button>`).join("");
    $$("#regionPills .pill").forEach(btn => btn.addEventListener("click", () => {
      S.region = btn.dataset.region;
      S.themeExpanded = false;
      if (S.theme !== "all") {
        const theme = overview.themes.find(t => t.key === S.theme);
        if (theme && S.region !== "all" && theme.region !== S.region) S.theme = "all";
      }
      renderRegions(overview);
      renderThemes(overview);
      loadList();
    }));
  }

  function renderThemes(overview) {
    const themes = overview.themes.filter(t => S.region === "all" || t.region === S.region);
    $("#themeNote").textContent = themes.length + " 个指数 / 主题";
    const allCount = themes.reduce((a, b) => a + b.count, 0);
    let visible = themes;
    if (!S.themeExpanded && themes.length > 12) {
      visible = themes.slice(0, 12);
      const selected = themes.find(t => t.key === S.theme);
      if (selected && !visible.includes(selected)) visible = visible.concat([selected]);
    }
    const items = [{ key: "all", label: "全部", count: allCount }].concat(visible);
    $("#themeChips").innerHTML = items.map(t => `
      <button class="chip ${S.theme === t.key ? "active" : ""}" data-theme="${esc(t.key)}">
        ${esc(t.label)}<em>${t.count}只</em>
      </button>`).join("") + (themes.length > 12
        ? `<button class="chip chip-more" id="themeToggle">${S.themeExpanded ? "收起" : `展开全部 ${themes.length}`}</button>` : "");
    $$("#themeChips [data-theme]").forEach(btn => btn.addEventListener("click", () => {
      S.theme = btn.dataset.theme;
      renderThemes(overview);
      loadList();
    }));
    const toggle = $("#themeToggle");
    if (toggle) toggle.addEventListener("click", () => {
      S.themeExpanded = !S.themeExpanded;
      renderThemes(overview);
    });
  }

  function renderTabs() {
    $("#tabs").innerHTML = TABS.map(t => `
      <button class="tab ${S.tab === t.k ? "active" : ""}" role="tab" aria-selected="${S.tab === t.k}" data-tab="${t.k}">${t.label}</button>`).join("");
    $$("#tabs .tab").forEach(btn => btn.addEventListener("click", () => {
      S.tab = btn.dataset.tab;
      S.sort = "";
      S.order = "";
      const tab = TABS.find(t => t.k === S.tab);
      $("#metricHead").textContent = tab.metric;
      $("#metricHead").dataset.sort = tab.k === "position" ? "premium" : {
        range: "range_high", scale: "scale", liquidity: "amount_avg20",
        status: "purchase_status", deviation: "deviation_value", fee: "fee_total",
      }[tab.k];
      renderTabs();
      loadList();
    }));
  }

  function renderRanges() {
    $("#rangePills").innerHTML = RANGES.map(r => `
      <button class="range-pill ${S.range === r.k ? "active" : ""}" data-range="${r.k}">${r.label}</button>`).join("");
    $$("#rangePills .range-pill").forEach(btn => btn.addEventListener("click", () => {
      S.range = btn.dataset.range;
      renderRanges();
      loadList();
    }));
  }

  function metricCell(r) {
    const tab = S.tab;
    const p = r.premium;
    const prem = `<span class="${cls(p)}">${fmtPct(p)}</span>`;
    if (tab === "position") {
      const pos = r.position;
      return {
        main: `<span class="metric-main ${cls(p)}">${fmtPct(p)}</span>`,
        sub: pos == null ? `<span>样本不足</span>` :
          `<span class="mini-track"><span class="mini-fill" style="width:${pos}%"></span></span><span>分位 ${fmtNum(pos, 0)}</span>`,
      };
    }
    if (tab === "range") {
      return {
        main: `<span class="metric-main ${cls(r.range_high)}">${fmtPct(r.range_high)}</span>`,
        sub: `<span>低位 ${fmtPct(r.range_low)}</span>`,
      };
    }
    if (tab === "scale") {
      return {
        main: `<span class="metric-main flat">${fmtYi(r.scale)}</span>`,
        sub: `<span>${r.shares_m ? fmtNum(r.shares_m, 1) + "百万份" : "份额 —"}</span>`,
      };
    }
    if (tab === "liquidity") {
      return {
        main: `<span class="metric-main flat">${fmtYi(r.amount_latest)}</span>`,
        sub: `<span>20日均 ${fmtYi(r.amount_avg20)}</span>`,
      };
    }
    if (tab === "status") {
      const st = r.purchase_status || "—";
      const isPause = /暂停|closed/i.test(st);
      return {
        main: `<span class="metric-main ${isPause ? "down" : "flat"}" style="font-size:13px">${esc(st)}</span>`,
        sub: `<span class="${cls(p)}">${fmtPct(p)}</span>`,
      };
    }
    if (tab === "deviation") {
      const d = r.deviation;
      return {
        main: d ? `<span class="metric-main ${cls(d.value)}">${d.value > 0 ? "+" : ""}${fmtNum(d.value, 2)}pp</span>`
          : `<span class="metric-main flat" style="font-size:12.5px">指数未覆盖</span>`,
        sub: d ? `<span>净值 ${fmtPct(d.fund_return)} / 指数 ${fmtPct(d.index_return)}</span>` : `<span>溢价 ${fmtPct(p)}</span>`,
      };
    }
    if (tab === "fee") {
      return {
        main: `<span class="metric-main flat">${r.fee_total == null ? "—" : fmtNum(r.fee_total, 2) + "%"}</span>`,
        sub: `<span>管理 ${fmtNum(r.fee_mgmt, 2)} / 托管 ${fmtNum(r.fee_custody, 2)}</span>`,
      };
    }
    return { main: prem, sub: "" };
  }

  function rowHtml(r) {
    const cell = metricCell(r);
    const checked = S.sel.has(r.code);
    const pos = r.position;
    return `<tr data-code="${esc(r.code)}" class="${checked ? "selected" : ""}">
      <td class="col-check"><input type="checkbox" class="etable-check" ${checked ? "checked" : ""} aria-label="选择 ${esc(r.name)}"></td>
      <td class="col-name"><span class="fund-name">${esc(r.name)}</span></td>
      <td class="col-code"><button class="code-btn" data-open="${esc(r.code)}">${esc(r.code)}</button></td>
      <td class="col-index"><div class="index-cell"><span>${esc(r.index_label)}</span></div></td>
      <td class="col-metric"><div class="metric-main-wrap">${cell.main}<div class="metric-sub">${cell.sub}</div></div></td>
      <td class="col-extra col-pos">
        <div class="pos-bar">
          <span class="pos-track"><span class="pos-fill" style="width:${pos == null ? 0 : pos}%"></span></span>
          <span class="pos-val">${pos == null ? "—" : fmtNum(pos, 0)}</span>
        </div>
      </td>
      <td class="col-extra col-amount">${fmtYi(r.amount_avg20)}</td>
      <td class="col-extra col-scale">${fmtYi(r.scale)}</td>
    </tr>`;
  }

  // 单行渲染失败时回退成可读行，绝不让一条坏数据把整批行打断
  function fallbackRowHtml(r, err) {
    const code = r && r.code ? String(r.code) : "";
    const name = r && r.name ? String(r.name) : (code || "未知基金");
    console.error("[ETF] 行渲染失败", code, err);
    return `<tr data-code="${esc(code)}" class="row-fallback">
      <td class="col-check"></td>
      <td class="col-name"><span class="fund-name">${esc(name)}</span></td>
      <td class="col-code">${esc(code)}</td>
      <td class="col-index"><div class="index-cell"><span>—</span></div></td>
      <td class="col-metric" colspan="4"><span class="metric-main flat" style="font-size:12.5px">该行数据异常，已保留占位（可刷新重试）</span></td>
    </tr>`;
  }

  function renderTable(data) {
    const body = $("#etfBody");
    const empty = $("#emptyState");
    const rows = data.rows || [];
    if (!rows.length) {
      body.innerHTML = "";
      empty.hidden = false;
    } else {
      empty.hidden = true;
      body.innerHTML = rows.map(r => {
        try {
          return rowHtml(r);
        } catch (err) {
          return fallbackRowHtml(r, err);
        }
      }).join("");
      // 渲染后校验：接口有 N 条，DOM 必须有 N 行；不一致时给出可见错误而不是静默空白
      if (body.children.length !== rows.length) {
        console.error("[ETF] 计数与行数不一致", rows.length, body.children.length);
        renderRowMismatch(rows.length, body.children.length);
      }
    }
    const shown = body.children.length;   // 计数直接来自实际渲染行，避免上下不一致
    $("#listSummary").innerHTML = `共 <b>${shown}</b> 只 · ${esc(data.tab_label)} · ${esc(data.range_label)}` +
      (S.q ? ` · 搜索「${esc(S.q)}」（已忽略地区/主题筛选）` : " · 日期见各字段标注");
    $("#rangeCount").textContent = `${data.range_label} · ${shown} 只`;
    const tab = TABS.find(t => t.k === data.tab);
    $("#metricHead").textContent = tab ? tab.metric : "溢价率";
    $("#metricHead").dataset.sort = tab && tab.k === "position" ? "premium" : data.sort;

    const ind = $("#sortCode");
    if (data.sort === "code") ind.textContent = data.order === "asc" ? " ↑" : " ↓";
    else ind.textContent = "";

    $$("#etfBody tr").forEach(tr => {
      tr.addEventListener("click", (e) => {
        if (e.target.closest(".etable-check")) return;
        location.hash = "#/etf/" + tr.dataset.code;
      });
      const cb = $(".etable-check", tr);
      if (cb) cb.addEventListener("change", (e) => {
        e.stopPropagation();
        toggleSelect(tr.dataset.code, cb.checked);
        tr.classList.toggle("selected", cb.checked);
      });
    });
  }

  async function loadList() {
    loading(true);
    try {
      const params = new URLSearchParams({
        region: S.region, theme: S.theme, q: S.q,
        range: S.range, tab: S.tab, sort: S.sort, order: S.order,
      });
      const data = await api("/api/etfs?" + params.toString());
      renderTable(data);
      setFreshness(data.meta);
    } catch (e) {
      renderError(e.message);
    } finally { loading(false); }
  }

  function renderError(msg) {
    const wrap = $("#etfTable").parentElement;
    let card = $(".error-card", wrap);
    if (!card) {
      card = document.createElement("div");
      card.className = "error-card";
      wrap.appendChild(card);
    }
    card.innerHTML = `<p>数据加载失败：${esc(msg)}</p><button class="mini-btn" id="retryBtn">重试</button>`;
    $("#retryBtn", card).addEventListener("click", () => { card.remove(); loadList(); });
  }

  function renderRowMismatch(expected, rendered) {
    const wrap = $("#etfTable").parentElement;
    let card = $(".row-mismatch", wrap);
    if (!card) {
      card = document.createElement("div");
      card.className = "error-card row-mismatch";
      wrap.appendChild(card);
    }
    card.innerHTML = `<p>列表渲染异常：接口返回 ${expected} 条，实际只渲染 ${rendered} 行。</p>
      <button class="mini-btn" id="retryRows">重新加载</button>`;
    $("#retryRows", card).addEventListener("click", () => { card.remove(); loadList(); });
  }

  async function showHome() {
    S.route = "home";
    $("#view-home").hidden = false;
    $("#view-detail").hidden = true;
    $("#view-compare").hidden = true;
    document.title = "溢价日 · 跨境 ETF 溢价监测";
    try {
      const overview = await ensureOverview();
      renderHero(overview);
      renderRegions(overview);
      renderThemes(overview);
      renderTabs();
      renderRanges();
      renderCompareBar();
      await loadList();
      loadCoverage();
    } catch (e) {
      renderError(e.message);
    }
  }

  /* ------------------------------------------------------------ 数据覆盖 */

  function renderCoverage(d) {
    const badge = $("#coverageBadge");
    const pending = (d.groups || []).filter(g => g.pct < 100);
    badge.textContent = pending.length ? `${pending.length} 项未满覆盖` : "数据完整";
    badge.classList.toggle("warn", pending.length > 0);

    $("#coverageStats").innerHTML = (d.groups || []).map(g => {
      const tone = g.pct >= 100 ? "" : (g.pct >= 70 ? "warn" : "bad");
      return `
        <div class="cov-item">
          <span class="cov-label">${esc(g.label)}</span>
          <span class="cov-track"><span class="cov-fill ${tone}" style="width:${Math.max(0, Math.min(100, g.pct))}%"></span></span>
          <span class="cov-val"><b>${g.covered}/${g.total}</b> · ${fmtNum(g.pct, 1)}%<small>${esc(g.date || "—")} · ${esc(g.note || "")}</small></span>
        </div>`;
    }).join("");

    $("#coverageReason").innerHTML =
      `统计于 ${esc(d.generated_at)} · 行情最新日 ${esc(d.trade_date || "—")}。${esc(d.reason || "")}`;

    const secs = [];
    secs.push(`<div class="cov-sec"><h4>缺口说明</h4><ul class="cov-gaps">${
      (d.gaps || []).map(g => `<li>${g}</li>`).join("")}</ul></div>`);

    if ((d.lagging || []).length) {
      const oldest = d.lagging[d.lagging.length - 1].last_nav;
      const newest = d.lagging[0].last_nav;
      secs.push(`<div class="cov-sec"><h4>官方净值滞后 <span>${d.lagging.length} 只 · ${esc(oldest)} 至 ${esc(newest)}</span></h4>
        <div class="cov-tags">${d.lagging.map(x =>
          `<span class="cov-tag">${esc(x.code)} ${esc(x.name)}<i> ${esc(x.last_nav)}</i></span>`).join("")}</div></div>`);
    }
    if ((d.index_missing || []).length) {
      secs.push(`<div class="cov-sec"><h4>未采集到行情的跟踪指数 <span>${d.index_missing.length} / ${(d.groups.find(g => g.key === "index") || {}).total || "—"}</span></h4>
        <div class="cov-tags">${d.index_missing.map(x =>
          `<span class="cov-tag">${esc(x.label || x.name || x.code)}<i> ${esc(x.code)} · ${x.funds} 只</i></span>`).join("")}</div></div>`);
    }
    if ((d.late_funds || []).length) {
      secs.push(`<div class="cov-sec"><h4>净值历史起点较晚 <span>${d.late_funds.length} 只 · 基准起点 ${esc(d.late_funds[0].missing_from)}</span></h4>
        <div class="cov-tags">${d.late_funds.map(x =>
          `<span class="cov-tag">${esc(x.code)} ${esc(x.name)}<i> ${esc(x.first_nav)} 起</i></span>`).join("")}</div></div>`);
    }
    const p = d.premium || {};
    secs.push(`<div class="cov-sec"><h4>日度溢价历史</h4>
      <div class="cov-tags">
        <span class="cov-tag">${esc(p.start || "—")} → ${esc(p.end || "—")}</span>
        <span class="cov-tag">${p.rows || 0} 条</span>
        <span class="cov-tag">剔除异常 ${p.suspect || 0} 条</span>
      </div></div>`);
    $("#coverageDetail").innerHTML = secs.join("");
  }

  async function loadCoverage() {
    try {
      const data = await api("/api/coverage");
      S.coverage = data;
      renderCoverage(data);
    } catch (e) {
      $("#coverageBadge").textContent = "不可用";
      $("#coverageBadge").classList.add("warn");
      $("#coverageReason").textContent = "覆盖统计加载失败：" + e.message;
    }
  }

  /* ------------------------------------------------------------ 选择对比 */

  function toggleSelect(code, on) {
    if (on) {
      if (S.sel.size >= MAX_SEL) {
        toast("最多对比 " + MAX_SEL + " 只基金");
        return false;
      }
      S.sel.add(code);
    } else {
      S.sel.delete(code);
    }
    renderCompareBar();
    return true;
  }

  function renderCompareBar() {
    const bar = $("#compareBar");
    if (!S.sel.size) { bar.hidden = true; return; }
    bar.hidden = false;
    $("#selCount").textContent = S.sel.size;
    const names = Array.from(S.sel);
    $("#compareChips").innerHTML = names.map(c => `
      <span class="sel-chip">${esc(c)}<button data-unsel="${esc(c)}" aria-label="移除">×</button></span>`).join("");
    $$("#compareChips [data-unsel]").forEach(b => b.addEventListener("click", (e) => {
      e.stopPropagation();
      S.sel.delete(b.dataset.unsel);
      renderCompareBar();
      const tr = $(`#etfBody tr[data-code="${b.dataset.unsel}"]`);
      if (tr) { tr.classList.remove("selected"); const cb = $(".etable-check", tr); if (cb) cb.checked = false; }
    }));
  }

  $("#clearSel").addEventListener("click", () => {
    S.sel.clear();
    renderCompareBar();
    $$("#etfBody tr.selected").forEach(tr => {
      tr.classList.remove("selected");
      const cb = $(".etable-check", tr); if (cb) cb.checked = false;
    });
  });
  $("#goCompare").addEventListener("click", () => {
    if (S.sel.size < 2) { toast("至少选择 2 只基金"); return; }
    location.hash = "#/compare/" + Array.from(S.sel).join(",");
  });

  /* ------------------------------------------------------------ 详情 */

  function sliceSeries(series, range) {
    if (!series || !series.length) return [];
    if (range === "all") return series;
    const days = DAYS[range] || 30;
    const last = new Date(series[series.length - 1].date + "T00:00:00");
    const cutoff = new Date(last.getTime() - days * 86400000);
    return series.filter(p => new Date(p.date + "T00:00:00") >= cutoff);
  }

  function detailRangePills() {
    return `<div class="range-mini">${RANGES.map(r => `
      <button class="range-pill ${S.detailRange === r.k ? "active" : ""}" data-dr="${r.k}">${r.label}</button>`).join("")}</div>`;
  }

  function positionInner(d) {
    const st = d.stats[S.detailRange];
    const rangeLabel = RANGES.find(r => r.k === S.detailRange).label;
    if (!st || st.position == null) {
      return `<div class="card-title"><h3>历史位置</h3><span class="chip-mini">${rangeLabel}</span></div>
        <p class="card-note">该区间暂无可用的历史样本。</p>`;
    }
    const pos = st.position;
    return `<div class="card-title"><h3>历史位置</h3><span class="chip-mini">${rangeLabel} · ${st.samples} 个样本</span></div>
      <div class="pos-dial">
        <div class="pos-readout"><b>${fmtNum(pos, 1)}</b><span>分位 · 越高越贵</span></div>
        <div class="pos-scale"><div class="pos-marker" style="left:${pos}%"></div></div>
        <div class="pos-scale-labels"><span>0 低位</span><span>50 中位</span><span>100 高位</span></div>
      </div>
      <p class="card-note">样本区间自 ${esc(st.cutoff)} 起至 ${esc(d.daily.date || "—")}，共 ${st.samples} 个交易日。当前溢价 ${fmtPct(d.daily.premium)} 在该区间历史样本中的百分位为 ${fmtNum(pos, 1)}。</p>`;
  }

  function rangeInner(d) {
    const st = d.stats[S.detailRange];
    const rangeLabel = RANGES.find(r => r.k === S.detailRange).label;
    const all = d.all_time;
    return `<div class="card-title"><h3>区间高低</h3><span class="chip-mini">${rangeLabel}</span></div>
      <div class="stat-pair">
        <div class="stat-box"><span>区间低位</span><b class="${cls(st.low)}">${fmtPct(st.low)}</b><small>${esc(st.low_date || "—")}</small></div>
        <div class="stat-box"><span>区间高位</span><b class="${cls(st.high)}">${fmtPct(st.high)}</b><small>${esc(st.high_date || "—")}</small></div>
      </div>
      <div class="stat-pair">
        <div class="stat-box"><span>历史最高</span><b class="up">${fmtPct(all.high)}</b><small>${esc(all.high_date || "—")}</small></div>
        <div class="stat-box"><span>历史最低</span><b class="down">${fmtPct(all.low)}</b><small>${esc(all.low_date || "—")}</small></div>
      </div>
      <p class="card-note">历史极值窗口 ${esc(all.start || "—")} 至 ${esc(d.daily.date || "—")}，共 ${all.count} 个样本。</p>`;
  }

  function detailHTML(d) {
    const p = d.profile, live = d.live, daily = d.daily, scale = d.scale, liq = d.liquidity;
    const statusTag = /暂停/.test(p.purchase_status || "")
      ? `<span class="tag warn"><i></i>${esc(p.purchase_status)}</span>`
      : `<span class="tag"><i></i>${esc(p.purchase_status || "场内交易")}</span>`;
    const headline = daily.premium != null ? daily.premium : live.premium;
    const headlineDate = daily.date || live.quote_date;
    const dev = d.deviation;

    return `
    <div class="detail-head">
      <button class="back-btn" onclick="location.hash='#/'">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg>
        返回列表
      </button>
      <div class="detail-actions">
        <button class="mini-btn" id="addCompare">+ 加入对比</button>
      </div>
    </div>

    <section class="identity">
      <div class="identity-tags">
        ${statusTag}
        <span class="tag gray"><i></i>${esc(d.meta.market_status)}</span>
        <span class="tag gray">${esc(p.region)}</span>
        ${p.risk_level ? `<span class="tag gray">风险等级 R${esc(p.risk_level)}</span>` : ""}
      </div>
      <h1 class="code">${esc(p.code)}</h1>
      <h2>${esc(p.full_name || p.name)}</h2>
      <p class="bench">跟踪 ${esc(p.index_name || p.benchmark || "—")}${p.index_code ? `（${esc(p.index_code)}）` : ""} · ${esc(p.company || "")}${p.list_date ? " · 上市 " + esc(p.list_date) : ""}</p>
      ${d.data_quality && d.data_quality.suspect_count
        ? `<p class="data-note">已识别并剔除 ${d.data_quality.suspect_count} 个拆分/折算造成的异常溢价点，统计与图表仅使用校准后样本。</p>` : ""}
    </section>

    <div class="detail-grid">
      <section class="card premium-hero">
        <div class="card-label">最近参考溢价 <span class="chip-mini">${esc(headlineDate || "—")} ${daily.premium != null ? "收盘" : "实时"}</span></div>
        <div class="big-number ${cls(headline)}">${headline == null ? "—" : (headline > 0 ? "+" : "") + fmtNum(headline, 2)}<span>%</span></div>
        <div class="big-sub">溢价率 = 收盘价 ÷ 当日官方净值 − 1</div>
        <div class="premium-spark">${window.Charts.sparkline(sliceSeries(d.series, S.detailRange).map(x => x.premium), { width: 260, height: 46 })}</div>
        <div class="quote-rows">
          <div class="quote-row"><span>最新收盘</span><b>${fmtNum(daily.close, 3)}</b></div>
          <div class="quote-row"><span>单位净值</span><b>${fmtNum(daily.nav, 4)}</b></div>
          <div class="quote-row"><span>参考溢价（净值滞后）</span><b class="${cls(live.premium)}">${fmtPct(live.premium)}</b></div>
        </div>
        <p class="card-note">参考溢价按最新价 ${fmtNum(live.price, 3)} 与最近已披露净值 ${fmtNum(daily.nav, 4)} 估算；净值日 ${esc(daily.nav_date || "—")}。公开行情接口未提供 IOPV，不以均价代替。</p>
      </section>

      <section class="card chart-card">
        <div class="card-title">
          <div>
            <h3>溢价率走势</h3>
            <p>折线为日度收盘溢价，浅色区间为样本 20–80 分位带。悬停查看每日收盘价与净值。</p>
          </div>
        </div>
        <div class="detail-range-row">${detailRangePills()}</div>
        <div class="chart-shell" id="detailChart"></div>
      </section>

      <section class="card" id="posCard">${positionInner(d)}</section>

      <section class="card" id="rangeCard">${rangeInner(d)}</section>

      <section class="card">
        <div class="card-title"><h3>规模与份额</h3><span class="chip-mini">参考口径</span></div>
        <div class="stat-pair">
          <div class="stat-box"><span>参考基金规模</span><b>${fmtYi(scale.value)}</b><small>业务日期 ${esc(scale.nav_date || "—")}</small></div>
          <div class="stat-box"><span>最新场内份额</span><b>${scale.shares_m ? fmtNum(scale.shares_m, 1) + "百万" : "—"}</b><small>快照日期 ${esc(scale.shares_date || "—")}</small></div>
        </div>
        <p class="card-note">规模 = 场内份额 × 当日官方单位净值；不使用场内价格估算。季报口径 ${fmtYi(scale.quarter_scale)} / ${scale.quarter_shares_m ? fmtNum(scale.quarter_shares_m, 1) + "百万份" : "—"}（${esc(scale.quarter_date || "—")}）。</p>
      </section>

      <section class="card liquidity-card">
        <div class="card-title"><h3>流动性趋势</h3><span class="chip-mini">近 60 个交易日</span></div>
        <div class="stat-pair">
          <div class="stat-box"><span>最近交易日成交额</span><b>${fmtYi(liq.latest_amount)}</b><small>交易日 ${esc(liq.latest_date || "—")}</small></div>
          <div class="stat-box"><span>近20日平均成交额</span><b>${fmtYi(liq.avg20)}</b><small>有效交易日简单平均</small></div>
        </div>
        <div class="liquidity-chart" id="liqChart"></div>
        <p class="card-note">每日成交额按交易所开市日展示；20 日平均按有效日成交额简单平均，数据缺口不补零。</p>
      </section>

      <section class="card">
        <div class="card-title"><h3>交易与费率</h3></div>
        <div class="quote-rows" style="grid-template-columns:1fr 1fr">
          <div class="quote-row"><span>申购状态</span><b style="font-size:12.5px">${esc(p.purchase_status || "—")}</b></div>
          <div class="quote-row"><span>赎回状态</span><b style="font-size:12.5px">${esc(p.redeem_status || "—")}</b></div>
          <div class="quote-row"><span>管理费率</span><b>${p.fee_mgmt == null ? "—" : fmtNum(p.fee_mgmt, 2) + "%"}</b></div>
          <div class="quote-row"><span>托管费率</span><b>${p.fee_custody == null ? "—" : fmtNum(p.fee_custody, 2) + "%"}</b></div>
        </div>
        <div class="stat-box" style="margin-top:8px">
          <span>近 20 日指数与基金偏离</span>
          <b class="${dev ? cls(dev.value) : "flat"}">${dev ? (dev.value > 0 ? "+" : "") + fmtNum(dev.value, 2) + "pp" : "指数行情未覆盖"}</b>
          <small>${dev ? `基金净值 ${fmtPct(dev.fund_return)} · 指数 ${fmtPct(dev.index_return)}（${fmtNum(dev.window, 0)} 个交易日）` : "跟踪指数缺少公开日行情"}</small>
        </div>
      </section>

      <section class="card history-card">
        <div class="card-title">
          <div><h3>历史溢价明细</h3><p>按日期范围查询已入库的每日收盘价、单位净值与溢价率。</p></div>
          <a class="mini-btn" id="csvLink" href="#" target="_blank" rel="noopener">导出 CSV</a>
        </div>
        <form class="filter-bar" id="histForm">
          <div class="field"><label for="fromDate">开始日期</label><input type="date" id="fromDate" required></div>
          <div class="field"><label for="toDate">结束日期</label><input type="date" id="toDate" required></div>
          <button class="mini-btn primary" type="submit">查询</button>
          <button class="mini-btn" type="button" id="lastMonth">近一月</button>
          <button class="mini-btn" type="button" id="lastYear">近一年</button>
        </form>
        <div class="history-summary" id="histSummary"></div>
        <div class="hist-scroll"><table class="hist-table" id="histTable"></table></div>
      </section>

      ${d.peers && d.peers.length ? `<section class="card">
        <div class="card-title"><h3>同指数 ETF</h3><span class="chip-mini">${esc(p.index_label)}</span></div>
        <div class="peers">${d.peers.map(x => `<a class="peer-chip" href="#/etf/${esc(x.code)}">${esc(x.name)} · ${esc(x.code)}</a>`).join("")}</div>
      </section>` : ""}
    </div>`;
  }

  async function showDetail(code) {
    S.route = "detail";
    $("#view-home").hidden = true;
    $("#view-compare").hidden = true;
    $("#view-detail").hidden = false;
    const view = $("#view-detail");
    view.innerHTML = `<div class="skeleton-row" style="padding:40px 0;max-width:520px">
      <div class="sk" style="width:36%;height:22px"></div>
      <div class="sk" style="width:70%;height:30px"></div>
      <div class="sk" style="width:100%;height:120px"></div>
      <div class="sk" style="width:100%;height:200px"></div>
    </div>`;
    loading(true);
    try {
      const d = await api("/api/etf/" + encodeURIComponent(code));
      S.detail = d;
      S.detailRange = "1m";
      setFreshness(d.meta);
      view.innerHTML = detailHTML(d);
      document.title = `${d.profile.code} ${d.profile.name} · 溢价日`;
      renderDetailContent(d);

      $("#addCompare").addEventListener("click", () => {
        if (!S.sel.has(code)) {
          if (!toggleSelect(code, true)) return;
        }
        toast("已加入对比：" + code);
      });
      view.onclick = (e) => {
        const btn = e.target.closest("[data-dr]");
        if (btn) setDetailRange(d, btn.dataset.dr);
      };
      $("#histForm").addEventListener("submit", (e) => { e.preventDefault(); loadHistory(code); });
      $("#lastMonth").addEventListener("click", () => {
        const to = $("#toDate").value || d.meta.trade_date;
        $("#fromDate").value = shiftDate(to, -30);
        loadHistory(code);
      });
      $("#lastYear").addEventListener("click", () => {
        const to = $("#toDate").value || d.meta.trade_date;
        $("#fromDate").value = shiftDate(to, -365);
        loadHistory(code);
      });
      initHistoryDates(d);
    } catch (e) {
      view.innerHTML = `<div class="error-card"><p>详情加载失败：${esc(e.message)}</p><button class="mini-btn" onclick="location.reload()">重新加载</button></div>`;
    } finally { loading(false); }
  }

  function shiftDate(dateStr, days) {
    const d = new Date((dateStr || "2026-01-01") + "T00:00:00");
    d.setDate(d.getDate() + days);
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }

  function initHistoryDates(d) {
    const end = d.meta.trade_date || d.daily.date;
    $("#toDate").value = end;
    $("#fromDate").value = shiftDate(end, -90);
    loadHistory(d.profile.code);
  }

  function renderDetailContent(d) {
    const chartEl = $("#detailChart");
    if (chartEl) renderDetailChart(d);
    const liq = $("#liqChart");
    if (liq) window.Charts.amountChart(liq, d.liquidity.series, { height: 110 });
  }

  function setDetailRange(d, range) {
    S.detailRange = range;
    $$("[data-dr]").forEach(b => b.classList.toggle("active", b.dataset.dr === range));
    const pos = $("#posCard");
    if (pos) pos.innerHTML = positionInner(d);
    const rc = $("#rangeCard");
    if (rc) rc.innerHTML = rangeInner(d);
    const spark = $(".premium-spark");
    if (spark) spark.innerHTML = window.Charts.sparkline(sliceSeries(d.series, range).map(x => x.premium), { width: 260, height: 46 });
    renderDetailChart(d);
  }

  function renderDetailChart(d) {
    const el = $("#detailChart");
    if (!el) return;
    const points = sliceSeries(d.series, S.detailRange);
    window.Charts.premiumChart(el, points, { height: window.innerWidth < 640 ? 230 : 300 });
  }

  async function loadHistory(code) {
    const from = $("#fromDate").value;
    const to = $("#toDate").value;
    if (!from || !to) { toast("请选择完整的日期范围"); return; }
    if (from > to) { toast("开始日期不能晚于结束日期"); return; }
    loading(true);
    try {
      const data = await api(`/api/history?code=${encodeURIComponent(code)}&from=${from}&to=${to}`);
      $("#histSummary").innerHTML = `
        <span class="hsum">区间样本 <b>${data.stats.samples}</b></span>
        <span class="hsum">当前 <b class="${cls(data.stats.current)}">${fmtPct(data.stats.current)}</b></span>
        <span class="hsum">分位 <b>${fmtNum(data.stats.position, 1)}</b></span>
        <span class="hsum">低 <b class="down">${fmtPct(data.stats.low)}</b> / 高 <b class="up">${fmtPct(data.stats.high)}</b></span>
        <span class="hsum">均值 <b>${fmtPct(data.stats.avg)}</b></span>`;
      const table = $("#histTable");
      table.innerHTML = `<thead><tr><th>日期</th><th>收盘价</th><th>单位净值</th><th>溢价率</th><th>区间分位</th><th>成交额</th></tr></thead>
        <tbody>${data.rows.slice().reverse().map(r => `
          <tr>
            <td>${esc(r.date)}</td>
            <td>${fmtNum(r.close, 3)}</td>
            <td>${fmtNum(r.nav, 4)}</td>
            <td class="${cls(r.premium)}">${fmtPct(r.premium)}</td>
            <td>${r.position == null ? "—" : fmtNum(r.position, 1)}</td>
            <td>${fmtYi(r.amount)}</td>
          </tr>`).join("")}</tbody>`;
      $("#csvLink").href = `${API_BASE}/api/history.csv?code=${encodeURIComponent(code)}&from=${from}&to=${to}`;
    } catch (e) {
      toast("历史查询失败：" + e.message);
    } finally { loading(false); }
  }

  /* ------------------------------------------------------------ 对比 */

  async function showCompare(codesParam) {
    S.route = "compare";
    $("#view-home").hidden = true;
    $("#view-detail").hidden = true;
    $("#view-compare").hidden = false;
    const view = $("#view-compare");
    view.innerHTML = `<div class="skeleton-row" style="padding:40px 0;max-width:640px">
      <div class="sk" style="width:30%;height:26px"></div>
      <div class="sk" style="width:100%;height:300px"></div>
    </div>`;
    const codes = (codesParam || "").split(",").filter(Boolean).slice(0, MAX_SEL);
    if (codes.length < 2) {
      view.innerHTML = `<div class="compare-head"><h1>ETF 对比</h1><p>请从列表勾选 2–4 只基金后进行对比。</p>
        <p><a class="mini-btn" href="#/">返回列表</a></p></div>`;
      return;
    }
    loading(true);
    try {
      try { await ensureOverview(); } catch (e) { /* 顶部新鲜度失败不阻塞对比 */ }
      const data = await api(`/api/compare?codes=${codes.join(",")}&range=${S.compareRange}`);
      S.compare = data;
      renderCompareView(view, data, codes);
    } catch (e) {
      view.innerHTML = `<div class="error-card"><p>对比数据加载失败：${esc(e.message)}</p><button class="mini-btn" onclick="location.reload()">重试</button></div>`;
    } finally { loading(false); }
  }

  function renderCompareView(view, data, codes) {
    const items = data.items || [];
    if (items.length < 2) {
      view.innerHTML = `
        <div class="compare-head">
          <div class="detail-head" style="padding:0 0 14px">
            <button class="back-btn" onclick="location.hash='#/'">← 返回列表</button>
          </div>
          <h1>ETF 对比</h1>
        </div>
        <div class="error-card">
          <p>${esc(data.note || "所选基金缺少足够的溢价历史，无法对比")}</p>
          <p>${(data.skipped || []).map(s => `${esc(s.code)}（${esc(s.reason)}）`).join("、")}</p>
          <a class="mini-btn" href="#/">重新选择</a>
        </div>`;
      return;
    }

    const aligned = data.aligned_dates || 0;
    view.innerHTML = `
      <div class="compare-head">
        <div class="detail-head" style="padding:0 0 14px">
          <button class="back-btn" onclick="location.hash='#/'">← 返回列表</button>
          <div class="range-mini">${RANGES.filter(r => ["3m", "6m", "1y", "3y", "all"].includes(r.k)).map(r =>
            `<button class="range-pill ${S.compareRange === r.k ? "active" : ""}" data-cr="${r.k}">${r.label}</button>`).join("")}</div>
        </div>
        <h1>ETF 对比</h1>
        <p>${esc(data.range_label)} · 共同交易日 <b>${aligned}</b> 个 · ${esc(data.start || "")} 至 ${esc(data.end || "")}</p>
        <p class="compare-note">${esc(data.note || "")}。「参考溢价」按最新价 ÷ 最近已披露净值实时计算；「最新溢价」为对齐区间内最后一个共同交易日的收盘口径，受净值披露（T+1）影响。${(data.skipped || []).length ? `已跳过：${data.skipped.map(s => esc(s.code)).join("、")}` : ""}</p>
      </div>
      <div class="compare-grid">
        <section class="card chart-card" style="grid-column:auto">
          <div class="legend-row">${items.map(s =>
            `<span class="legend-item"><span class="legend-swatch" style="background:${s.color}"></span>${esc(s.name)} · ${esc(s.code)}</span>`).join("")}</div>
          <div class="chart-shell" id="compareChart"></div>
        </section>
        <section class="card">
          <div class="compare-table-wrap">
            <table class="compare-table">
              <thead><tr><th>基金</th><th>参考溢价</th><th>最新溢价</th><th>区间均值</th><th>区间低 / 高</th><th>历史位置</th><th>样本</th></tr></thead>
              <tbody>${items.map(s => {
                const st = s.stats || {};
                const lv = s.live;
                return `<tr>
                  <td><a href="#/etf/${esc(s.code)}"><b>${esc(s.name)}</b><br><span style="color:#9aa89f;font-size:11px">${esc(s.code)} · ${esc(s.index_label || "")}</span></a></td>
                  <td class="${cls(lv && lv.premium)}">${lv ? `<b>${fmtPct(lv.premium)}</b><br><span style="color:#9aa89f;font-size:11px">最新价 ${esc(lv.quote_date || "")}</span>` : "—"}</td>
                  <td class="${cls(st.latest)}"><b>${fmtPct(st.latest)}</b><br><span style="color:#9aa89f;font-size:11px">${esc(st.latest_date || "")}</span></td>
                  <td>${fmtPct(st.avg)}</td>
                  <td><span class="down">${fmtPct(st.low)}</span> / <span class="up">${fmtPct(st.high)}</span>
                      <br><span style="color:#9aa89f;font-size:11px">${esc(st.low_date || "")} / ${esc(st.high_date || "")}</span></td>
                  <td>${st.position == null ? "—" : fmtNum(st.position, 0)}</td>
                  <td>${st.samples == null ? "—" : st.samples}</td>
                </tr>`; }).join("")}</tbody>
            </table>
          </div>
        </section>
      </div>`;

    const chartEl = $("#compareChart");
    window.Charts.multiChart(chartEl, items, { height: window.innerWidth < 640 ? 260 : 340 });
    $$("[data-cr]").forEach(btn => btn.addEventListener("click", () => {
      S.compareRange = btn.dataset.cr;
      showCompare(codes.join(","));
    }));
  }

  /* ------------------------------------------------------------ 数据说明 */

  function aboutModal() {
    showModal("数据说明", `
      <h4>数据来源</h4>
      <p>全部数据来自东方财富公开、免登录的行情与基金档案接口：ETF 日 K 线、基金官方单位净值、最新价与当日均价、场内份额、基金基本概况。采集后落地到本地 SQLite，页面只读取本地库。</p>
      <h4>指标口径</h4>
      <ul>
        <li><b>日度溢价率</b>：(收盘价 − 当日官方单位净值) ÷ 单位净值。跨境 ETF 净值存在滞后，溢价可能显著偏离，属于该产品的典型特征。</li>
        <li><b>参考溢价</b>：(最新价 − 最近已披露单位净值) ÷ 净值，用于参考盘中偏离；公开行情接口未提供 IOPV，不使用均价代替。</li>
        <li><b>历史位置</b>：当前溢价在所选区间历史样本中的百分位排名，越高表示相对越贵。</li>
        <li><b>规模与份额</b>：场内份额 × 当日单位净值；不使用场内价格估算。</li>
        <li><b>指数与基金偏离</b>：近 20 个交易日基金净值涨幅 − 跟踪指数涨幅，缺指数日行情时显示未覆盖。</li>
      </ul>
      <h4>更新方式</h4>
      <p>页面顶部「同步最新数据」可触发一次后台增量采集；服务端也会按计划自动更新。采集完成后仅重建本地 SQLite，不修改数据源。</p>
      <h4>数据质量</h4>
      <p>拆分 / 份额折算造成的单日孤立极值（价格与净值切换差一天）会被自动识别并剔除，详情页会提示校准条数；跟踪指数行情未覆盖时不做猜测填充。</p>
      <h4>合规与免责</h4>
      <p>数据仅用于研究与信息展示，可能存在延迟、缺失或接口口径变化；不构成投资建议，也不应作为交易依据。请遵守数据源的使用条款，控制采集频率。</p>
    `);
  }

  function serviceModal() {
    showModal("联系客服", `
      <p>会员权益、数据定制或接口合作，请添加微信客服。</p>
      <div class="service-box">
        <div class="service-qr" aria-hidden="true"></div>
        <div>
          <b>溢价日 · 微信 CodeTester</b>
          <p>工作日 9:30–18:00 在线，添加时请备注「溢价日」。</p>
          <button class="copy-btn" id="copyWechat">复制微信号 CodeTester</button>
        </div>
      </div>
      <h4>免费权限</h4>
      <ul>
        <li>当前溢价、参考溢价与基础档案</li>
        <li>近一月历史位置、区间高低与走势图</li>
        <li>最多 4 只 ETF 的横向对比</li>
      </ul>
      <h4>会员权益（演示占位）</h4>
      <ul>
        <li>近三月及以上区间历史位置与曲线</li>
        <li>数据导出、监控提醒与自定义样本池</li>
      </ul>
    `);
    const btn = $("#copyWechat");
    if (btn) btn.addEventListener("click", () => copyText("CodeTester"));
  }

  /* ------------------------------------------------------------ 路由 */

  function route() {
    const raw = location.hash.replace(/^#\/?/, "");
    const parts = raw.split("/");
    if (parts[0] === "etf" && parts[1]) return showDetail(decodeURIComponent(parts[1]));
    if (parts[0] === "compare") return showCompare(decodeURIComponent(parts.slice(1).join("/")));
    return showHome();
  }

  /* ------------------------------------------------------------ 事件 */

  $("#btnAbout").addEventListener("click", aboutModal);
  $("#btnService").addEventListener("click", serviceModal);
  $("#btnRefresh").addEventListener("click", () => {
    S.overview = null;
    if (S.route === "detail" && S.detail) return showDetail(S.detail.profile.code);
    if (S.route === "compare") return route();
    return showHome();
  });

  $("#coverageToggle").addEventListener("click", () => {
    S.covExpanded = !S.covExpanded;
    $("#coverageDetail").hidden = !S.covExpanded;
    $("#coverageToggle").textContent = S.covExpanded ? "收起明细" : "查看明细";
    $("#coverageToggle").setAttribute("aria-expanded", S.covExpanded ? "true" : "false");
  });

  /* ------------------------------------------------------------ 数据同步 */
  const SYNC = { timer: null, busy: false };

  function syncMetaText(st) {
    const last = st.last_sync || {};
    if (st.state === "running") {
      return `${st.started_at || ""} 开始 · 正在增量抓取行情与官方净值…`;
    }
    if (st.state === "success") {
      return `本次新增 ${st.added || 0} 条 · 刷新 ${st.updated || 0} 只 · ${st.finished_at || ""}`;
    }
    if (st.state === "failed") {
      return st.message || "同步失败，请稍后重试";
    }
    if (last.at) {
      return `上次同步 ${last.at} · ${last.note || "已完成"}`;
    }
    return "尚未同步，点击右侧按钮增量更新最新行情与净值";
  }

  function paintSync(st) {
    const strip = $("#syncStrip");
    const btn = $("#btnSync");
    if (!strip || !btn) return;
    const running = st.state === "running";
    strip.classList.toggle("running", running);
    strip.classList.toggle("failed", st.state === "failed");
    btn.disabled = running;
    $("#syncBtnText").textContent = running ? "同步中…" : "同步最新数据";
    $("#syncTitle").textContent = running ? "正在同步" : (st.state === "failed" ? "同步失败" : "数据同步");
    $("#syncMeta").textContent = syncMetaText(st);
    $("#syncMeta").title = syncMetaText(st);
  }

  function scheduleSyncPoll(delay) {
    clearTimeout(SYNC.timer);
    SYNC.timer = setTimeout(checkSync, delay || 1500);
  }

  async function checkSync() {
    try {
      const st = await api("/api/sync/status");
      paintSync(st);
      // 跟踪最新同步时间：无论是本页触发、定时任务还是别的端同步，
      // last_sync.at 一有变化就更新顶栏；页面空闲时每 30 秒检查一次
      const at = (st.last_sync || {}).at || "";
      if (at && at !== S.lastSyncAt) {
        const first = !S.lastSyncAt;
        S.lastSyncAt = at;
        if (st.state === "success") setFreshness(S.meta);
        if (!first && !SYNC.busy && st.state === "success") {
          toast(`数据已更新：新增 ${st.added || 0} 条 · ${at.slice(11) || ""}`);
          S.overview = null;
          if (S.route === "home") showHome();
        }
      }
      if (st.state === "running") {
        scheduleSyncPoll();
        return st;
      }
      scheduleSyncPoll(30000);
      if (SYNC.busy) {
        SYNC.busy = false;
        if (st.state === "success") {
          toast(`同步完成：新增 ${st.added || 0} 条，刷新 ${st.updated || 0} 只基金`);
          S.overview = null;
          if (S.route === "home") showHome();
        } else if (st.state === "failed") {
          toast(st.message || "同步失败，请稍后重试");
        }
      }
      return st;
    } catch (e) {
      clearTimeout(SYNC.timer);
      SYNC.busy = false;
      paintSync({ state: "failed", message: "无法获取同步状态：" + e.message, last_sync: {} });
      scheduleSyncPoll(30000);
      return null;
    }
  }

  $("#btnSync").addEventListener("click", async () => {
    if (SYNC.busy || $("#btnSync").disabled) return;
    SYNC.busy = true;
    $("#btnSync").disabled = true;
    $("#syncStrip").classList.add("running");
    $("#syncBtnText").textContent = "同步中…";
    $("#syncTitle").textContent = "正在同步";
    $("#syncMeta").textContent = "已提交同步任务，正在等待后台采集…";
    try {
      const st = await fetch(API_BASE + "/api/sync", { method: "POST" }).then(async (res) => {
        let body = null;
        try { body = await res.json(); } catch (e) { body = null; }
        return { ok: res.ok, body };
      }).then(({ ok, body }) => {
        if (!ok) throw new Error((body && (body.message || body.error)) || "同步请求被拒绝");
        return body;
      });
      paintSync(st || { state: "running" });
      scheduleSyncPoll();
    } catch (e) {
      SYNC.busy = false;
      $("#btnSync").disabled = false;
      $("#syncStrip").classList.remove("running");
      $("#syncTitle").textContent = "同步失败";
      $("#syncMeta").textContent = e.message;
      toast("同步启动失败：" + e.message);
      checkSync();
    }
  });

  $("#btnReset").addEventListener("click", () => {
    S.region = "all"; S.theme = "all"; S.q = ""; S.sort = ""; S.order = ""; S.themeExpanded = false;
    $("#searchInput").value = "";
    $("#clearSearch").hidden = true;
    renderRegions(S.overview); renderThemes(S.overview); loadList();
  });

  let searchTimer = null;
  $("#searchInput").addEventListener("input", (e) => {
    const v = e.target.value.trim();
    $("#clearSearch").hidden = !v;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      S.q = v;
      if (v && (S.region !== "all" || S.theme !== "all")) {
        // 搜索跨全样本，避免与地区/主题筛选叠加造成“搜索后为空”
        S.region = "all"; S.theme = "all";
        renderRegions(S.overview); renderThemes(S.overview);
      }
      loadList();
    }, 320);
  });
  $("#clearSearch").addEventListener("click", () => {
    $("#searchInput").value = "";
    $("#clearSearch").hidden = true;
    S.q = "";
    loadList();
  });

  $("#checkAll").addEventListener("change", (e) => {
    const rows = $$("#etfBody tr");
    if (e.target.checked) {
      for (const tr of rows) {
        if (S.sel.size >= MAX_SEL) { toast("最多对比 4 只基金"); break; }
        if (!S.sel.has(tr.dataset.code)) {
          S.sel.add(tr.dataset.code);
          tr.classList.add("selected");
          const cb = $(".etable-check", tr); if (cb) cb.checked = true;
        }
      }
      renderCompareBar();
    } else {
      S.sel.clear();
      renderCompareBar();
      $$("#etfBody tr").forEach(tr => {
        tr.classList.remove("selected");
        const cb = $(".etable-check", tr); if (cb) cb.checked = false;
      });
    }
  });

  $$("[data-sort]").forEach(th => th.addEventListener("click", () => {
    const key = th.dataset.sort;
    if (S.sort === key) S.order = S.order === "asc" ? "desc" : "asc";
    else { S.sort = key; S.order = key === "code" || key === "name" ? "asc" : "desc"; }
    loadList();
  }));

  $("#modal").addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (S.route === "detail" && S.detail) {
        renderDetailContent(S.detail);
        renderDetailChart(S.detail);
      }
      if (S.route === "compare" && S.compare) {
        window.Charts.multiChart($("#compareChart"), S.compare.items, { height: window.innerWidth < 640 ? 260 : 340 });
      }
    }, 180);
  });

  window.addEventListener("hashchange", route);
  checkSync();
  route();
})();
