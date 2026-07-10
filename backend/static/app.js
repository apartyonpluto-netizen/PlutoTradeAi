const pageId = document.body.dataset.page;
const rootTicker = (document.body.dataset.ticker || "SPY").toUpperCase();

function fmtNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "Unavailable";
  }
  return Number(value).toFixed(digits);
}

function showToast(message, type = "success") {
  const host = document.getElementById("toastHost");
  if (!host) return;
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  host.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

async function apiGet(url) {
  const response = await fetch(url);
  const json = await response.json();
  return { ok: response.ok, json };
}

async function apiPost(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const json = await response.json();
  return { ok: response.ok, json };
}

function setStatus(elementId, message) {
  const el = document.getElementById(elementId);
  if (el) el.textContent = message;
}

function renderMetricGrid(targetId, pairs) {
  const target = document.getElementById(targetId);
  if (!target) return;

  target.innerHTML = "";
  pairs.forEach((item) => {
    const card = document.createElement("div");
    card.className = "metric";
    card.innerHTML = `<div class="label">${item.label}</div><div class="value">${item.value}</div>`;
    target.appendChild(card);
  });
}

function getFormList(form, name) {
  return [...form.querySelectorAll(`input[name='${name}']:checked`)].map((node) => node.value);
}

function renderSymbolPreview(target, payload) {
  if (!target) return;
  target.innerHTML = `
    <div class="detail-item"><strong>Company Name</strong>: ${payload.company_name || "Unavailable"}</div>
    <div class="detail-item"><strong>Current Price</strong>: ${payload.current_price !== null && payload.current_price !== undefined ? `$${fmtNumber(payload.current_price)}` : "Unavailable"}</div>
    <div class="detail-item"><strong>Asset Type</strong>: ${payload.asset_type || "Unavailable"}</div>
    <div class="detail-item"><strong>Market Status</strong>: ${payload.market_status || "Unavailable"}</div>
    <div class="detail-item"><strong>Data Source</strong>: ${payload.data_source || "Unavailable"}</div>
    <div class="detail-item"><strong>Last Updated</strong>: ${payload.last_updated || "Unavailable"}</div>
  `;
}

function renderTimeline(targetId, items) {
  const target = document.getElementById(targetId);
  if (!target) return;

  target.innerHTML = "";
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "timeline-item";
    const value = typeof item.value === "object" ? JSON.stringify(item.value) : item.value;
    row.innerHTML = `<strong>${item.label}</strong><div>${value === null || value === undefined ? "Unavailable" : value}</div><div class="meta">${item.timestamp || "Unavailable"}</div>`;
    target.appendChild(row);
  });
}

function initMissionPanel() {
  const panel = document.getElementById("missionPanel");
  const openBtn = document.getElementById("openAssignMissionPanel");
  const openFromCenter = document.getElementById("openMissionFromCenter");
  const cancelBtn = document.getElementById("cancelMissionPanel");
  const validateBtn = document.getElementById("validateMissionSymbolButton");
  const analyzeBtn = document.getElementById("analyzeMissionButton");
  const form = document.getElementById("missionForm");
  const preview = document.getElementById("missionSymbolPreview");

  if (!panel || !openBtn || !cancelBtn || !form || !validateBtn || !analyzeBtn) return;

  const open = () => panel.classList.add("is-open");
  const close = () => panel.classList.remove("is-open");
  openBtn.addEventListener("click", open);
  if (openFromCenter) {
    openFromCenter.addEventListener("click", open);
  }
  cancelBtn.addEventListener("click", close);

  validateBtn.addEventListener("click", async () => {
    const formData = new FormData(form);
    const ticker = (formData.get("ticker_symbol") || "").toString().trim().toUpperCase();

    if (!ticker) {
      showToast("Ticker symbol is required", "error");
      return;
    }

    const { ok, json } = await apiGet(`/api/validate-symbol/${encodeURIComponent(ticker)}`);
    if (!ok || !json.success) {
      renderSymbolPreview(preview, {});
      showToast(json.error || "Symbol validation failed", "error");
      return;
    }

    renderSymbolPreview(preview, json.data);
    showToast(`${ticker} validated (${json.data_status})`, "success");
  });

  analyzeBtn.addEventListener("click", () => {
    const formData = new FormData(form);
    const ticker = (formData.get("ticker_symbol") || "").toString().trim().toUpperCase();
    if (!ticker) {
      showToast("Ticker symbol is required", "error");
      return;
    }
    window.location.href = `/stock/${encodeURIComponent(ticker)}`;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();

    const formData = new FormData(form);
    const payload = {
      ticker_symbol: (formData.get("ticker_symbol") || "").toString().trim().toUpperCase(),
      quick_action: formData.get("quick_action"),
      asset_type: formData.get("asset_type"),
      mission_type: formData.get("mission_type"),
      priority: formData.get("priority"),
      notes: formData.get("notes") || "",
      assigned_scanners: getFormList(form, "assigned_scanners"),
      assigned_brains: getFormList(form, "assigned_brains"),
      monitoring_flags: getFormList(form, "monitoring_flags"),
    };

    if (!payload.ticker_symbol) {
      showToast("Ticker symbol is required", "error");
      return;
    }

    const { ok, json } = await apiPost("/api/missions/assign", payload);
    if (!ok || !json.success) {
      showToast(json.error || "Mission assignment failed", "error");
      return;
    }

    showToast(json.data.message || "Mission assigned", "success");
    close();
    await refreshMissionSummaryWidgets();
    if (pageId === "missions") {
      await initMissionsPage();
    }
  });
}

