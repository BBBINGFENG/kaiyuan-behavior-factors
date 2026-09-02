/* 开源金工交易行为因子 dashboard — 渲染层 (设计沿用 china-value-dashboard) */
(function () {
  "use strict";
  const DATA = window.DASHBOARD_DATA;
  if (!DATA) {
    document.querySelector("main").innerHTML =
      "<div class='card'><h2>无数据</h2><p>data/dashboard_data.js 缺失 — 运行 src/website/build_dashboard.py。</p></div>";
    return;
  }

  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const pct = (x, d = 1) => (x === null || x === undefined ? "–" : (x * 100).toFixed(d) + "%");
  const num = (x, d = 2) => (x === null || x === undefined ? "–" : Number(x).toFixed(d));
  const signClass = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
  const el = (t, c, h) => { const e = document.createElement(t); if (c) e.className = c; if (h !== undefined) e.innerHTML = h; return e; };

  /* Chart.js 全局默认 */
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 11.5;
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--grid");
  Chart.defaults.animation = false;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.legend.labels.boxHeight = 12;
  const GRID = { color: css("--grid"), drawTicks: false };
  const pctTick = (v) => (v * 100).toFixed(Math.abs(v) >= 0.1 ? 0 : 1) + "%";
  const tipPct = { callbacks: { label: (c) => ` ${c.dataset.label || ""}: ${pct(c.parsed.y ?? c.parsed, 2)}`.trim() } };

  function lineChart(id, labels, datasets, opts = {}) {
    const c = document.getElementById(id); if (!c) return;
    new Chart(c, { type: "line", data: { labels, datasets },
      options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
        plugins: { legend: { display: datasets.length > 1 }, tooltip: opts.tooltip || {} },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8, maxRotation: 0 } },
          y: Object.assign({ grid: GRID, ticks: { maxTicksLimit: 6 } }, opts.y || {}) } } });
  }
  function barChart(id, labels, datasets, opts = {}) {
    const c = document.getElementById(id); if (!c) return;
    new Chart(c, { type: "bar", data: { labels, datasets },
      options: { maintainAspectRatio: false,
        plugins: { legend: { display: !!opts.legend }, tooltip: opts.tooltip || tipPct },
        scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: opts.maxX || 12, maxRotation: 0 } },
          y: Object.assign({ grid: GRID, ticks: { maxTicksLimit: 6, callback: pctTick } }, opts.y || {}) } } });
  }
  const line = (color, w = 2) => ({ borderColor: color, backgroundColor: color, borderWidth: w, pointRadius: 0, pointHoverRadius: 4, tension: 0 });
  const barColors = (vals) => vals.map((v) => (v >= 0 ? css("--pos") : css("--neg")));

  /* ---------- 头部 meta ---------- */
  document.getElementById("meta-latest-date").textContent = DATA.meta.latest_date;
  document.getElementById("meta-generated").textContent = DATA.meta.generated_at;

  /* ---------- 数据陈旧告警(主动提示"我过期了", 不用翻日志) ---------- */
  (function () {
    const days = Math.floor((Date.now() - new Date(DATA.meta.latest_date + "T15:00:00")) / 86400000);
    if (days >= 5) {
      const b = el("div", "stale-banner",
        `⚠️ 数据已 ${days} 天未更新（最新 ${DATA.meta.latest_date}）— 每日更新任务可能失败，请检查 live_update.log 并手动运行 ./run_daily_update.sh`);
      document.querySelector("main").prepend(b);
    }
  })();

  /* ---------- 关键指标(Live) ---------- */
  const S = DATA.live.stats;
  const tiles = [
    ["今年累计收益", pct(S.total), `${S.start}~${S.end}`, signClass(S.total)],
    ["今年年化", pct(S.ann), "按日年化", signClass(S.ann)],
    ["今年 Sharpe", num(S.sharpe), "rf≈0", signClass(S.sharpe)],
    ["今年最大回撤", pct(S.mdd), "每日净值", "neg"],
    ["今年年化波动", pct(S.vol), `${S.days} 交易日`, ""],
    ["回测年化(参考)", pct(DATA.backtest.ann), "2010–2026 多空", "pos"],
  ];
  const row = document.getElementById("stat-row");
  tiles.forEach(([label, value, sub, cls]) => {
    const t = el("div", "stat-tile");
    t.appendChild(el("div", "label", label));
    t.appendChild(el("div", "value " + cls, value));
    t.appendChild(el("div", "sub", sub));
    row.appendChild(t);
  });

  /* ---------- 图表 ---------- */
  const A = css("--accent");
  // Live 净值
  lineChart("chart-live-nav", DATA.live.nav.dates,
    [Object.assign({ label: "Live 净值", data: DATA.live.nav.vals, fill: true,
      backgroundColor: A + "18" }, line(A))],
    { tooltip: { callbacks: { label: (c) => " 净值: " + num(c.parsed.y, 3) } } });
  // Live 回撤
  const NEG = css("--neg");
  lineChart("chart-live-dd", DATA.live.dd.dates,
    [Object.assign({ label: "回撤", data: DATA.live.dd.vals, fill: true, backgroundColor: NEG + "20" }, line(NEG, 1.5))],
    { y: { ticks: { callback: pctTick, maxTicksLimit: 6 } }, tooltip: tipPct });
  // 历史回测净值(对数)
  lineChart("chart-hist-nav", DATA.backtest.nav.dates,
    [Object.assign({ label: "回测净值", data: DATA.backtest.nav.vals }, line(A, 1.6))],
    { y: { type: "logarithmic" }, tooltip: { callbacks: { label: (c) => " 净值: " + num(c.parsed.y, 2) } } });
  // 月度收益
  barChart("chart-monthly", DATA.monthly.dates,
    [{ label: "月度多空", data: DATA.monthly.vals, backgroundColor: barColors(DATA.monthly.vals) }], { maxX: 8 });
  // 年度收益
  barChart("chart-annual", DATA.annual.years,
    [{ label: "年度多空", data: DATA.annual.vals, backgroundColor: barColors(DATA.annual.vals) }], { maxX: 20 });
  // 各因子最新月
  barChart("chart-factor-month", DATA.factor_month.names,
    [{ label: "最新月多空", data: DATA.factor_month.vals, backgroundColor: barColors(DATA.factor_month.vals) }], { maxX: 6 });

  /* ---------- 持仓表 ---------- */
  document.getElementById("holdings-meta").textContent = DATA.holdings.meta;
  function holdTable(id, rows) {
    let h = "<tr><th>代码</th><th>名称</th><th>合成分</th></tr>";
    rows.forEach((r) => { h += `<tr><td>${r.code}</td><td>${r.name}</td><td>${num(r.score, 3)}</td></tr>`; });
    document.getElementById(id).innerHTML = h;
  }
  holdTable("table-long", DATA.holdings.longs);
  holdTable("table-short", DATA.holdings.shorts);

  /* ---------- 因子绩效 + 显著性 ---------- */
  const tstar = (t) => { const a = Math.abs(t); const s = a >= 2.58 ? "***" : a >= 1.96 ? "**" : a >= 1.65 ? "*" : ""; return num(t, 2) + (s ? `<sup>${s}</sup>` : ""); };
  let ft = "<tr><th>因子</th><th>IC均值</th><th>ICIR年化</th><th>多空IR</th><th>年化收益</th><th>胜率</th><th>t(全)</th><th>t(样本内)</th><th>t(样本外)</th></tr>";
  DATA.factor_table.forEach((r) => {
    ft += `<tr><td>${r.label}</td><td>${num(r.ic, 4)}</td><td>${num(r.icir_y, 2)}</td><td>${num(r.ir, 2)}</td><td>${pct(r.ann)}</td><td>${pct(r.win)}</td><td>${tstar(r.t_full)}</td><td>${tstar(r.t_in)}</td><td>${tstar(r.t_out)}</td></tr>`;
  });
  document.getElementById("table-significance").innerHTML = ft;
})();
