"use strict";

/* ========== 工具函数 ========== */

const TOKEN = document.querySelector('meta[name="local-token"]').content;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
const fmt = (n) => Number(n || 0).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const compact = (n) => {
  const a = Math.abs(n);
  if (a >= 1e8) return (n / 1e8).toFixed(1).replace(/\.0$/, "") + "亿";
  if (a >= 1e4) return (n / 1e4).toFixed(a >= 1e5 ? 0 : 1).replace(/\.0$/, "") + "万";
  return fmt(n);
};
const yuan = (b) => "¥" + (Number(b || 0) / 10).toLocaleString("zh-CN", { maximumFractionDigits: 1 });
const signed = (n) => (n > 0 ? "+" : "") + fmt(n);
const pnClass = (n) => (n > 0 ? "pos" : n < 0 ? "neg" : "");
const isBiliImg = (u) => typeof u === "string" && /^https:\/\/[a-z0-9.-]+\.hdslb\.com\//i.test(u);
const img = (u, cls = "gimg") =>
  isBiliImg(u) ? `<img class="${cls}" src="${esc(u)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : `<span class="${cls}"></span>`;
const dateStr = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString("zh-CN") : "");
const ago = (ts) => {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return "刚刚";
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
};
const store = {
  get(k, d) { try { const v = localStorage.getItem("bililocal:" + k); return v ? JSON.parse(v) : d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("bililocal:" + k, JSON.stringify(v)); } catch { /* 隐私模式下忽略 */ } },
};

async function api(path, { method = "GET", body, raw = false } = {}) {
  const headers = { "X-Bili-Local-Token": TOKEN };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (raw && res.ok) return res;
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (res.status === 401 && data.need_login) {
    showLogin();
    throw new Error(data.error);
  }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 3200);
}

/* ========== 状态 ========== */

const state = {
  tab: store.get("tab", "overview"),
  range: store.get("range", { name: "all", start: "", end: "" }),
  status: null,
  rec: { q: "", ruid: "", kind: "all", page: 1 },
  medals: null,
  wallet: null,
  medalSort: { key: "level", dir: -1 },
  medalQ: "",
};
let renderSeq = 0;
const charts = []; // 当前页面上的图表，窗口大小变化时重画

function rangeQS() {
  const r = state.range;
  const p = new URLSearchParams({ range: r.name });
  if (r.name === "custom") {
    if (r.start) p.set("start", r.start);
    if (r.end) p.set("end", r.end);
  }
  return p.toString();
}

/* ========== 登录 ========== */

let qrTimer = null;
let qrKey = null;

function showLogin() {
  $("#app").hidden = true;
  $("#account").hidden = true;
  $("#login").hidden = false;
  newQr();
}

async function newQr() {
  clearTimeout(qrTimer);
  const qr = $("#qr");
  qr.classList.remove("expired");
  qr.innerHTML = '<div class="spinner"></div>';
  $("#qrRefresh").hidden = true;
  $("#qrStatus").textContent = "正在生成二维码…";
  try {
    const r = await api("/api/qr", { method: "POST" });
    qrKey = r.key;
    qr.innerHTML = r.svg; // 本机 Python 生成的二维码 SVG
    $("#qrStatus").textContent = "请用 B站 App 扫码";
    pollQr();
  } catch (e) {
    $("#qrStatus").textContent = e.message;
    $("#qrRefresh").hidden = false;
  }
}

function pollQr() {
  qrTimer = setTimeout(async () => {
    if ($("#login").hidden) return;
    try {
      const r = await api(`/api/qr/poll?key=${encodeURIComponent(qrKey)}`);
      if (r.code === 0) {
        $("#qrStatus").textContent = "登录成功，正在进入…";
        const st = await api("/api/status");
        applyStatus(st);
        showApp();
        if (!st.records) startSync();
        return;
      }
      if (r.code === 86090) $("#qrStatus").textContent = "已扫码，请在手机上点「确认登录」";
      else if (r.code === 86101) $("#qrStatus").textContent = "请用 B站 App 扫码";
      else {
        $("#qrStatus").textContent = r.code === 86038 ? "二维码已失效" : r.message || "二维码已失效";
        $("#qr").classList.add("expired");
        $("#qrRefresh").hidden = false;
        return;
      }
    } catch (e) {
      $("#qrStatus").textContent = e.message;
    }
    pollQr();
  }, 1500);
}

/* ========== 顶栏 / 同步 ========== */

function applyStatus(st) {
  state.status = st;
  $("#demoTag").hidden = !st.demo;
  if (!st.logged_in) return;
  $("#uname").textContent = st.user.uname;
  const av = $("#avatar");
  if (isBiliImg(st.user.face)) av.src = st.user.face;
  else av.removeAttribute("src");
  const s = st.sync || {};
  const txt = $("#syncText");
  txt.classList.toggle("error", !!s.error && !s.running);
  if (s.running) txt.textContent = s.text;
  else if (s.error) txt.textContent = s.error;
  else if (st.last_sync) txt.textContent = `${fmt(st.records)} 条记录 · ${ago(st.last_sync)}同步`;
  else txt.textContent = "还没有同步过";
  $("#syncBtn").disabled = !!s.running;
  $("#syncBtn").textContent = s.running ? "同步中…" : "同步数据";
}

let statusTimer;
let syncing = false;
async function startSync() {
  try {
    await api("/api/sync", { method: "POST" });
    syncing = true;
  } catch (e) {
    toast(e.message);
  }
  pollStatus();
}

async function pollStatus() {
  clearTimeout(statusTimer);
  let st;
  try {
    st = await api("/api/status");
  } catch {
    return;
  }
  applyStatus(st);
  if (st.sync?.running) {
    syncing = true;
    statusTimer = setTimeout(pollStatus, 1200);
  } else if (syncing) {
    syncing = false;
    toast(st.sync.error || st.sync.text || "同步完成");
    render();
  }
}

/* ========== 主框架 ========== */

function showApp() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#account").hidden = false;
  $$(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === state.tab));
  syncRangeUI();
  render();
}

function syncRangeUI() {
  $$("#rangeSeg button").forEach((b) => b.classList.toggle("active", b.dataset.range === state.range.name));
  $("#customRange").hidden = state.range.name !== "custom";
  $("#rangeStart").value = state.range.start || "";
  $("#rangeEnd").value = state.range.end || "";
}

const VIEWS = {
  overview: viewOverview,
  records: viewRecords,
  blindbox: viewBlindbox,
  synthesis: viewSynthesis,
  medals: viewMedals,
  security: viewSecurity,
};
const RANGED = new Set(["overview", "records", "blindbox"]);

async function render() {
  const seq = ++renderSeq;
  const view = $("#view");
  $("#rangebar").hidden = !RANGED.has(state.tab);
  charts.length = 0;
  view.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  try {
    await VIEWS[state.tab](view, () => seq === renderSeq);
  } catch (e) {
    if (seq === renderSeq) view.innerHTML = `<div class="card empty"><h2>加载失败</h2><p>${esc(e.message)}</p></div>`;
  }
}

function needSyncEmpty(text) {
  const synced = state.status?.last_sync;
  return `<div class="card empty">
    <h2>${synced ? esc(text) : "还没有数据"}</h2>
    <p>${synced ? "换个时间范围试试。" : "点「同步数据」从 B站拉取你的直播消费记录（B站只保留最近约一年）。"}</p>
    ${synced ? "" : '<button class="btn primary" data-action="sync">同步数据</button>'}
  </div>`;
}

/* ========== 图表 ========== */

function niceStep(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

// 柱子：数据端 4px 圆角，基线端方角
function barPath(x, y0, w, h, up) {
  const r = Math.min(4, w / 2, Math.abs(h));
  if (Math.abs(h) < 0.5) return "";
  if (up) {
    const top = y0 - h;
    return `M${x},${y0}V${top + r}Q${x},${top} ${x + r},${top}H${x + w - r}Q${x + w},${top} ${x + w},${top + r}V${y0}Z`;
  }
  const bot = y0 + h;
  return `M${x},${y0}V${bot - r}Q${x},${bot} ${x + r},${bot}H${x + w - r}Q${x + w},${bot} ${x + w},${bot - r}V${y0}Z`;
}

/** 单系列柱状图，支持负值（盈亏）。spec: {data:[{label, value, tip}], height} */
function drawColumns(el, spec) {
  const W = Math.max(280, el.clientWidth);
  const H = spec.height || 220;
  const m = { l: 46, r: 8, t: 10, b: 24 };
  const iw = W - m.l - m.r;
  const ih = H - m.t - m.b;
  const vals = spec.data.map((d) => d.value);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const stepV = niceStep((hi - lo) / 4 || 1);
  const minV = Math.floor(lo / stepV) * stepV;
  const maxV = Math.max(stepV, Math.ceil(hi / stepV) * stepV);
  const y = (v) => m.t + ((maxV - v) / (maxV - minV)) * ih;
  const ticks = [];
  for (let v = minV; v <= maxV + stepV / 2; v += stepV) ticks.push(v);
  const n = spec.data.length || 1;
  const band = iw / n;
  const bw = Math.max(2, Math.min(24, band - Math.max(2, band * 0.3)));
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(iw / 52))));
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${esc(spec.label || "")}">`;
  for (const t of ticks) {
    s += `<line class="grid-line" x1="${m.l}" x2="${W - m.r}" y1="${y(t)}" y2="${y(t)}"/>`;
    s += `<text x="${m.l - 8}" y="${y(t) + 4}" text-anchor="end">${esc(compact(Math.round(t)))}</text>`;
  }
  spec.data.forEach((d, i) => {
    const x = m.l + band * i + (band - bw) / 2;
    const y0 = y(0);
    const up = d.value >= 0;
    const h = Math.abs(y(d.value) - y0);
    s += `<rect class="hit" x="${m.l + band * i}" y="${m.t}" width="${band}" height="${ih}" data-tip="${esc(d.tip)}"/>`;
    s += `<path class="bar${up ? "" : " neg"}" d="${barPath(x, y0, bw, h, up)}" pointer-events="none"/>`;
    if (i % every === 0) s += `<text x="${m.l + band * i + band / 2}" y="${H - 6}" text-anchor="middle">${esc(d.label)}</text>`;
  });
  el.innerHTML = s + "</svg>";
}