function createChart(targetId) {
  const host = document.getElementById(targetId);
  if (!host || !window.LightweightCharts) return null;

  const chart = LightweightCharts.createChart(host, {
    layout: { background: { color: "#0b111f" }, textColor: "#c9d2e7" },
    grid: { vertLines: { color: "#1a273d" }, horzLines: { color: "#1a273d" } },
    width: host.clientWidth,
    height: 360,
    rightPriceScale: { borderColor: "#2e3c57" },
    timeScale: { borderColor: "#2e3c57" },
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: "#23d18b",
    downColor: "#e34f4f",
    borderVisible: false,
    wickUpColor: "#23d18b",
    wickDownColor: "#e34f4f",
  });

  const volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "",
    color: "#4f81ff",
    scaleMargins: { top: 0.8, bottom: 0 },
  });

  const resize = () => {
    chart.applyOptions({ width: host.clientWidth });
  };

  window.addEventListener("resize", resize);

  return {
    chart,
    candleSeries,
    volumeSeries,
    overlayLines: [],
    cleanup: () => window.removeEventListener("resize", resize),
  };
}

function clearOverlayLines(ctx) {
  if (!ctx) return;
  ctx.overlayLines.forEach((item) => {
    if (item.kind === "price" && item.series && item.line) {
      item.series.removePriceLine(item.line);
      return;
    }
    if (item.kind === "series" && item.series) {
      ctx.chart.removeSeries(item.series);
    }
  });
  ctx.overlayLines = [];
}

function addPriceLine(series, value, title, color) {
  if (value === null || value === undefined) return null;
  const line = series.createPriceLine({
    price: Number(value),
    color,
    lineStyle: 2,
    lineWidth: 1,
    axisLabelVisible: true,
    title,
  });
  return { kind: "price", line, series };
}

function addLineSeries(chart, lineData, color) {
  if (!Array.isArray(lineData) || !lineData.length) return null;
  const series = chart.addLineSeries({ color, lineWidth: 1 });
  series.setData(lineData);
  return { kind: "series", series };
}

function getSelectedOverlays() {
  const boxes = [...document.querySelectorAll("input[data-overlay]")];
  const set = new Set();
  boxes.forEach((box) => {
    if (box.checked) set.add(box.dataset.overlay);
  });
  return set;
}

function renderObjectDetails(targetId, payload) {
  const target = document.getElementById(targetId);
  if (!target) return;

  target.innerHTML = "";
  Object.entries(payload).forEach(([key, value]) => {
    if (value && typeof value === "object") return;
    const row = document.createElement("div");
    row.className = "detail-item";
    row.innerHTML = `<strong>${key.replaceAll("_", " ")}</strong>: ${value === null || value === undefined ? "Unavailable" : value}`;
    target.appendChild(row);
  });
}

