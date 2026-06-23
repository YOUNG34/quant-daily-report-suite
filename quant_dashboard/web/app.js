// 量化指标看板 — 前端逻辑
"use strict";

// ── 状态 ──────────────────────────────
const state = {
  latest: null,
  selectedCode: null,
  charts: { radar: null, price: null, score: null },
  filter: { sortBy: "score_total", holdingOnly: true },
};

// ── 工具 ──────────────────────────────
const fmt = {
  num: (v, d = 2) => v == null ? "-" : Number(v).toFixed(d),
  pct: (v, d = 2) => {
    if (v == null) return "-";
    const sign = v > 0 ? "+" : "";
    return sign + Number(v).toFixed(d) + "%";
  },
  pctClass: v => v == null ? "pct-zero" : v > 0 ? "pct-up" : v < 0 ? "pct-down" : "pct-zero",
  scoreClass: v => v == null ? "" : v >= 65 ? "score-high" : v >= 40 ? "score-mid" : "score-low",
  verdictText: v => ({ bullish: "看多", neutral: "中性", bearish: "看空" }[v] || v),
  regimeText: r => ({
    trending_up: "趋势上行",
    trending_down: "趋势下行",
    range: "震荡",
    high_vol: "高波动",
  }[r] || r),
  vpmText: v => ({
    healthy_up: "健康放量上行",
    fake_up: "假突破",
    healthy_down: "缩量回调",
    fake_down: "放量杀跌",
    neutral: "中性",
  }[v] || v || "-"),
};

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`API ${path}: ${r.status}`);
  return r.json();
}

// ── 初始化 ─────────────────────────────
async function init() {
  try {
    state.latest = await api("/api/latest");
    renderHeader();
    renderStocks();
    renderFooter();
  } catch (e) {
    document.body.innerHTML = `<div style="padding:40px;color:#ef4444;text-align:center;">
      <h2>加载失败</h2><p>${e.message}</p>
      <p style="color:#6b7280;margin-top:20px;">请先运行: python3 scripts/fetch_indicators.py</p>
    </div>`;
  }
}

// ── Header（含大盘环境） ─────────────────
function renderHeader() {
  const data = state.latest;
  document.getElementById("updated-at").textContent = data.date ? `数据: ${data.date}` : "无数据";

  const bar = document.getElementById("market-bar");
  if (!data.market) {
    bar.innerHTML = '<span class="market-item">大盘数据缺失</span>';
    return;
  }
  const m = data.market;
  const pctCls = fmt.pctClass(m.sh_pct_chg);
  bar.innerHTML = `
    <div class="market-item">
      上证 <strong>${fmt.num(m.sh_close)}</strong>
      <span class="${pctCls}">${fmt.pct(m.sh_pct_chg)}</span>
    </div>
    <div class="market-item">
      创业 <strong>${fmt.num(m.cyb_close)}</strong>
      <span class="${fmt.pctClass(m.cyb_pct_chg)}">${fmt.pct(m.cyb_pct_chg)}</span>
    </div>
    <div class="market-item">
      波动率 <strong>${fmt.num(m.volatility20, 1)}%</strong>
    </div>
    <div class="market-item">
      市场 <strong>${fmt.regimeText(m.regime)}</strong>
    </div>
    ${m.north_inflow != null ? `<div class="market-item">北向 <strong>${fmt.num(m.north_inflow / 100, 1)}亿</strong></div>` : ""}
  `;
}

// ── 股票列表 ─────────────────────────────
function renderStocks() {
  const tbody = document.querySelector("#stocks-table tbody");
  let stocks = state.latest.stocks.slice();

  if (state.filter.holdingOnly) {
    stocks = stocks.filter(s => s.is_holding);
  }
  stocks.sort((a, b) => {
    const k = state.filter.sortBy;
    return (b[k] ?? -Infinity) - (a[k] ?? -Infinity);
  });

  document.getElementById("stocks-count").textContent = stocks.length;

  tbody.innerHTML = stocks.map(s => `
    <tr data-code="${s.code}" ${s.code === state.selectedCode ? 'class="selected"' : ''}>
      <td>${s.code}</td>
      <td>${s.name}</td>
      <td>${fmt.num(s.close)}</td>
      <td class="${fmt.pctClass(s.pct_chg)}">${fmt.pct(s.pct_chg)}</td>
      <td class="${fmt.scoreClass(s.score_trend)}">${fmt.num(s.score_trend, 0)}</td>
      <td class="${fmt.scoreClass(s.score_momentum)}">${fmt.num(s.score_momentum, 0)}</td>
      <td class="${fmt.scoreClass(s.score_volume)}">${fmt.num(s.score_volume, 0)}</td>
      <td class="${fmt.scoreClass(s.score_valuation)}">${fmt.num(s.score_valuation, 0)}</td>
      <td class="${fmt.scoreClass(s.score_total)}"><strong>${fmt.num(s.score_total, 1)}</strong></td>
      <td class="verdict-${s.verdict}">${fmt.verdictText(s.verdict)}</td>
    </tr>
  `).join("");

  tbody.querySelectorAll("tr").forEach(tr => {
    tr.addEventListener("click", () => selectStock(tr.dataset.code));
  });
}