/** 日历热力图。spec: {days:{'YYYY-MM-DD': value}, from: Date, to: Date} */
function drawHeat(el, spec) {
  const cell = 12, gap = 2, step = cell + gap;
  const start = new Date(spec.from);
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7)); // 对齐到周一
  const end = new Date(spec.to);
  const weeks = Math.max(1, Math.ceil((end - start) / 864e5 / 7 + 1 / 7));
  const vals = Object.values(spec.days).filter((v) => v > 0).sort((a, b) => a - b);
  const q = (p) => vals[Math.min(vals.length - 1, Math.floor(p * vals.length))] || 0;
  const cuts = [q(0.2), q(0.45), q(0.7), q(0.9)];
  const level = (v) => (v <= 0 ? 0 : 1 + cuts.filter((c) => v > c).length);
  const L = 22, T = 16;
  const W = L + weeks * step, H = T + 7 * step;
  let s = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="每日消费日历">`;
  ["一", "三", "五"].forEach((d, i) => (s += `<text x="0" y="${T + (i * 2) * step + 10}" font-size="10">${d}</text>`));
  let lastMonth = -1;
  const pad = (n) => String(n).padStart(2, "0");
  for (let w = 0; w < weeks; w++) {
    for (let d = 0; d < 7; d++) {
      const day = new Date(start);
      day.setDate(start.getDate() + w * 7 + d);
      if (day > end) break;
      const key = `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
      const v = spec.days[key] || 0;
      if (d === 0 && day.getMonth() !== lastMonth && day.getDate() <= 7) {
        lastMonth = day.getMonth();
        s += `<text x="${L + w * step}" y="10" font-size="10">${day.getMonth() + 1}月</text>`;
      }
      const tip = `${key}\n${v ? fmt(v) + " 电池（" + yuan(v) + "）" : "没有消费"}`;
      s += `<rect x="${L + w * step}" y="${T + d * step}" width="${cell}" height="${cell}" rx="3" fill="var(--heat-${level(v)})" data-tip="${esc(tip)}"/>`;
    }
  }
  el.innerHTML = s + "</svg>";
}