async function loadSnapshot(ticker, statusId, metricId) {
  setStatus(statusId, "Loading market data...");
  const { ok, json } = await apiGet(`/api/stock/${encodeURIComponent(ticker)}`);
  if (!ok || !json.success) {
    setStatus(statusId, json.error || "Ticker unavailable");
    renderMetricGrid(metricId, []);
    return null;
  }

  const d = json.data;
  const metrics = [
    { label: "Company/Ticker", value: `${d.company} (${d.ticker})` },
    { label: "Asset Type", value: d.asset_type || "Unavailable" },
    { label: "Current Price", value: `$${fmtNumber(d.current_price)}` },
    { label: "Daily Change", value: `${fmtNumber(d.daily_change)} (${fmtNumber(d.daily_change_percent)}%)` },
    { label: "Session Status", value: d.session_status || "Unavailable" },
    { label: "Market Session", value: d.market_session || "Unavailable" },
    { label: "Premarket", value: d.premarket_price ? `$${fmtNumber(d.premarket_price)}` : "Unavailable" },
    { label: "After Hours", value: d.after_hours_price ? `$${fmtNumber(d.after_hours_price)}` : "Unavailable" },
    { label: "Volume", value: d.volume ?? "Unavailable" },
    { label: "Relative Volume", value: d.relative_volume ? `${fmtNumber(d.relative_volume)}x` : "Unavailable" },
    { label: "Market Cap", value: d.market_cap ?? "Unavailable" },
    { label: "Previous Close", value: `$${fmtNumber(d.previous_close)}` },
    { label: "Day High/Low", value: `$${fmtNumber(d.day_high)} / $${fmtNumber(d.day_low)}` },
    { label: "52-Week High/Low", value: `$${fmtNumber(d.week_52_high)} / $${fmtNumber(d.week_52_low)}` },
    { label: "Data Source", value: d.data_source || "Unavailable" },
    { label: "Last Updated", value: d.last_updated || "Unavailable" },
    { label: "Status", value: d.market_data_status || json.data_status },
  ];

  renderMetricGrid(metricId, metrics);
  setStatus(statusId, `Provider: ${json.provider} | Status: ${json.data_status} | Timestamp: ${json.timestamp}`);
  return json;
}

async function loadChart(ticker, timeframe, chartCtx, statusId) {
  setStatus(statusId, "Loading chart...");
  const { ok, json } = await apiGet(`/api/chart/${encodeURIComponent(ticker)}?timeframe=${encodeURIComponent(timeframe)}`);
  if (!ok || !json.success) {
    setStatus(statusId, json.error || "Chart unavailable");
    chartCtx.candleSeries.setData([]);
    chartCtx.volumeSeries.setData([]);
    clearOverlayLines(chartCtx);
    return null;
  }

  const data = json.data;
  chartCtx.candleSeries.setData(data.candles);
  chartCtx.volumeSeries.setData(data.volume);

  clearOverlayLines(chartCtx);
  const overlays = getSelectedOverlays();
  const indicators = data.indicators || {};

  if (overlays.has("major_support")) {
    const line = addPriceLine(chartCtx.candleSeries, indicators.major_support, "Support", "#00d4ff");
    if (line) chartCtx.overlayLines.push(line);
  }

  if (overlays.has("major_resistance")) {
    const line = addPriceLine(chartCtx.candleSeries, indicators.major_resistance, "Resistance", "#ffb347");
    if (line) chartCtx.overlayLines.push(line);
  }

  if (overlays.has("breakout_level")) {
    const line = addPriceLine(chartCtx.candleSeries, indicators.breakout_level, "Breakout", "#f18f01");
    if (line) chartCtx.overlayLines.push(line);
  }

  if (overlays.has("breakdown_level")) {
    const line = addPriceLine(chartCtx.candleSeries, indicators.breakdown_level, "Breakdown", "#f35b66");
    if (line) chartCtx.overlayLines.push(line);
  }

  if (overlays.has("reversal_zone")) {
    const lowLine = addPriceLine(chartCtx.candleSeries, indicators.reversal_zone_low, "Reversal Low", "#a3e635");
    const highLine = addPriceLine(chartCtx.candleSeries, indicators.reversal_zone_high, "Reversal High", "#84cc16");
    if (lowLine) chartCtx.overlayLines.push(lowLine);
    if (highLine) chartCtx.overlayLines.push(highLine);
  }

  if (overlays.has("premarket")) {
    const preHigh = addPriceLine(chartCtx.candleSeries, indicators.premarket_high, "Pre High", "#c084fc");
    const preLow = addPriceLine(chartCtx.candleSeries, indicators.premarket_low, "Pre Low", "#a855f7");
    if (preHigh) chartCtx.overlayLines.push(preHigh);
    if (preLow) chartCtx.overlayLines.push(preLow);
  }

  const emaColorMap = {
    ema9: "#f59e0b",
    ema20: "#38bdf8",
    ema50: "#4ade80",
    ema200: "#a78bfa",
    vwap: "#f43f5e",
  };

  ["ema9", "ema20", "ema50", "ema200", "vwap"].forEach((name) => {
    if (!overlays.has(name)) return;
    const series = addLineSeries(chartCtx.chart, indicators[`${name}_series`], emaColorMap[name]);
    if (series) chartCtx.overlayLines.push(series);
  });

  chartCtx.chart.timeScale().fitContent();
  setStatus(statusId, `${data.ticker} | ${data.timeframe} | ${json.data_status} | Last updated ${data.last_updated}`);
  return json;
}