// ── 选中股票 → 加载详情 ────────────────────
async function selectStock(code) {
  state.selectedCode = code;
  document.querySelectorAll("#stocks-table tbody tr").forEach(tr => {
    tr.classList.toggle("selected", tr.dataset.code === code);
  });

  const stock = state.latest.stocks.find(s => s.code === code);
  if (!stock) return;

  document.getElementById("detail-placeholder").classList.add("hidden");
  document.getElementById("detail-content").classList.remove("hidden");
  document.getElementById("detail-title").textContent = `📌 ${code} ${stock.name} · ${stock.industry || ""}`;

  renderRadar(stock);
  renderKeyMetrics(stock);
  renderFullIndicators(stock);

  const hist = await api(`/api/history?code=${code}&days=60`);
  renderPriceChart(hist);
  renderScoreChart(hist);
}

// ── 雷达图 ─────────────────────────────
function renderRadar(stock) {
  const ctx = document.getElementById("radar-chart").getContext("2d");
  if (state.charts.radar) state.charts.radar.destroy();
  state.charts.radar = new Chart(ctx, {
    type: "radar",
    data: {
      labels: ["趋势", "动量", "量能", "估值", "情绪"],
      datasets: [{
        label: stock.name,
        data: [
          stock.score_trend ?? 0,
          stock.score_momentum ?? 0,
          stock.score_volume ?? 0,
          stock.score_valuation ?? 0,
          stock.score_sentiment ?? 0,
        ],
        backgroundColor: "rgba(59, 130, 246, 0.25)",
        borderColor: "#3b82f6",
        borderWidth: 2,
        pointBackgroundColor: "#3b82f6",
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        r: {
          min: 0, max: 100,
          ticks: { color: "#6b7280", backdropColor: "transparent", stepSize: 25 },
          grid: { color: "#2a3344" },
          angleLines: { color: "#2a3344" },
          pointLabels: { color: "#d1d5db", font: { size: 12 } },
        },
      },
    },
  });
}

// ── 关键指标卡片 ─────────────────────────
function renderKeyMetrics(s) {
  const metrics = [
    { label: "收盘", val: fmt.num(s.close), cls: "" },
    { label: "涨跌幅", val: fmt.pct(s.pct_chg), cls: fmt.pctClass(s.pct_chg) },
    { label: "ADX(14)", val: fmt.num(s.adx14, 1), cls: "" },
    { label: "RSI(14)", val: fmt.num(s.rsi14, 1), cls: "" },
    { label: "MACD柱", val: fmt.num(s.macd_hist, 3), cls: fmt.pctClass(s.macd_hist) },
    { label: "量比", val: fmt.num(s.vol_ratio, 2), cls: "" },
    { label: "PE-TTM", val: fmt.num(s.pe_ttm, 1), cls: "" },
    { label: "PE分位", val: s.pe_pct3y == null ? "-" : fmt.num(s.pe_pct3y, 0) + "%", cls: "" },
    { label: "综合分", val: fmt.num(s.score_total, 1), cls: fmt.scoreClass(s.score_total) },
    { label: "判定", val: fmt.verdictText(s.verdict), cls: "verdict-" + s.verdict },
  ];
  document.getElementById("key-metrics").innerHTML = metrics.map(m => `
    <div class="metric">
      <div class="metric-label">${m.label}</div>
      <div class="metric-value ${m.cls}">${m.val}</div>
    </div>
  `).join("");
}

// ── 价格走势 (close + MA20 + MA60) ──────────
function renderPriceChart(hist) {
  const ctx = document.getElementById("price-chart").getContext("2d");
  if (state.charts.price) state.charts.price.destroy();
  const labels = hist.history.map(r => r.date);
  state.charts.price = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "收盘", data: hist.history.map(r => r.close), borderColor: "#06b6d4", backgroundColor: "transparent", borderWidth: 2, pointRadius: 0 },
        { label: "MA20", data: hist.history.map(r => r.ma20), borderColor: "#f59e0b", backgroundColor: "transparent", borderWidth: 1.5, pointRadius: 0 },
        { label: "MA60", data: hist.history.map(r => r.ma60), borderColor: "#8b5cf6", backgroundColor: "transparent", borderWidth: 1.5, pointRadius: 0 },
      ],
    },
    options: chartOptions("¥"),
  });
}