function hbars(rows, { max } = {}) {
  if (!rows.length) return '<p class="muted">没有数据</p>';
  const top = max ?? Math.max(...rows.map((r) => r.value), 1);
  return `<div class="hbar">${rows
    .map(
      (r) => `<div class="hbar-row" data-tip="${esc(r.tip)}">
      <div class="name">${r.icon || ""}<span title="${esc(r.name)}">${esc(r.name)}</span></div>
      <div class="track"><div class="fill" style="width:${Math.max(0.5, (r.value / top) * 100)}%"></div></div>
      <div class="val">${esc(r.label)}</div></div>`
    )
    .join("")}</div>`;
}

function mountCharts(root) {
  for (const c of charts) {
    const el = $(`[data-chart="${c.id}"]`, root);
    if (el) c.draw(el, c.spec);
  }
}
let chartId = 0;
function chart(draw, spec) {
  const id = "c" + ++chartId;
  charts.push({ id, draw, spec });
  return `<div class="${draw === drawHeat ? "heat" : "chart"}" data-chart="${id}"></div>`;
}
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => mountCharts(document), 150);
});

/* 悬浮提示（所有带 data-tip 的元素） */
const tip = $("#tooltip");
document.addEventListener("mouseover", (e) => {
  const t = e.target.closest("[data-tip]");
  if (!t || !t.dataset.tip) return (tip.hidden = true);
  tip.textContent = t.dataset.tip;
  tip.style.whiteSpace = "pre-line";
  tip.hidden = false;
});
document.addEventListener("mousemove", (e) => {
  if (tip.hidden) return;
  const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  const y = e.clientY + 16 + tip.offsetHeight > window.innerHeight ? e.clientY - tip.offsetHeight - 10 : e.clientY + 16;
  tip.style.left = x + "px";
  tip.style.top = y + "px";
});
document.addEventListener("mouseout", (e) => {
  if (!e.relatedTarget || !e.relatedTarget.closest?.("[data-tip]")) tip.hidden = true;
});

/* ========== 概览 ========== */