function wireTimeframeButtons(onClick) {
  const buttons = [...document.querySelectorAll(".time-btn")];
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      buttons.forEach((item) => item.classList.remove("is-active"));
      btn.classList.add("is-active");
      onClick(btn.dataset.timeframe);
    });
  });
}

async function refreshMissionSummaryWidgets() {
  const status = document.getElementById("missionControlStatus");
  const summary = document.getElementById("missionControlSummary");
  const dash = document.getElementById("missionDashboardSummary");
  if (!status && !summary && !dash) return;

  const { ok, json } = await apiGet("/api/missions/summary");
  if (!ok || !json.success) {
    if (status) status.textContent = json.error || "Mission summary unavailable";
    return;
  }

  const missionControl = json.data.mission_control;
  const dashboard = json.data.dashboard;

  if (status) {
    status.textContent = `Active missions: ${missionControl.active_missions} | High priority: ${missionControl.high_priority_missions} | Updated: ${missionControl.last_updated}`;
  }

  if (summary) {
    summary.innerHTML = "";
    [
      ["Active Missions", missionControl.active_missions],
      ["Symbols Being Monitored", missionControl.symbols_being_monitored],
      ["High Priority Missions", missionControl.high_priority_missions],
      ["Recently Changed Missions", (missionControl.recently_changed_missions || []).length],
      ["AI Confidence Changes", (missionControl.ai_confidence_changes || []).length],
      ["Risk Changes", (missionControl.risk_changes || []).length],
      ["Upcoming Opportunities", (missionControl.upcoming_opportunities || []).length],
    ].forEach(([label, value]) => {
      const row = document.createElement("div");
      row.className = "detail-item";
      row.innerHTML = `<strong>${label}</strong>: ${value}`;
      summary.appendChild(row);
    });
  }

  if (dash) {
    dash.innerHTML = "";
    [
      ["Overnight Monitored", dashboard.overnight_monitored],
      ["Require Attention", dashboard.require_attention],
      ["Setups Strengthened", dashboard.setups_strengthened],
      ["Setups Invalidated", dashboard.setups_invalidated],
      ["Highest Priority Mission", dashboard.highest_priority_mission?.ticker],
      ["Highest Priority Confidence", `${dashboard.highest_priority_mission?.confidence || 0}%`],
      ["Recommended", dashboard.highest_priority_mission?.recommended],
    ].forEach(([label, value]) => {
      const row = document.createElement("div");
      row.className = "detail-item";
      row.innerHTML = `<strong>${label}</strong>: ${value || "Unavailable"}`;
      dash.appendChild(row);
    });
  }
}

async function loadMissionTimeline(ticker, statusId, summaryId, trackId) {
  setStatus(statusId, "Loading mission timeline...");
  const { ok, json } = await apiGet(`/api/missions/${encodeURIComponent(ticker)}/timeline`);
  if (!ok || !json.success) {
    setStatus(statusId, json.error || "Mission timeline unavailable");
    renderTimeline(trackId, []);
    return null;
  }

  const data = json.data;
  setStatus(statusId, `Provider: ${json.provider || "Unavailable"} | Status: ${json.data_status || "unavailable"} | Timestamp: ${json.timestamp || "unavailable"}`);

  const summaryTarget = document.getElementById(summaryId);
  if (summaryTarget) {
    summaryTarget.innerHTML = "";
    [
      ["Ticker", data.ticker],
      ["Company", data.company],
      ["Priority", data.priority],
      ["Mission Type", data.mission_type],
      ["Latest Confidence", data.latest?.confidence],
      ["Latest Risk", data.latest?.risk],
      ["Latest Thesis", data.latest?.trade_thesis],
      ["Latest Support", data.latest?.support],
      ["Latest Resistance", data.latest?.resistance],
    ].forEach(([label, value]) => {
      const row = document.createElement("div");
      row.className = "detail-item";
      row.innerHTML = `<strong>${label}</strong>: ${value ?? "Unavailable"}`;
      summaryTarget.appendChild(row);
    });
  }

  renderTimeline(trackId, data.timeline || []);
  return data;
}