// ── 综合分历史 ──────────────────────────
function renderScoreChart(hist) {
  const ctx = document.getElementById("score-chart").getContext("2d");
  if (state.charts.score) state.charts.score.destroy();
  const labels = hist.history.map(r => r.date);
  state.charts.score = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "综合分",
        data: hist.history.map(r => r.score_total),
        borderColor: "#22c55e",
        backgroundColor: "rgba(34, 197, 94, 0.15)",
        fill: true,
        borderWidth: 2,
        pointRadius: 0,
        tension: 0.2,
      }],
    },
    options: {
      ...chartOptions(""),
      scales: {
        x: { ticks: { color: "#6b7280", maxTicksLimit: 8 }, grid: { color: "#1e2533" } },
        y: { min: 0, max: 100, ticks: { color: "#6b7280" }, grid: { color: "#1e2533" } },
      },
    },
  });
}

function chartOptions(prefix = "") {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: "#d1d5db", font: { size: 11 } } },
      tooltip: { mode: "index", intersect: false },
    },
    scales: {
      x: { ticks: { color: "#6b7280", maxTicksLimit: 8 }, grid: { color: "#1e2533" } },
      y: { ticks: { color: "#6b7280", callback: v => prefix + v }, grid: { color: "#1e2533" } },
    },
    interaction: { mode: "nearest", axis: "x", intersect: false },
  };
}

// ── 完整指标表 ──────────────────────────
function renderFullIndicators(s) {
  const groups = [
    ["趋势", [
      ["MA5", fmt.num(s.ma5)], ["MA10", fmt.num(s.ma10)],
      ["MA20", fmt.num(s.ma20)], ["MA60", fmt.num(s.ma60)],
      ["MA20斜率", fmt.num(s.ma20_slope, 2)], ["ADX(14)", fmt.num(s.adx14, 1)],
    ]],
    ["动量", [
      ["RSI(14)", fmt.num(s.rsi14, 1)], ["MACD-DIF", fmt.num(s.macd_diff, 3)],
      ["MACD-DEA", fmt.num(s.macd_dea, 3)], ["MACD柱", fmt.num(s.macd_hist, 3)],
      ["ROC(20)", fmt.pct(s.roc20)],
    ]],
    ["量能", [
      ["量比", fmt.num(s.vol_ratio, 2)],
      ["OBV", s.obv == null ? "-" : (s.obv / 1e8).toFixed(2) + "亿"],
      ["换手率", fmt.pct(s.turnover)],
      ["量价配合", fmt.vpmText(s.vol_price_match)],
    ]],
    ["估值", [
      ["PE-TTM", fmt.num(s.pe_ttm, 1)], ["PB", fmt.num(s.pb, 2)],
      ["PE分位3y", s.pe_pct3y == null ? "-" : fmt.num(s.pe_pct3y, 0) + "%"],
      ["PB分位3y", s.pb_pct3y == null ? "-" : fmt.num(s.pb_pct3y, 0) + "%"],
    ]],
    ["资金", [
      ["主力净流入", s.net_inflow_main == null ? "-" : fmt.num(s.net_inflow_main / 1e4, 1) + "万"],
      ["大单占比", s.big_order_ratio == null ? "-" : fmt.num(s.big_order_ratio, 1) + "%"],
    ]],
    ["评分", [
      ["趋势", fmt.num(s.score_trend, 1)], ["动量", fmt.num(s.score_momentum, 1)],
      ["量能", fmt.num(s.score_volume, 1)], ["估值", fmt.num(s.score_valuation, 1)],
      ["情绪", fmt.num(s.score_sentiment, 1)], ["综合", fmt.num(s.score_total, 1)],
    ]],
  ];

  const html = groups.map(([title, items]) => `
    <div style="grid-column: span 3; margin-top: 8px; font-size: 12px; color: #6b7280;">${title}</div>
    ${items.map(([k, v]) => `
      <div class="indicator-row">
        <span class="label">${k}</span>
        <span class="value">${v}</span>
      </div>
    `).join("")}
  `).join("");

  document.getElementById("full-indicators").innerHTML = html;
}

// ── Footer ──────────────────────────
function renderFooter() {
  const fl = state.latest.fetch_log;
  if (fl) {
    document.getElementById("fetch-info").textContent =
      `上次采集: ${fl.finished_at} (${fl.n_success}/${fl.n_stocks} 成功, ${fl.duration_sec}s)`;
  }
}

// ── 过滤器事件 ────────────────────────
document.getElementById("sort-by").addEventListener("change", e => {
  state.filter.sortBy = e.target.value;
  renderStocks();
});
document.getElementById("filter-holding").addEventListener("change", e => {
  state.filter.holdingOnly = e.target.checked;
  renderStocks();
});

// 启动
init();