async function viewOverview(view, alive) {
  const d = await api(`/api/overview?${rangeQS()}`);
  if (!alive()) return;
  const t = d.totals;
  if (!t.count && !t.bag_count) {
    view.innerHTML = needSyncEmpty("这个时间范围内没有消费记录");
    return;
  }
  const big = t.biggest_day;
  const months = d.by_month.map((m) => ({
    label: m.month.slice(2).replace("-", "/"),
    value: m.spent,
    tip: `${m.month}\n${fmt(m.spent)} 电池（${yuan(m.spent)}）${m.bag ? `\n另送包裹礼物 ${fmt(m.bag)} 电池` : ""}`,
  }));
  const days = Object.fromEntries(d.by_day.map((x) => [x.date, x.spent]));
  const to = new Date(t.last_ts * 1000);
  const from = new Date(Math.max(t.first_ts * 1000, to - 364 * 864e5));
  const hours = d.by_hour.map((v, h) => ({ label: String(h), value: v, tip: `${h}:00 – ${h}:59\n${fmt(v)} 电池` }));

  view.innerHTML = `
  <div class="grid tiles">
    <div class="card tile hero"><div class="label">电池消费</div><div class="value num">${fmt(t.spent)}<small>电池</small></div><div class="hint">约 ${yuan(t.spent)} · ${esc(d.range)}</div></div>
    <div class="card tile"><div class="label">送礼次数</div><div class="value num">${fmt(t.count)}</div><div class="hint">共 ${fmt(t.gift_num)} 个礼物</div></div>
    <div class="card tile"><div class="label">送过的主播</div><div class="value num">${fmt(t.anchors)}</div><div class="hint">消费 ${fmt(t.days_active)} 天</div></div>
    <div class="card tile"><div class="label">包裹礼物价值</div><div class="value num">${fmt(t.bag_value)}<small>电池</small></div><div class="hint">送出 ${fmt(t.bag_count)} 次，不花电池</div></div>
    <div class="card tile"><div class="label">花得最多的一天</div><div class="value num">${fmt(big.spent)}<small>电池</small></div><div class="hint">${esc(big.date || "-")}</div></div>
  </div>

  <div class="card">
    <h2>每月电池消费</h2><p class="sub">不含包裹礼物和已退回的记录</p>
    ${chart(drawColumns, { data: months, label: "每月电池消费" })}
  </div>

  <div class="card">
    <h2>每日消费日历</h2><p class="sub">${esc(dateStr(from / 1000))} – ${esc(dateStr(t.last_ts))}，颜色越深花得越多</p>
    ${chart(drawHeat, { days, from, to })}
    <div class="legend">少 ${[0, 1, 2, 3, 4, 5].map((i) => `<i style="background:var(--heat-${i})"></i>`).join("")} 多</div>
  </div>

  <div class="grid cols-2">
    <div class="card">
      <h2>送得最多的主播</h2><p class="sub">共 ${fmt(d.anchor_count)} 位，按电池 + 包裹礼物价值排序</p>
      ${hbars(d.top_anchors.slice(0, 10).map((a) => ({
        name: a.name, value: a.spent + a.bag, label: fmt(a.spent + a.bag),
        tip: `${a.name}\n电池 ${fmt(a.spent)} · 包裹 ${fmt(a.bag)}\n${fmt(a.count)} 次 · 最近 ${dateStr(a.last_ts)}`,
      })))}
    </div>
    <div class="card">
      <h2>花电池最多的礼物</h2><p class="sub">按电池合计排序</p>
      ${hbars(d.top_gifts.slice(0, 10).map((g) => ({
        name: g.name, icon: img(g.img), value: g.spent, label: fmt(g.spent),
        tip: `${g.name}\n${fmt(g.num)} 个 · ${fmt(g.spent)} 电池（${yuan(g.spent)}）`,
      })))}
    </div>
  </div>

  <div class="grid cols-2">
    <div class="card">
      <h2>几点最爱送礼</h2><p class="sub">按小时汇总电池消费</p>
      ${chart(drawColumns, { data: hours, height: 180, label: "按小时消费" })}
    </div>
    <div class="card">
      <h2>单笔最大的 10 次</h2><p class="sub">${esc(d.range)}</p>
      <div class="table-wrap"><table>
        <thead><tr><th>时间</th><th>主播</th><th>礼物</th><th class="r">电池</th></tr></thead>
        <tbody>${d.biggest.map((r) => `<tr>
          <td class="muted num">${esc(r.time.slice(0, 16))}</td><td>${esc(r.anchor)}</td>
          <td><div class="cell">${img(r.gift_img)}${esc(r.gift_name)} ×${r.gift_num}</div></td>
          <td class="r">${fmt(r.coin)}</td></tr>`).join("")}</tbody>
      </table></div>
    </div>
  </div>`;
  mountCharts(view);
}

/* ========== 消费明细 ========== */

async function viewRecords(view, alive) {
  const f = state.rec;
  view.innerHTML = `<div class="card">
    <div class="filters">
      <input type="search" id="recQ" placeholder="搜索礼物或主播" value="${esc(f.q)}">
      <select id="recAnchor"><option value="">全部主播</option></select>
      <select id="recKind">
        <option value="all">全部记录</option><option value="paid">只看花电池的</option>
        <option value="bag">只看包裹礼物</option><option value="refunded">只看已退回</option>
      </select>
      <span style="flex:1"></span>
      <button class="btn" data-export="csv">导出 CSV</button>
      <button class="btn" data-export="json">导出 JSON</button>
    </div>
    <div id="recBody"><div class="loading"><div class="spinner"></div></div></div>
  </div>`;
  $("#recKind").value = f.kind;
  let timer;
  $("#recQ").addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => { f.q = e.target.value; f.page = 1; loadRecords(alive); }, 280);
  });
  $("#recAnchor").addEventListener("change", (e) => { f.ruid = e.target.value; f.page = 1; loadRecords(alive); });
  $("#recKind").addEventListener("change", (e) => { f.kind = e.target.value; f.page = 1; loadRecords(alive); });
  $$("[data-export]", view).forEach((b) => b.addEventListener("click", () => exportRecords(b.dataset.export)));
  await loadRecords(alive, true);
}

function recQS(extra = {}) {
  const f = state.rec;
  const p = new URLSearchParams(rangeQS());
  for (const [k, v] of Object.entries({ q: f.q, ruid: f.ruid, kind: f.kind, page: f.page, ...extra })) if (v) p.set(k, v);
  return p.toString();
}

async function loadRecords(alive, first = false) {
  const d = await api(`/api/records?${recQS()}`);
  if (!alive()) return;
  const sel = $("#recAnchor");
  if (first || sel.options.length <= 1) {
    sel.innerHTML = '<option value="">全部主播</option>' + d.anchors.map((a) => `<option value="${esc(a.ruid)}">${esc(a.name)}</option>`).join("");
    sel.value = state.rec.ruid;
  }
  const body = $("#recBody");
  if (!d.total) {
    body.innerHTML = state.status?.records ? '<p class="empty">没有符合条件的记录</p>' : needSyncEmpty("");
    return;
  }
  body.innerHTML = `
    <p class="sub">共 ${fmt(d.total)} 条 · 合计 ${fmt(d.sum)} 电池（${yuan(d.sum)}，不含已退回）</p>
    <div class="table-wrap"><table>
      <thead><tr><th>时间</th><th>主播</th><th>礼物</th><th class="r">数量</th><th class="r">电池</th><th></th></tr></thead>
      <tbody>${d.items.map((r) => `<tr>
        <td class="muted num">${esc(r.time)}</td>
        <td>${esc(r.anchor)}</td>
        <td><div class="cell">${img(r.gift_img)}${esc(r.gift_name)}</div></td>
        <td class="r">${fmt(r.gift_num)}</td>
        <td class="r">${fmt(r.coin)}</td>
        <td>${r.bag ? '<span class="chip">包裹</span>' : ""}${r.refunded ? '<span class="chip warn">已退回</span>' : ""}</td>
      </tr>`).join("")}</tbody>
    </table></div>
    <div class="pager">
      <span class="muted">第 ${d.page} / ${d.pages} 页</span>
      <span><button class="btn small" id="prevPage" ${d.page <= 1 ? "disabled" : ""}>上一页</button>
      <button class="btn small" id="nextPage" ${d.page >= d.pages ? "disabled" : ""}>下一页</button></span>
    </div>`;
  $("#prevPage").onclick = () => { state.rec.page = d.page - 1; loadRecords(alive); };
  $("#nextPage").onclick = () => { state.rec.page = d.page + 1; loadRecords(alive); };
}