async function initDashboard() {
  const chartCtx = createChart("dashboardChart");
  if (!chartCtx) return;

  await loadSnapshot(rootTicker, "dashboardChartStatus", "dashboardSnapshot");
  await loadChart(rootTicker, "1M", chartCtx, "dashboardChartStatus");
  await refreshMissionSummaryWidgets();

  wireTimeframeButtons(async (timeframe) => {
    await loadChart(rootTicker, timeframe, chartCtx, "dashboardChartStatus");
  });
}

async function initStockWorkspace() {
  const ticker = rootTicker;
  const chartCtx = createChart("stockChart");
  if (!chartCtx) return;

  await loadSnapshot(ticker, "stockDataStatus", "stockMetrics");
  await loadChart(ticker, "1M", chartCtx, "stockChartStatus");

  wireTimeframeButtons(async (timeframe) => {
    await loadChart(ticker, timeframe, chartCtx, "stockChartStatus");
  });

  document.querySelectorAll("input[data-overlay]").forEach((box) => {
    box.addEventListener("change", async () => {
      const active = document.querySelector(".time-btn.is-active");
      const tf = active ? active.dataset.timeframe : "1M";
      await loadChart(ticker, tf, chartCtx, "stockChartStatus");
    });
  });

  document.querySelectorAll(".stock-action").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      if (action === "mission") {
        document.getElementById("openAssignMissionPanel")?.click();
        return;
      }

      if (action === "chart") {
        document.getElementById("stockChart")?.scrollIntoView({ behavior: "smooth" });
        return;
      }

      if (action === "analyze" || action === "thesis") {
        const { ok, json } = await apiGet(`/api/brains/${encodeURIComponent(ticker)}`);
        if (!ok || !json.success) {
          showToast(json.error || "AI thesis unavailable", "error");
          return;
        }
        renderObjectDetails("workspaceDetailBody", json.data);
        return;
      }

      if (action === "options") {
        const { ok, json } = await apiGet(`/api/brains/options/${encodeURIComponent(ticker)}`);
        if (!ok || !json.success) {
          showToast(json.error || "Options research unavailable", "error");
          return;
        }
        renderObjectDetails("workspaceDetailBody", json.data);
        return;
      }

      if (action === "levels") {
        const { ok, json } = await apiGet(`/api/brains/support-resistance/${encodeURIComponent(ticker)}`);
        if (!ok || !json.success) {
          showToast(json.error || "Key levels unavailable", "error");
          return;
        }
        renderObjectDetails("workspaceDetailBody", json.data);
      }
    });
  });

  await loadMissionTimeline(ticker, "stockMissionTimelineStatus", "stockMissionTimelineSummary", "stockMissionTimelineTrack");
}

