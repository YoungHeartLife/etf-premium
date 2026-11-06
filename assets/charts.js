/* 轻量 SVG 图表：溢价曲线 / 迷你走势 / 成交额柱 / 多基金对比 */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const UP = "#0c7a55";
  const DOWN = "#b8402e";
  const INK = "#10231b";

  function el(tag, attrs, text) {
    const node = document.createElementNS(NS, tag);
    if (attrs) for (const k in attrs) node.setAttribute(k, attrs[k]);
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function num(v) { return v === null || v === undefined || Number.isNaN(v) ? "" : v; }
  function pct(v, digits = 2) {
    if (v === null || v === undefined) return "—";
    const s = v > 0 ? "+" : "";
    return s + v.toFixed(digits) + "%";
  }
  function money(v) {
    if (v === null || v === undefined) return "—";
    if (v >= 100) return v.toFixed(0) + "亿";
    if (v >= 1) return v.toFixed(2) + "亿";
    return (v * 10000).toFixed(0) + "万";
  }
  function mmdd(d) { return d ? d.slice(5).replace("-", "/") : ""; }
  function niceTicks(min, max, count) {
    const span = max - min || 1;
    const raw = span / (count - 1);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = Math.ceil(raw / mag) * mag;
    const start = Math.floor(min / step) * step;
    const ticks = [];
    for (let v = start; v <= max + step * .5; v += step) ticks.push(v);
    return ticks;
  }

  /* ---------------------------------------------------------- 迷你走势 */
  function sparkline(values, opts) {
    const o = opts || {};
    const w = o.width || 76, h = o.height || 26;
    const vals = values.filter(v => v !== null && v !== undefined);
    if (!vals.length) return "";
    const min = Math.min(...vals), max = Math.max(...vals);
    const span = max - min || 1;
    const stepX = w / Math.max(vals.length - 1, 1);
    const y = v => h - 2 - ((v - min) / span) * (h - 4);
    const pts = vals.map((v, i) => `${(i * stepX).toFixed(1)},${y(v).toFixed(1)}`);
    const color = vals[vals.length - 1] >= 0 ? UP : DOWN;
    const zero = min < 0 && max > 0 ? y(0) : h;
    const area = `M0,${h} L${pts.join(" L")} L${w},${h} Z`;
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true">
      <path d="${area}" fill="${color}" opacity=".07"></path>
      ${min < 0 && max > 0 ? `<line x1="0" y1="${zero}" x2="${w}" y2="${zero}" stroke="#cbd8cf" stroke-width="1" stroke-dasharray="2 3"></line>` : ""}
      <polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"></polyline>
    </svg>`;
  }

  /* ---------------------------------------------------------- 溢价曲线 */
  function premiumChart(container, points, opts) {
    const o = opts || {};
    const h = o.height || 280;
    container.innerHTML = "";
    const width = Math.max(container.clientWidth || 640, 300);
    if (!points || points.length < 2) {
      container.innerHTML = '<div class="empty" style="padding:36px 10px"><span>该区间暂无足够的净值样本</span></div>';
      return;
    }
    const pad = { l: 46, r: 16, t: 22, b: 28 };
    const iw = width - pad.l - pad.r, ih = h - pad.t - pad.b;
    const vals = points.map(p => p.premium);
    let min = Math.min(...vals), max = Math.max(...vals);
    // 仅当数据本身接近 0 时才把 0 纳入坐标范围，避免高溢价基金被压扁
    const dataSpan = max - min || 1;
    if (min > 0 && min <= dataSpan * 0.6) min = 0;
    if (max < 0 && -max <= dataSpan * 0.6) max = 0;
    const span = max - min || 1;
    min -= span * .06; max += span * .06;
    const x = i => pad.l + (points.length === 1 ? 0 : i / (points.length - 1)) * iw;
    const y = v => pad.t + ((max - v) / (max - min)) * ih;
    const baseVal = min > 0 ? min : (max < 0 ? max : 0);
    const zeroY = y(baseVal);
    const sorted = [...vals].sort((a, b) => a - b);
    const p20 = sorted[Math.floor(sorted.length * .2)];
    const p80 = sorted[Math.floor(sorted.length * .8)];

    const svg = el("svg", {
      width: "100%", height: h, viewBox: `0 0 ${width} ${h}`,
      preserveAspectRatio: "none", role: "img",
      "aria-label": "溢价率历史曲线"
    });

    // 20-80 分位带
    svg.appendChild(el("rect", {
      x: pad.l, y: Math.min(y(p80), y(p20)), width: iw,
      height: Math.abs(y(p20) - y(p80)),
      fill: "#2c8a6b", opacity: ".055", rx: 3
    }));

    // 网格与 Y 轴
    const ticks = niceTicks(min, max, 5);
    const tickStep = ticks.length > 1 ? Math.abs(ticks[1] - ticks[0]) : 1;
    const tickDigits = tickStep >= 0.1 ? 1 : 2;
    ticks.forEach(t => {
      if (t > max + span * .02 || t < min - span * .02) return;
      const yy = y(t);
      svg.appendChild(el("line", { x1: pad.l, x2: width - pad.r, y1: yy, y2: yy, stroke: "#edf1ed", "stroke-width": 1 }));
      const label = el("text", { x: pad.l - 8, y: yy + 3.5, "text-anchor": "end", "font-size": "10", fill: "#9aa89f" },
        (t > 0 ? "+" : "") + t.toFixed(tickDigits) + "%");
      svg.appendChild(label);
    });

    // 零轴
    if (min < 0 && max > 0) {
      svg.appendChild(el("line", {
        x1: pad.l, x2: width - pad.r, y1: zeroY, y2: zeroY,
        stroke: "#c3d3c8", "stroke-width": 1, "stroke-dasharray": "4 4"
      }));
    }

    // X 轴日期
    const tickCount = Math.min(6, points.length);
    for (let i = 0; i < tickCount; i++) {
      const idx = Math.round(i * (points.length - 1) / Math.max(tickCount - 1, 1));
      const px = x(idx);
      svg.appendChild(el("text", {
        x: px, y: h - 8, "text-anchor": i === 0 ? "start" : (i === tickCount - 1 ? "end" : "middle"),
        "font-size": "10", fill: "#9aa89f"
      }, mmdd(points[idx].date)));
    }

    // 面积 + 折线
    const linePts = points.map((p, i) => `${x(i).toFixed(1)},${y(p.premium).toFixed(1)}`);
    const areaD = `M${x(0).toFixed(1)},${zeroY.toFixed(1)} L${linePts.join(" L")} L${x(points.length - 1).toFixed(1)},${zeroY.toFixed(1)} Z`;
    const defs = el("defs");
    const grad = el("linearGradient", { id: "pg", x1: "0", y1: "0", x2: "0", y2: "1" });
    grad.appendChild(el("stop", { offset: "0%", "stop-color": UP, "stop-opacity": ".22" }));
    grad.appendChild(el("stop", { offset: "100%", "stop-color": UP, "stop-opacity": "0" }));
    defs.appendChild(grad);
    svg.appendChild(defs);
    svg.appendChild(el("path", { d: areaD, fill: "url(#pg)" }));
    svg.appendChild(el("polyline", {
      points: linePts.join(" "), fill: "none", stroke: UP,
      "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"
    }));

    // 高低点
    const hi = points.reduce((a, b) => (b.premium > a.premium ? b : a), points[0]);
    const lo = points.reduce((a, b) => (b.premium < a.premium ? b : a), points[0]);
    const hiIdx = points.indexOf(hi), loIdx = points.indexOf(lo);
    [[hi, hiIdx, -1], [lo, loIdx, 1]].forEach(([pt, idx, dir]) => {
      const cx = x(idx), cy = y(pt.premium);
      svg.appendChild(el("circle", { cx, cy, r: 3.4, fill: "#fff", stroke: dir < 0 ? DOWN : UP, "stroke-width": 1.8 }));
      let ly = cy + (dir < 0 ? -12 : 18);
      // 避免高位标签越出上边界、低位标签压到 X 轴日期
      if (ly < pad.t + 10) ly = cy + 16;
      if (ly > h - pad.b - 4) ly = cy - 10;
      const anchor = cx < pad.l + 40 ? "start" : (cx > width - pad.r - 40 ? "end" : "middle");
      const tx = cx < pad.l + 40 ? cx + 3 : (cx > width - pad.r - 40 ? cx - 3 : cx);
      svg.appendChild(el("text", {
        x: tx, y: ly, "text-anchor": anchor, "font-size": "10",
        fill: dir < 0 ? DOWN : UP, "font-weight": "600",
        stroke: "#fff", "stroke-width": "3", "paint-order": "stroke"
      }, pct(pt.premium)));
    });

    // 最新交易日：极值点之外单独标注具体数值
    const lastIdx = points.length - 1;
    const last = points[lastIdx];
    if (lastIdx !== hiIdx && lastIdx !== loIdx) {
      const lx = x(lastIdx), ly2 = y(last.premium);
      svg.appendChild(el("circle", { cx: lx, cy: ly2, r: 3.6, fill: UP, stroke: "#fff", "stroke-width": 1.8 }));
      const anchor = lx < pad.l + 46 ? "start" : (lx > width - pad.r - 46 ? "end" : "middle");
      const tx = lx < pad.l + 46 ? lx + 4 : (lx > width - pad.r - 46 ? lx - 4 : lx);
      const ty = ly2 < pad.t + 26 ? ly2 + 17 : ly2 - 10;
      svg.appendChild(el("text", {
        x: tx, y: ty, "text-anchor": anchor, "font-size": "10.5", fill: UP, "font-weight": "700",
        stroke: "#fff", "stroke-width": "3", "paint-order": "stroke"
      }, pct(last.premium)));
    }

    // 交互层
    const hoverLine = el("line", {
      x1: 0, x2: 0, y1: pad.t, y2: pad.t + ih,
      stroke: "#8fb6a2", "stroke-width": 1, "stroke-dasharray": "3 3", opacity: "0"
    });
    const hoverDot = el("circle", { r: 4, fill: UP, stroke: "#fff", "stroke-width": 2, opacity: "0" });
    svg.appendChild(hoverLine);
    svg.appendChild(hoverDot);

    container.appendChild(svg);
    const tip = document.createElement("div");
    tip.className = "chart-tip";
    container.appendChild(tip);

    function move(evt) {
      const rect = svg.getBoundingClientRect();
      const px = (evt.clientX - rect.left) / rect.width * width;
      let idx = Math.round((px - pad.l) / iw * (points.length - 1));
      idx = Math.max(0, Math.min(points.length - 1, idx));
      const p = points[idx];
      const cx = x(idx), cy = y(p.premium);
      hoverLine.setAttribute("x1", cx); hoverLine.setAttribute("x2", cx);
      hoverLine.setAttribute("opacity", "1");
      hoverDot.setAttribute("cx", cx); hoverDot.setAttribute("cy", cy);
      hoverDot.setAttribute("opacity", "1");
      tip.innerHTML = `<div><em>${p.date}</em></div><div><b>${pct(p.premium)}</b></div>
        <div><em>收盘 ${num(p.close)} · 净值 ${num(p.nav)}</em></div>
        <div><em>成交额 ${p.amount === undefined || p.amount === null ? "—" : money(p.amount)}</em></div>`;
      // 贴边时把提示框收回可视区，避免被裁切
      const tw = tip.offsetWidth || 140, th = tip.offsetHeight || 60;
      const tipX = Math.max(tw / 2 + 4, Math.min(rect.width - tw / 2 - 4, cx / width * rect.width));
      const tipY = Math.max(th + 6, cy - 12);
      tip.style.left = tipX + "px";
      tip.style.top = tipY + "px";
      tip.style.opacity = "1";
    }
    function leave() {
      hoverLine.setAttribute("opacity", "0");
      hoverDot.setAttribute("opacity", "0");
      tip.style.opacity = "0";
    }
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerleave", leave);
  }

  /* ---------------------------------------------------------- 成交额柱 */
  function amountChart(container, items, opts) {
    const o = opts || {};
    const h = o.height || 120;
    container.innerHTML = "";
    if (!items || !items.length) {
      container.innerHTML = '<div class="empty" style="padding:22px"><span>暂无成交额数据</span></div>';
      return;
    }
    container.style.position = "relative";
    const width = Math.max(container.clientWidth || 500, 260);
    const pad = { l: 0, r: 0, t: 8, b: 18 };
    const iw = width - pad.l - pad.r, ih = h - pad.t - pad.b;
    const max = Math.max(...items.map(d => d.amount), .001);
    const step = iw / items.length;
    const bw = Math.max(3, step - 3);
    const svg = el("svg", { width: "100%", height: h, viewBox: `0 0 ${width} ${h}`, preserveAspectRatio: "none" });
    items.forEach((d, i) => {
      const bh = Math.max(2, d.amount / max * ih);
      const x = pad.l + i * step;
      const last = i === items.length - 1;
      svg.appendChild(el("rect", {
        x: x + 1, y: pad.t + ih - bh, width: bw, height: bh, rx: 2,
        fill: last ? "#17624b" : "#d6e5da"
      }));
    });
    const lab = el("text", { x: 0, y: h - 5, "font-size": "10", fill: "#9aa89f" }, mmdd(items[0].date));
    svg.appendChild(lab);
    const lab2 = el("text", { x: width, y: h - 5, "text-anchor": "end", "font-size": "10", fill: "#9aa89f" }, mmdd(items[items.length - 1].date));
    svg.appendChild(lab2);

    // 悬浮高亮与成交额提示
    const mark = el("rect", { x: 0, y: pad.t - 2, width: bw + 2, height: ih + 4, rx: 3, fill: "#2c8a6b", opacity: "0" });
    svg.appendChild(mark);
    container.appendChild(svg);
    const tip = document.createElement("div");
    tip.className = "chart-tip";
    container.appendChild(tip);

    function move(evt) {
      const rect = svg.getBoundingClientRect();
      const px = (evt.clientX - rect.left) / rect.width * width;
      let idx = Math.floor((px - pad.l) / step);
      idx = Math.max(0, Math.min(items.length - 1, idx));
      const d = items[idx];
      const bx = pad.l + idx * step + 1;
      const bh = Math.max(2, d.amount / max * ih);
      mark.setAttribute("x", bx);
      mark.setAttribute("opacity", ".12");
      tip.innerHTML = `<div><em>${d.date}</em></div>
        <div><b>${money(d.amount)}</b></div>
        <div><em>收盘 ${num(d.close)}</em></div>`;
      const tw = tip.offsetWidth || 120;
      const tipX = Math.max(tw / 2 + 4, Math.min(rect.width - tw / 2 - 4, (bx + bw / 2) / width * rect.width));
      const barTop = pad.t + ih - bh;
      tip.style.left = tipX + "px";
      tip.style.top = Math.max(4, barTop - 4) + "px";
      tip.style.opacity = "1";
    }
    function leave() {
      mark.setAttribute("opacity", "0");
      tip.style.opacity = "0";
    }
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerleave", leave);
  }

  /* ---------------------------------------------------------- 多基金对比 */
  function multiChart(container, items, opts) {
    const o = opts || {};
    const h = o.height || 320;
    container.innerHTML = "";
    if (!items.length) {
      container.innerHTML = '<div class="empty" style="padding:40px"><span>暂无可对比数据</span></div>';
      return;
    }
    const width = Math.max(container.clientWidth || 700, 320);
    const pad = { l: 46, r: 18, t: 18, b: 28 };
    const iw = width - pad.l - pad.r, ih = h - pad.t - pad.b;
    const all = items.flatMap(s => s.points.map(p => p.premium));
    let min = Math.min(0, ...all), max = Math.max(0, ...all);
    const span = max - min || 1; min -= span * .08; max += span * .08;
    const y = v => pad.t + ((max - v) / (max - min)) * ih;
    const svg = el("svg", { width: "100%", height: h, viewBox: `0 0 ${width} ${h}`, preserveAspectRatio: "none" });

    const ticks = niceTicks(min, max, 5);
    const tickStep = ticks.length > 1 ? Math.abs(ticks[1] - ticks[0]) : 1;
    const tickDigits = tickStep >= 0.1 ? 1 : 2;
    ticks.forEach(t => {
      if (t > max || t < min) return;
      const yy = y(t);
      svg.appendChild(el("line", { x1: pad.l, x2: width - pad.r, y1: yy, y2: yy, stroke: "#edf1ed" }));
      svg.appendChild(el("text", { x: pad.l - 8, y: yy + 3.5, "text-anchor": "end", "font-size": "10", fill: "#9aa89f" },
        (t > 0 ? "+" : "") + t.toFixed(tickDigits) + "%"));
    });
    if (min < 0 && max > 0) {
      svg.appendChild(el("line", { x1: pad.l, x2: width - pad.r, y1: y(0), y2: y(0), stroke: "#c3d3c8", "stroke-dasharray": "4 4" }));
    }

    const dates = [...new Set(items.flatMap(s => s.points.map(p => p.date)))].sort();
    const tickCount = Math.min(6, dates.length);
    for (let i = 0; i < tickCount; i++) {
      const idx = Math.round(i * (dates.length - 1) / Math.max(tickCount - 1, 1));
      const px = pad.l + (idx / Math.max(dates.length - 1, 1)) * iw;
      svg.appendChild(el("text", {
        x: px, y: h - 8,
        "text-anchor": i === 0 ? "start" : (i === tickCount - 1 ? "end" : "middle"),
        "font-size": "10", fill: "#9aa89f"
      }, mmdd(dates[idx])));
    }

    items.forEach(s => {
      const byDate = new Map(s.points.map(p => [p.date, p.premium]));
      const pts = dates.map((d, i) => byDate.has(d)
        ? `${(pad.l + (i / Math.max(dates.length - 1, 1)) * iw).toFixed(1)},${y(byDate.get(d)).toFixed(1)}`
        : null).filter(Boolean);
      if (pts.length < 2) return;
      svg.appendChild(el("polyline", {
        points: pts.join(" "), fill: "none", stroke: s.color,
        "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round", opacity: ".95"
      }));
      const last = s.points[s.points.length - 1];
      const idx = dates.indexOf(last.date);
      svg.appendChild(el("circle", {
        cx: pad.l + (idx / Math.max(dates.length - 1, 1)) * iw,
        cy: y(last.premium), r: 3, fill: s.color, stroke: "#fff", "stroke-width": 1.5
      }));
    });

    const hoverLine = el("line", { y1: pad.t, y2: pad.t + ih, x1: 0, x2: 0, stroke: "#95b7a4", "stroke-dasharray": "3 3", opacity: 0 });
    svg.appendChild(hoverLine);
    container.appendChild(svg);
    const tip = document.createElement("div");
    tip.className = "chart-tip";
    container.appendChild(tip);

    function move(evt) {
      const rect = svg.getBoundingClientRect();
      const px = (evt.clientX - rect.left) / rect.width * width;
      let idx = Math.round((px - pad.l) / iw * (dates.length - 1));
      idx = Math.max(0, Math.min(dates.length - 1, idx));
      const cx = pad.l + (idx / Math.max(dates.length - 1, 1)) * iw;
      hoverLine.setAttribute("x1", cx); hoverLine.setAttribute("x2", cx); hoverLine.setAttribute("opacity", "1");
      const date = dates[idx];
      const rows = items.map(s => {
        const p = s.points.find(x => x.date === date);
        return `<div style="display:flex;justify-content:space-between;gap:12px"><span>${s.name}</span><b style="color:${s.color}">${p ? pct(p.premium) : "—"}</b></div>`;
      }).join("");
      tip.innerHTML = `<div><em>${date}</em></div>${rows}`;
      const tw = tip.offsetWidth || 180;
      const tipX = Math.max(tw / 2 + 4, Math.min(rect.width - tw / 2 - 4, cx / width * rect.width));
      tip.style.left = tipX + "px";
      tip.style.top = "12px";
      tip.style.transform = "translate(-50%, 0)";
      tip.style.opacity = "1";
    }
    function leave() { hoverLine.setAttribute("opacity", "0"); tip.style.opacity = "0"; }
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerleave", leave);
  }

  window.Charts = { sparkline, premiumChart, amountChart, multiChart, pct, money };
})();