async function exportRecords(format) {
  try {
    const res = await api(`/api/export?${recQS({ format, page: "" })}`, { raw: true });
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const name = decodeURIComponent((cd.match(/filename\*=UTF-8''(.+)$/) || [])[1] || `bili-records.${format}`);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  } catch (e) {
    toast(e.message);
  }
}

/* ========== 盲盒 ========== */

async function viewBlindbox(view, alive) {
  const d = await api(`/api/blindbox?${rangeQS()}`);
  if (!alive()) return;
  if (!d.boxes.length) {
    view.innerHTML = state.status?.last_sync
      ? `<div class="card empty"><h2>没有找到盲盒记录</h2>
        <p>消费记录里没有名字带「盲盒」的礼物。如果你开过的盲盒名字里不带这两个字，<br>可以在「安全与设置」里手动填它的礼物 ID，再同步一次。</p></div>`
      : needSyncEmpty("");
    return;
  }
  const t = d.totals;
  const dash = '<span class="muted">—</span>';
  const profitTile = t.profit === null
    ? `<div class="value num">${dash}</div><div class="hint">B站没有返回开盒结果，算不了盈亏</div>`
    : `<div class="value num ${pnClass(t.profit)}">${signed(t.profit)}<small>电池</small></div>
       <div class="hint">按 ${fmt(t.draws)} 个有开盒结果的盲盒计算</div>`;
  view.innerHTML = `
  <div class="grid tiles">
    <div class="card tile hero"><div class="label">盲盒花费</div><div class="value num">${fmt(t.spent)}<small>电池</small></div><div class="hint">约 ${yuan(t.spent)} · 共 ${fmt(t.bought)} 个 · ${esc(d.range)}</div></div>
    <div class="card tile"><div class="label">盈亏</div>${profitTile}</div>
    <div class="card tile"><div class="label">爆出价值</div><div class="value num">${t.draws ? fmt(t.value) + "<small>电池</small>" : dash}</div></div>
    <div class="card tile"><div class="label">回报率</div><div class="value num">${t.rate === null ? dash : t.rate + "<small>%</small>"}</div><div class="hint">爆出价值 ÷ 花费</div></div>
  </div>
  ${t.missing ? `<div class="card"><p class="sub" style="margin:0">有 ${fmt(t.missing)} 个盲盒 B站没有返回开盒结果。B站的开盒记录接口只能查到最近一段时间的记录，更早开的盲盒只能显示购买数和花费，开出了什么、赚了还是亏了没法知道。以后定期同步，新开的盲盒结果就会存到本地。</p></div>` : ""}
  ${d.boxes.map(boxCard).join("")}`;
  mountCharts(view);
}