async function initScannersPage() {
  const active = document.body.dataset.activeScanner || "overview";
  setStatus("scannersStatus", "Loading scanner output...");
  const path = active === "overview" ? "/api/scanners" : `/api/scanners/${encodeURIComponent(active)}`;
  const { ok, json } = await apiGet(path);

  if (!ok || !json.success) {
    setStatus("scannersStatus", json.error || "Scanner unavailable");
    return;
  }

  const data = json.data;
  setStatus("scannersStatus", `${data.scanner || "Scanner"} | Provider: ${json.provider || "Unavailable"} | Status: ${json.data_status || "unavailable"} | Last scan: ${data.last_scan_time || "unavailable"} | Timestamp: ${json.timestamp || "unavailable"}`);

  const summaryTarget = document.getElementById("scannersSummary");
  if (summaryTarget) {
    summaryTarget.innerHTML = "";
    [
      ["Top Opportunity", data.highest_confidence_setup?.ticker],
      ["Largest Mover", data.largest_mover?.ticker],
      ["Best Breakout", data.best_breakout_candidate?.ticker],
      ["Best Reversal", data.best_reversal_candidate?.ticker],
      ["Highest Volume", data.strongest_relative_volume?.ticker],
      ["Highest Confidence", data.highest_confidence_setup?.scanner_score],
      ["Data Health", data.data_health],
      ["Last Scan", data.last_scan_time],
    ].forEach(([label, value]) => {
      if (!value) return;
      const row = document.createElement("div");
      row.className = "detail-item";
      row.innerHTML = `<strong>${label}</strong>: ${value}`;
      summaryTarget.appendChild(row);
    });
  }

  const rows = data.rows || [];
  const body = document.querySelector("#scannersTable tbody");
  if (!body) return;
  body.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.ticker || "-"}</td>
      <td>${row.price ?? "-"}</td>
      <td>${row.percent_change ?? "-"}</td>
      <td>${row.relative_volume ?? "-"}</td>
      <td>${row.scanner_score ?? "-"}</td>
    `;
    body.appendChild(tr);
  });
}

async function initBrainsPage() {
  const active = document.body.dataset.activeBrain || "overview";
  const ticker = rootTicker;
  setStatus("brainsStatus", "Loading AI brain output...");

  const path = active === "overview"
    ? `/api/brains/${encodeURIComponent(ticker)}`
    : `/api/brains/${encodeURIComponent(active)}/${encodeURIComponent(ticker)}`;

  const { ok, json } = await apiGet(path);
  if (!ok || !json.success) {
    setStatus("brainsStatus", json.error || "Brain unavailable");
    return;
  }

  setStatus("brainsStatus", `Ticker ${ticker} | Provider: ${json.provider || "Unavailable"} | Status: ${json.data_status || "unavailable"} | Data quality: ${json.data.data_quality || "unavailable"} | Timestamp: ${json.timestamp || "unavailable"}`);
  renderObjectDetails("brainsBody", json.data);
}

async function initFuturesPage() {
  setStatus("futuresStatus", "Loading futures command...");
  const { ok, json } = await apiGet("/api/futures");
  if (!ok || !json.success) {
    setStatus("futuresStatus", json.error || "Futures command unavailable");
    return;
  }

  const data = json.data;
  setStatus("futuresStatus", `Provider: ${json.provider || "Unavailable"} | Status: ${json.data_status || "unavailable"} | Timestamp: ${json.timestamp || "unavailable"}`);

  const summary = document.getElementById("futuresSummary");
  if (summary) {
    summary.innerHTML = "";
    [
      ["Top Opportunity", data.top_opportunity?.ticker],
      ["Last Updated", data.last_updated],
    ].forEach(([label, value]) => {
      const row = document.createElement("div");
      row.className = "detail-item";
      row.innerHTML = `<strong>${label}</strong>: ${value || "Unavailable"}`;
      summary.appendChild(row);
    });
  }

  const body = document.querySelector("#futuresTable tbody");
  if (!body) return;

  body.innerHTML = "";
  (data.rows || []).forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.ticker || "-"}</td>
      <td>${row.current_price ?? "Data Unavailable"}</td>
      <td>${row.trend || "Unavailable"}</td>
      <td>${row.support ?? "Unavailable"}</td>
      <td>${row.resistance ?? "Unavailable"}</td>
      <td>${row.suggested_direction || "WAIT"}</td>
      <td>${row.risk || "Unavailable"}</td>
      <td>${row.confidence ?? "Unavailable"}</td>
    `;
    body.appendChild(tr);
  });
}

async function initMissionsPage() {
  await refreshMissionSummaryWidgets();

  const { ok, json } = await apiGet("/api/missions");
  if (!ok || !json.success) {
    setStatus("missionControlStatus", json.error || "Mission profiles unavailable");
    return;
  }

  const rows = [...(json.data.persistent || []), ...(json.data.session_only || [])];
  const body = document.querySelector("#missionsTable tbody");
  if (!body) return;

  body.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.ticker || "-"}</td>
      <td>${row.company || "-"}</td>
      <td>${row.priority || "-"}</td>
      <td>${row.mission_type || "-"}</td>
      <td>${row.last_ai_update || "-"}</td>
      <td>${row.last_market_update || "-"}</td>
    `;
    body.appendChild(tr);
  });

  const timelineForm = document.getElementById("missionTimelineForm");
  if (timelineForm) {
    timelineForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const ticker = (document.getElementById("missionTimelineTicker")?.value || rootTicker).toUpperCase();
      await loadMissionTimeline(ticker, "missionTimelineStatus", "missionTimelineSummary", "missionTimelineTrack");
    });
  }

  await loadMissionTimeline(rootTicker, "missionTimelineStatus", "missionTimelineSummary", "missionTimelineTrack");
}

function init() {
  initMissionPanel();

  if (pageId === "dashboard") {
    initDashboard();
  }

  if (pageId === "stock-workspace") {
    initStockWorkspace();
  }

  if (pageId === "scanners") {
    initScannersPage();
  }

  if (pageId === "brains") {
    initBrainsPage();
  }

  if (pageId === "missions") {
    initMissionsPage();
  }

  if (pageId === "futures") {
    initFuturesPage();
  }
}

init();