function boxCard(b) {
  const dash = '<span class="muted">—</span>';
  const months = b.months.map((m) => ({
    label: m.month.slice(2).replace("-", "/"), value: m.profit,
    tip: `${m.month}\n花费 ${fmt(m.spent)} · 爆出 ${fmt(m.value)}\n盈亏 ${signed(m.profit)} 电池`,
  }));
  const anchors = `<h3>按直播间</h3>
    <div class="table-wrap"><table>
      <thead><tr><th>主播</th><th class="r">购买</th><th class="r">花费</th>${b.draws ? '<th class="r">有结果</th><th class="r">盈亏</th>' : ""}</tr></thead>
      <tbody>${b.anchors.slice(0, 12).map((a) => `<tr><td>${esc(a.name || "UID " + a.ruid)}</td>
        <td class="r">${fmt(a.bought)}</td><td class="r">${fmt(a.spent)}</td>
        ${b.draws ? `<td class="r">${fmt(a.draws)}</td><td class="r ${pnClass(a.profit)}">${a.profit === null ? dash : signed(a.profit)}</td>` : ""}</tr>`).join("")}</tbody>
    </table></div>`;
  const detail = b.draws ? `<div class="grid cols-2">
      <div>
        <h3>爆出分布</h3>
        <div class="table-wrap"><table>
          <thead><tr><th>礼物</th><th class="r">价值</th><th class="r">次数</th><th class="r">实际占比</th><th class="r">官方概率</th></tr></thead>
          <tbody>${b.gifts.map((g) => `<tr>
            <td><div class="cell">${img(g.img)}${esc(g.name)}</div></td>
            <td class="r">${fmt(g.price)}</td><td class="r">${fmt(g.count)}</td>
            <td class="r">${g.share}%</td><td class="r muted">${esc(g.chance || "-")}</td></tr>`).join("")}</tbody>
        </table></div>
      </div>
      <div>
        <h3>每月盈亏</h3>
        ${chart(drawColumns, { data: months, height: 200, label: "每月盲盒盈亏" })}
        ${anchors}
      </div>
    </div>` : anchors;
  const note = !b.draws
    ? `<p class="sub">B站没有返回这个盲盒的开盒结果${b.has_pool ? "" : "，也查不到它的奖池（可能是限定活动盲盒）"}，只能显示购买数和花费。</p>`
    : b.missing ? `<p class="sub">其中 ${fmt(b.missing)} 个没有开盒结果，盈亏只按有结果的 ${fmt(b.draws)} 个计算。</p>` : "";
  return `<div class="card">
    <div class="box-head">${img(b.img)}<div><h2>${esc(b.name)}</h2><div class="muted">单价 ${fmt(b.blind_price)} 电池（${yuan(b.blind_price)}）${b.last_ts ? " · 最近一次 " + esc(dateStr(b.last_ts)) : ""}</div></div></div>
    <div class="stats-row">
      <div class="stat"><div class="label">购买</div><div class="value">${fmt(b.bought || b.draws)}</div></div>
      <div class="stat"><div class="label">花费</div><div class="value">${fmt(b.spent)}</div></div>
      <div class="stat"><div class="label">爆出价值</div><div class="value">${b.draws ? fmt(b.value) : dash}</div></div>
      <div class="stat"><div class="label">盈亏</div><div class="value ${pnClass(b.profit)}">${b.profit === null ? dash : signed(b.profit)}</div></div>
      <div class="stat"><div class="label">回报率</div><div class="value">${b.rate === null ? dash : b.rate + "%"}</div></div>
    </div>
    ${note}
    ${b.unknown ? `<p class="sub">有 ${fmt(b.unknown)} 个爆出礼物不在当前奖池里（盲盒换过奖池），这部分按 0 计价。</p>` : ""}
    ${detail}
  </div>`;
}

/* ========== 合成活动 ========== */

async function viewSynthesis(view, alive) {
  const d = await api("/api/synthesis");
  if (!alive()) return;
  const cards = d.activities.map((a) => `<div class="card">
    <h2>${esc(a.name)}</h2>
    <div class="stats-row">
      <div class="stat"><div class="label">材料花费</div><div class="value">${fmt(a.spent)}</div></div>
      <div class="stat"><div class="label">成品价值</div><div class="value">${fmt(a.earned)}</div></div>
      <div class="stat"><div class="label">盈亏（电池）</div><div class="value ${pnClass(a.profit)}">${signed(a.profit)}</div></div>
      <div class="stat"><div class="label">材料 / 成品</div><div class="value">${fmt(a.materials_num)} / ${fmt(a.products_num)}</div></div>
    </div>
    ${a.spent || a.earned ? `<div class="grid cols-2">
      <div><h3>合成出的成品</h3><div class="table-wrap"><table>
        <thead><tr><th>成品</th><th class="r">单价</th><th class="r">个数</th><th class="r">价值</th></tr></thead>
        <tbody>${a.products.map((p) => `<tr><td><div class="cell">${img(p.img)}${esc(p.name)}</div></td>
          <td class="r">${fmt(p.price)}</td><td class="r">${fmt(p.count)}</td><td class="r">${fmt(p.value)}</td></tr>`).join("") || '<tr><td colspan="4" class="muted">还没有成品记录</td></tr>'}</tbody>
      </table></div></div>
      <div><h3>按直播间</h3><div class="table-wrap"><table>
        <thead><tr><th>主播</th><th class="r">花费</th><th class="r">成品</th><th class="r">盈亏</th></tr></thead>
        <tbody>${a.anchors.slice(0, 12).map((x) => `<tr><td>${esc(x.name || "UID " + x.ruid)}</td>
          <td class="r">${fmt(x.spent)}</td><td class="r">${fmt(x.earned)}</td><td class="r ${pnClass(x.profit)}">${signed(x.profit)}</td></tr>`).join("")}</tbody>
      </table></div></div>
    </div>` : '<p class="muted">消费记录里没有这个活动的材料或成品。名字不对的话在下面改一下。</p>'}
  </div>`).join("");

  view.innerHTML = `
    <div class="card"><h2>合成活动盈亏</h2>
      <p class="sub">支出 = 买材料礼物花的电池；收入 = 合成后进背包的成品，送出去时的礼物价值。活动名单是按礼物名称匹配的，每期活动的材料和成品不同，可以在下面自己改。</p>
    </div>
    ${cards || '<div class="card empty"><h2>还没有配置活动</h2></div>'}
    <div class="card"><details ${d.config.length ? "" : "open"}>
      <summary><b>编辑活动配置</b></summary>
      <div id="actForms" style="margin-top:12px"></div>
      <div style="display:flex;gap:8px;margin-top:12px">
        <button class="btn" id="actAdd">添加活动</button>
        <button class="btn primary" id="actSave">保存</button>
      </div>
    </details></div>`;

  const forms = $("#actForms");
  const addForm = (a = { name: "", materials: [], products: [], start: "", end: "" }) => {
    const div = document.createElement("div");
    div.className = "act-form card";
    div.innerHTML = `
      <div class="row"><label>活动名</label><input type="text" data-k="name" value="${esc(a.name)}"></div>
      <div class="row"><label>材料礼物</label><textarea rows="2" data-k="materials" placeholder="每行一个，或用逗号分隔">${esc(a.materials.join("\n"))}</textarea></div>
      <div class="row"><label>成品礼物</label><textarea rows="2" data-k="products" placeholder="每行一个，或用逗号分隔">${esc(a.products.join("\n"))}</textarea></div>
      <div class="row"><label>活动时间</label><div class="dates">
        <input type="date" data-k="start" value="${esc(a.start)}"> 至 <input type="date" data-k="end" value="${esc(a.end)}">
        <span class="muted">（可留空）</span><span style="flex:1"></span><button class="btn small danger" data-del>删除</button></div></div>`;
    $("[data-del]", div).onclick = () => div.remove();
    forms.appendChild(div);
  };
  d.config.forEach(addForm);
  $("#actAdd").onclick = () => addForm();
  $("#actSave").onclick = async () => {
    const split = (s) => s.split(/[\n,，、]/).map((x) => x.trim()).filter(Boolean);
    const activities = $$(".act-form", forms).map((f) => ({
      name: $('[data-k="name"]', f).value.trim(),
      materials: split($('[data-k="materials"]', f).value),
      products: split($('[data-k="products"]', f).value),
      start: $('[data-k="start"]', f).value,
      end: $('[data-k="end"]', f).value,
    }));
    try {
      await api("/api/synthesis", { method: "PUT", body: { activities } });
      toast("已保存");
      render();
    } catch (e) {
      toast(e.message);
    }
  };
}

/* ========== 勋章与钱包 ========== */

async function viewMedals(view, alive, force = false) {
  if (!state.medals || force) {
    const [medals, wallet] = await Promise.all([api("/api/medals"), api("/api/wallet").catch(() => null)]);
    state.medals = medals;
    state.wallet = wallet;
  }
  if (!alive()) return;
  const ms = state.medals;
  const w = state.wallet;
  const lighted = ms.filter((m) => m.lighted).length;
  view.innerHTML = `
  <div class="grid tiles">
    ${w ? `<div class="card tile"><div class="label">电池余额</div><div class="value num">${fmt(w.battery)}</div><div class="hint">约 ${yuan(w.battery)}</div></div>
    <div class="card tile"><div class="label">B币</div><div class="value num">${esc(w.bp)}</div></div>` : ""}
    <div class="card tile"><div class="label">粉丝勋章</div><div class="value num">${fmt(ms.length)}</div><div class="hint">点亮 ${fmt(lighted)} 个</div></div>
    <div class="card tile"><div class="label">最高等级</div><div class="value num">${ms.length ? Math.max(...ms.map((m) => m.level)) : 0}</div></div>
    <div class="card tile"><div class="label">今日已获亲密度</div><div class="value num">${fmt(ms.reduce((s, m) => s + (m.today_feed || 0), 0))}</div></div>
  </div>
  <div class="card">
    <div class="card-head"><div><h2>粉丝勋章</h2><p class="sub">实时读取，只看不改</p></div>
      <div class="filters" style="margin:0"><input type="search" id="medalQ" placeholder="搜索主播或勋章" value="${esc(state.medalQ)}">
      <button class="btn" id="medalRefresh">刷新</button></div></div>
    <div id="medalTable"></div>
  </div>`;
  $("#medalQ").addEventListener("input", (e) => { state.medalQ = e.target.value; medalTable(); });
  $("#medalRefresh").onclick = () => { state.medals = null; render(); };
  medalTable();
}

function medalTable() {
  const q = state.medalQ.trim().toLowerCase();
  const { key, dir } = state.medalSort;
  const rows = state.medals
    .filter((m) => !q || m.anchor.toLowerCase().includes(q) || m.name.toLowerCase().includes(q))
    .sort((a, b) => {
      const av = a[key], bv = b[key];
      return (typeof av === "string" ? av.localeCompare(bv, "zh") : (av || 0) - (bv || 0)) * dir;
    });
  const th = (k, label, cls = "") =>
    `<th class="sortable ${cls}" data-sort="${k}">${label}${key === k ? (dir < 0 ? " ↓" : " ↑") : ""}</th>`;
  $("#medalTable").innerHTML = rows.length ? `<div class="table-wrap"><table>
    <thead><tr>${th("anchor", "主播")}${th("name", "勋章")}${th("level", "等级", "r")}${th("intimacy", "亲密度", "r")}${th("today_feed", "今日", "r")}<th>状态</th></tr></thead>
    <tbody>${rows.map((m) => `<tr>
      <td><div class="cell">${img(m.anchor_face, "face")}${esc(m.anchor)}${m.living ? '<span class="chip on">直播中</span>' : ""}</div></td>
      <td>${esc(m.name)}</td>
      <td class="r">${m.level}</td>
      <td class="r">${fmt(m.intimacy)} / ${fmt(m.next_intimacy)}</td>
      <td class="r">${fmt(m.today_feed)} / ${fmt(m.day_limit)}</td>
      <td>${m.lighted ? '<span class="chip on">已点亮</span>' : '<span class="chip off">未点亮</span>'}
        ${m.wearing ? '<span class="chip">佩戴中</span>' : ""}${m.guard_level ? '<span class="chip warn">大航海</span>' : ""}</td>
    </tr>`).join("")}</tbody></table></div>` : '<p class="empty">没有勋章</p>';
  $$("[data-sort]", $("#medalTable")).forEach((el) => (el.onclick = () => {
    const k = el.dataset.sort;
    state.medalSort = { key: k, dir: state.medalSort.key === k ? -state.medalSort.dir : -1 };
    medalTable();
  }));
}

/* ========== 安全与设置 ========== */

async function viewSecurity(view, alive) {
  const [s, log] = await Promise.all([api("/api/settings"), api("/api/netlog")]);
  if (!alive()) return;
  view.innerHTML = `
  <div class="grid cols-2">
    <div class="card">
      <h2>这个工具做什么、不做什么</h2>
      <ul class="plain">
        <li>只监听本机 <code>127.0.0.1</code>，局域网里的其它设备访问不到。</li>
        <li>网络请求只发往下面这几个 B站官方域名，代码里写死了白名单，别的地址发不出去。</li>
        <li>登录凭证和消费记录只存在本机的数据目录，没有任何上传、统计或遥测。</li>
        <li>只读取数据：不送礼、不发弹幕、不点赞、不关注、不删勋章、不领或兑换 B币。</li>
        <li>页面接口需要每次启动随机生成的令牌，其它网站没法偷偷调用。</li>
      </ul>
    </div>
    <div class="card">
      <h2>本机信息</h2>
      <dl class="kv">
        <dt>数据目录</dt><dd><code>${esc(s.data_dir)}</code></dd>
        <dt>允许访问</dt><dd>${s.allowed_hosts.map((h) => `<code>${esc(h)}</code>`).join(" ")}</dd>
        <dt>图片</dt><dd>头像和礼物图标直接从 B站图床 <code>*.hdslb.com</code> 加载，不带来源信息</dd>
      </dl>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><div><h2>网络请求记录</h2><p class="sub">本次启动以来发出的每一个请求（只记域名和路径，不记参数和 Cookie）</p></div>
      <button class="btn" id="logRefresh">刷新</button></div>
    <div class="table-wrap" style="max-height:340px;overflow-y:auto"><table>
      <thead><tr><th>时间</th><th>方法</th><th>域名</th><th>路径</th><th class="r">状态</th></tr></thead>
      <tbody>${log.length ? log.map((r) => `<tr><td class="muted num">${esc(new Date(r.ts * 1000).toLocaleTimeString("zh-CN"))}</td>
        <td>${esc(r.method)}</td><td><code>${esc(r.host)}</code></td><td class="muted">${esc(r.path)}</td><td class="r">${esc(r.status)}</td></tr>`).join("")
        : `<tr><td colspan="5" class="muted">${s.demo ? "演示模式不会发出任何网络请求" : "还没有发出请求"}</td></tr>`}</tbody>
    </table></div>
  </div>

  <div class="grid cols-2">
    <div class="card">
      <h2>手动补充盲盒</h2>
      <p class="sub">自动识别不到的盲盒，填礼物 ID（逗号分隔），保存后再同步一次。</p>
      <div class="filters"><input type="text" id="boxIds" placeholder="例如 32251, 35206" value="${esc(s.extra_blind_box_ids.join(", "))}">
      <button class="btn" id="boxSave">保存</button></div>
    </div>
    <div class="card">
      <h2>退出登录</h2>
      <p class="sub">退出时会调用 B站官方退出接口，让本机保存的这份登录凭证立即失效。</p>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button class="btn" id="logoutKeep">退出登录（保留本地记录）</button>
        <button class="btn danger" id="logoutPurge">退出并删除本地数据</button>
      </div>
    </div>
  </div>`;
  $("#logRefresh").onclick = () => render();
  $("#boxSave").onclick = async () => {
    const ids = $("#boxIds").value.split(/[,，\s]+/).filter(Boolean);
    try {
      await api("/api/settings", { method: "PUT", body: { extra_blind_box_ids: ids } });
      toast("已保存，下次同步时生效");
    } catch (e) {
      toast(e.message);
    }
  };
  const logout = async (purge) => {
    if (purge && !confirm("确定退出并删除本机保存的这个账号的所有消费记录吗？")) return;
    try {
      const r = await api("/api/logout", { method: "POST", body: { purge } });
      toast(r.revoked ? "已退出，凭证已在 B站失效" : "已退出（B站退出接口没有响应，凭证可能要等自然过期）");
    } catch (e) {
      toast(e.message);
    }
    state.medals = null;
    showLogin();
  };
  $("#logoutKeep").onclick = () => logout(false);
  $("#logoutPurge").onclick = () => logout(true);
}

/* ========== 事件绑定 & 启动 ========== */

$$(".tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    state.tab = b.dataset.tab;
    store.set("tab", state.tab);
    $$(".tabs button").forEach((x) => x.classList.toggle("active", x === b));
    render();
  })
);
$$("#rangeSeg button").forEach((b) =>
  b.addEventListener("click", () => {
    const name = b.dataset.range;
    state.range = { ...state.range, name };
    syncRangeUI();
    if (name === "custom") return; // 等点「应用」
    store.set("range", state.range);
    state.rec.page = 1;
    render();
  })
);
$("#rangeApply").addEventListener("click", () => {
  state.range = { name: "custom", start: $("#rangeStart").value, end: $("#rangeEnd").value };
  store.set("range", state.range);
  state.rec.page = 1;
  render();
});
$("#syncBtn").addEventListener("click", startSync);
$("#qrRefresh").addEventListener("click", newQr);
document.addEventListener("click", (e) => {
  if (e.target.closest('[data-action="sync"]')) startSync();
});

(async function boot() {
  if (!VIEWS[state.tab]) state.tab = "overview";
  try {
    const st = await api("/api/status");
    applyStatus(st);
    if (!st.logged_in) return showLogin();
    showApp();
    if (st.sync?.running) pollStatus();
  } catch (e) {
    document.body.insertAdjacentHTML("beforeend", `<div class="card empty"><h2>连接本地服务失败</h2><p>${esc(e.message)}</p></div>`);
  }
})();
