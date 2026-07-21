const pageId = document.body.dataset.page;
const rootTicker = (document.body.dataset.ticker || "SPY").toUpperCase();

const POLL_INTERVALS = {
  dashboard: 20000,
  "stock-workspace": 10000,
  scanners: 45000,
  brains: 30000,
  missions: 30000,
  futures: 45000,
};

const state = {
  mode: localStorage.getItem("pluto:realtime-mode") || "polling",
  status: "offline",
  latencies: [],
  pollHandle: null,
  socket: null,
  socketBound: false,
  streamStatus: "UNAVAILABLE",
  marketWatch: new Set(),
};

const STATUS_LABELS = {
  LIVE: "LIVE",
  RECONNECTING: "RECONNECTING",
  POLLING: "POLLING",
  CACHED: "CACHED",
  STALE: "STALE",
  UNAVAILABLE: "UNAVAILABLE",
};

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

function setConnectionStatus(status, latencyMs = null) {
  state.status = status;

  if (typeof latencyMs === "number") {
    state.latencies.push(latencyMs);
    if (state.latencies.length > 20) {
      state.latencies.shift();
    }
  }

  const avgLatency = state.latencies.length
    ? Math.round(state.latencies.reduce((acc, val) => acc + val, 0) / state.latencies.length)
    : null;

  let quality = "--";
  if (avgLatency !== null) {
    if (avgLatency < 250) quality = "Excellent";
    else if (avgLatency < 600) quality = "Good";
    else if (avgLatency < 1200) quality = "Fair";
    else quality = "Poor";
  }

  const dot = document.getElementById("connectionDot");
  const label = document.getElementById("connectionLabel");
  const qualityEl = document.getElementById("connectionQuality");

  if (dot) {
    dot.classList.remove("connected", "reconnecting", "offline");
    dot.classList.add(status);
  }

  if (label) {
    const base = status === "connected" ? "Connected" : status === "reconnecting" ? "Reconnecting" : "Offline";
    const stream = STATUS_LABELS[state.streamStatus] || STATUS_LABELS.UNAVAILABLE;
    label.textContent = `${base} | ${stream}`;
  }

  if (qualityEl) {
    qualityEl.textContent = `Quality: ${quality}${avgLatency !== null ? ` (${avgLatency}ms)` : ""}`;
  }
}

function mapDataStatusToStreamStatus(status) {
  const normalized = (status || "").toString().trim().toUpperCase();
  if (normalized === "LIVE") return "LIVE";
  if (normalized === "STREAMING") return "LIVE";
  if (normalized === "RECONNECTING") return "RECONNECTING";
  if (normalized === "POLLING") return "POLLING";
  if (normalized === "CACHED") return "CACHED";
  if (normalized === "STALE") return "STALE";
  return "UNAVAILABLE";
}

function mapSocketStateToStreamStatus(stateValue) {
  const normalized = (stateValue || "").toString().trim().toLowerCase();
  if (normalized === "live") return "LIVE";
  if (normalized === "polling") return "POLLING";
  if (normalized === "reconnecting") return "RECONNECTING";
  return "UNAVAILABLE";
}

function updateStreamStatus(status) {
  state.streamStatus = mapDataStatusToStreamStatus(status);
  setConnectionStatus(state.status);
}

async function apiGet(url) {
  const start = performance.now();
  try {
    const response = await fetch(url);
    const json = await response.json();
    setConnectionStatus("connected", performance.now() - start);
    updateStreamStatus(json.data_status);
    return { ok: response.ok, json };
  } catch (_error) {
    setConnectionStatus("reconnecting");
    updateStreamStatus("UNAVAILABLE");
    return {
      ok: false,
      json: {
        success: false,
        error: "Data unavailable",
        data: {},
        timestamp: new Date().toISOString(),
        data_status: "unavailable",
        provider: "Unavailable",
      },
    };
  }
}

async function apiPost(url, payload) {
  const start = performance.now();
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const json = await response.json();
    setConnectionStatus("connected", performance.now() - start);
    return { ok: response.ok, json };
  } catch (_error) {
    setConnectionStatus("reconnecting");
    return {
      ok: false,
      json: {
        success: false,
        error: "Data unavailable",
        data: {},
        timestamp: new Date().toISOString(),
        data_status: "unavailable",
        provider: "Unavailable",
      },
    };
  }
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
    const reason = item.reason ? `<div>${item.reason}</div>` : "";
    const confidence = (item.previous_confidence !== undefined || item.current_confidence !== undefined)
      ? `<div>Confidence: ${item.previous_confidence ?? "-"} → ${item.current_confidence ?? "-"}</div>`
      : "";

    row.innerHTML = `<strong>${item.label}</strong><div>${value === null || value === undefined ? "Unavailable" : value}</div>${reason}${confidence}<div class="meta">${item.timestamp || "Unavailable"} | ${item.source || "AI"} | ${item.provider || "PlutoTradeAI"}</div>`;
    target.appendChild(row);
  });
}

function getStoredList(key) {
  try {
    return JSON.parse(localStorage.getItem(key) || "[]");
  } catch (_error) {
    return [];
  }
}

function setStoredList(key, value) {
  localStorage.setItem(key, JSON.stringify(value.slice(0, 12)));
}

function rememberTicker(ticker) {
  const clean = (ticker || "").toUpperCase();
  if (!clean) return;
  const recents = getStoredList("pluto:recent-tickers").filter((item) => item !== clean);
  recents.unshift(clean);
  setStoredList("pluto:recent-tickers", recents);
}

function addFavoriteTicker(ticker) {
  const clean = (ticker || "").toUpperCase();
  if (!clean) return;
  const favorites = getStoredList("pluto:favorite-tickers");
  if (!favorites.includes(clean)) {
    favorites.unshift(clean);
    setStoredList("pluto:favorite-tickers", favorites);
  }
}

function renderSearchMemory() {
  const host = document.getElementById("workspaceSearchMemory");
  if (!host) return;

  host.innerHTML = "";

  const rows = [
    ["Recent Searches", getStoredList("pluto:recent-tickers").slice(0, 8)],
    ["Favorites", getStoredList("pluto:favorite-tickers").slice(0, 8)],
  ];

  rows.forEach(([title, values]) => {
    if (!values.length) return;

    const titleRow = document.createElement("div");
    titleRow.className = "detail-item";
    titleRow.innerHTML = `<strong>${title}</strong>`;
    host.appendChild(titleRow);

    const chipRow = document.createElement("div");
    chipRow.className = "memory-chip-row";

    values.forEach((ticker) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "memory-chip";
      chip.textContent = ticker;
      chip.addEventListener("click", () => {
        window.location.href = `/stock/${encodeURIComponent(ticker)}`;
      });
      chipRow.appendChild(chip);
    });

    host.appendChild(chipRow);
  });
}

async function loadTickerSuggestions(seed = "") {
  const list = document.getElementById("tickerSuggestions");
  if (!list) return;

  const { ok, json } = await apiGet(`/api/tickers/suggest?q=${encodeURIComponent(seed)}`);
  const serverItems = ok && json.success ? (json.data.items || []) : [];
  const merged = [...new Set([...getStoredList("pluto:favorite-tickers"), ...getStoredList("pluto:recent-tickers"), ...serverItems])].slice(0, 24);

  list.innerHTML = "";
  merged.forEach((ticker) => {
    const option = document.createElement("option");
    option.value = ticker;
    list.appendChild(option);
  });
}

function setupTopTickerMemory() {
  const topForm = document.getElementById("topTickerForm");
  const topInput = document.getElementById("tickerInput");

  if (topForm && topInput) {
    topForm.addEventListener("submit", () => rememberTicker(topInput.value));
    topInput.addEventListener("input", () => loadTickerSuggestions(topInput.value.toUpperCase()));
  }
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
    const ticker = (new FormData(form).get("ticker_symbol") || "").toString().trim().toUpperCase();
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
    const ticker = (new FormData(form).get("ticker_symbol") || "").toString().trim().toUpperCase();
    if (!ticker) {
      showToast("Ticker symbol is required", "error");
      return;
    }
    window.location.href = `/stock/${encodeURIComponent(ticker)}`;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const payload = {
      ticker_symbol: (data.get("ticker_symbol") || "").toString().trim().toUpperCase(),
      quick_action: data.get("quick_action"),
      asset_type: data.get("asset_type"),
      mission_type: data.get("mission_type"),
      priority: data.get("priority"),
      notes: data.get("notes") || "",
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

    rememberTicker(payload.ticker_symbol);
    showToast(json.data.message || "Mission assigned", "success");
    close();
    await refreshMissionSummaryWidgets();
    if (pageId === "missions") {
      await initMissionsPage(false);
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

  const resize = () => chart.applyOptions({ width: host.clientWidth });
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
    setStatus(statusId, json.error || "Data unavailable");
    renderMetricGrid(metricId, []);
    return null;
  }

  const d = json.data;
  const metrics = [
    { label: "Company/Ticker", value: `${d.company} (${d.ticker})` },
    { label: "Asset Type", value: d.asset_type || "Unavailable" },
    { label: "Current Price", value: `$${fmtNumber(d.current_price)}` },
    { label: "Daily Change", value: `${fmtNumber(d.daily_change)} (${fmtNumber(d.daily_change_percent)}%)` },
    { label: "Session", value: d.market_session || "Unavailable" },
    { label: "Volume", value: d.volume ?? "Unavailable" },
    { label: "Relative Volume", value: d.relative_volume ? `${fmtNumber(d.relative_volume)}x` : "Unavailable" },
    { label: "Data Provider", value: d.data_source || json.provider || "Unavailable" },
    { label: "Status", value: d.market_data_status || json.data_status || "Unavailable" },
    { label: "Age", value: d.market_data_age_seconds !== null && d.market_data_age_seconds !== undefined ? `${d.market_data_age_seconds}s` : "Unavailable" },
    { label: "Last Updated", value: d.last_updated || "Unavailable" },
  ];

  renderMetricGrid(metricId, metrics);
  setStatus(statusId, `Provider: ${json.provider} | Status: ${d.market_data_status || json.data_status} | Session: ${d.market_session || "unavailable"} | Age: ${d.market_data_age_seconds ?? "--"}s | Timestamp: ${json.timestamp}`);
  return json;
}

async function loadChart(ticker, timeframe, chartCtx, statusId) {
  setStatus(statusId, "Loading chart...");
  const { ok, json } = await apiGet(`/api/chart/${encodeURIComponent(ticker)}?timeframe=${encodeURIComponent(timeframe)}`);
  if (!ok || !json.success) {
    setStatus(statusId, json.error || "Data unavailable");
    chartCtx.candleSeries.setData([]);
    chartCtx.volumeSeries.setData([]);
    clearOverlayLines(chartCtx);
    return null;
  }

  const data = json.data;
  const indicators = data.indicators || {};

  chartCtx.candleSeries.setData(data.candles || []);
  chartCtx.volumeSeries.setData(data.volume || []);

  clearOverlayLines(chartCtx);
  const overlays = getSelectedOverlays();

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
  if (overlays.has("afterhours")) {
    const ahHigh = addPriceLine(chartCtx.candleSeries, indicators.after_hours_high, "AH High", "#d946ef");
    const ahLow = addPriceLine(chartCtx.candleSeries, indicators.after_hours_low, "AH Low", "#c026d3");
    if (ahHigh) chartCtx.overlayLines.push(ahHigh);
    if (ahLow) chartCtx.overlayLines.push(ahLow);
  }

  const colorMap = {
    ema9: "#f59e0b",
    ema20: "#38bdf8",
    ema50: "#4ade80",
    ema200: "#a78bfa",
    vwap: "#f43f5e",
  };

  ["ema9", "ema20", "ema50", "ema200", "vwap"].forEach((name) => {
    if (!overlays.has(name)) return;
    const series = addLineSeries(chartCtx.chart, indicators[`${name}_series`], colorMap[name]);
    if (series) chartCtx.overlayLines.push(series);
  });

  chartCtx.chart.timeScale().fitContent();
  setStatus(statusId, `${data.ticker} | ${data.timeframe} | ${data.market_data_status || json.data_status} | Session: ${data.market_session || "unavailable"} | Age: ${data.market_data_age_seconds ?? "--"}s | Last updated ${data.last_updated}`);
  return json;
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

function startPolling(task, interval) {
  if (state.mode !== "polling" && state.mode !== "streaming") {
    return;
  }

  if (state.pollHandle) {
    clearInterval(state.pollHandle);
  }

  state.pollHandle = setInterval(task, interval);
}

async function syncMarketStatus() {
  const { ok, json } = await apiGet("/api/market/status");
  if (!ok || !json.success) {
    updateStreamStatus("UNAVAILABLE");
    return null;
  }
  const marketState = json.data?.state || json.data_status;
  const streamStatus = mapSocketStateToStreamStatus(marketState);
  updateStreamStatus(streamStatus);
  return json;
}

function updateSnapshotMetricStatus(statusId, metricId, quote) {
  const metricsHost = document.getElementById(metricId);
  if (!metricsHost || !quote) return;

  const metricRows = [...metricsHost.querySelectorAll(".metric")];
  metricRows.forEach((row) => {
    const label = row.querySelector(".label")?.textContent || "";
    const value = row.querySelector(".value");
    if (!value) return;
    if (label === "Current Price" && quote.price !== null && quote.price !== undefined) {
      value.textContent = `$${fmtNumber(quote.price)}`;
    }
    if (label === "Status") {
      value.textContent = quote.cache_status || quote.source_status || "UNAVAILABLE";
    }
    if (label === "Age") {
      value.textContent = quote.age_seconds !== null && quote.age_seconds !== undefined ? `${quote.age_seconds}s` : "Unavailable";
    }
    if (label === "Last Updated") {
      value.textContent = quote.timestamp || "Unavailable";
    }
    if (label === "Data Provider") {
      value.textContent = quote.provider || "Unavailable";
    }
  });

  const statusEl = document.getElementById(statusId);
  if (statusEl) {
    statusEl.textContent = `Provider: ${quote.provider || "Unavailable"} | Status: ${quote.cache_status || quote.source_status || "UNAVAILABLE"} | Age: ${quote.age_seconds ?? "--"}s | Timestamp: ${quote.timestamp || "unavailable"}`;
  }
}

function handleSocketMarketUpdate(payload) {
  const quote = payload?.quote;
  if (!quote || !quote.symbol) return;
  updateStreamStatus(quote.cache_status || quote.source_status || "UNAVAILABLE");

  if (pageId === "dashboard" && quote.symbol === rootTicker) {
    updateSnapshotMetricStatus("dashboardChartStatus", "dashboardSnapshot", quote);
  }

  if (pageId === "stock-workspace" && quote.symbol === rootTicker) {
    updateSnapshotMetricStatus("stockDataStatus", "stockMetrics", quote);
  }
}

function ensureSocketConnection() {
  if (state.mode !== "streaming") {
    return;
  }
  if (typeof io === "undefined") {
    showToast("Socket.IO client unavailable, polling fallback active.", "error");
    updateStreamStatus("POLLING");
    return;
  }
  if (!state.socket) {
    state.socket = io({ transports: ["websocket", "polling"], reconnection: true, reconnectionAttempts: Infinity });
  }
  if (state.socketBound) {
    return;
  }

  state.socketBound = true;
  state.socket.on("connect", async () => {
    setConnectionStatus("connected");
    const status = await syncMarketStatus();
    const streamState = mapSocketStateToStreamStatus(status?.data?.state);
    updateStreamStatus(streamState);
  });

  state.socket.on("disconnect", () => {
    setConnectionStatus("reconnecting");
    updateStreamStatus("RECONNECTING");
  });

  state.socket.on("connection_status", (payload) => {
    const streamState = mapSocketStateToStreamStatus(payload?.state);
    updateStreamStatus(streamState);
  });

  state.socket.on("fallback_activated", () => {
    updateStreamStatus("POLLING");
    showToast("Live stream fallback activated: polling mode", "error");
  });

  state.socket.on("stream_recovered", () => {
    updateStreamStatus("LIVE");
    showToast("Live stream recovered", "success");
  });

  state.socket.on("market_data_error", (payload) => {
    updateStreamStatus("UNAVAILABLE");
    showToast(payload?.error || "Market data error", "error");
  });

  state.socket.on("market_update", handleSocketMarketUpdate);
}

function setupRealtimeMode() {
  const modeSelect = document.getElementById("realtimeMode");
  if (!modeSelect) return;

  modeSelect.value = state.mode;
  modeSelect.addEventListener("change", () => {
    state.mode = modeSelect.value;
    localStorage.setItem("pluto:realtime-mode", state.mode);

    if (state.pollHandle) {
      clearInterval(state.pollHandle);
      state.pollHandle = null;
    }

    if (state.mode === "streaming") {
      setConnectionStatus("reconnecting");
      updateStreamStatus("RECONNECTING");
      ensureSocketConnection();
      showToast("Streaming mode enabled. Polling fallback remains active when needed.", "success");
      return;
    }

    updateStreamStatus("POLLING");
    showToast("Polling mode enabled", "success");
  });
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

  startPolling(async () => {
    await loadSnapshot(rootTicker, "dashboardChartStatus", "dashboardSnapshot");
    await refreshMissionSummaryWidgets();
  }, POLL_INTERVALS.dashboard);
}

async function initStockWorkspace() {
  const ticker = rootTicker;
  const chartCtx = createChart("stockChart");
  if (!chartCtx) return;

  await loadSnapshot(ticker, "stockDataStatus", "stockMetrics");
  await loadChart(ticker, "1M", chartCtx, "stockChartStatus");
  await loadMissionTimeline(ticker, "stockMissionTimelineStatus", "stockMissionTimelineSummary", "stockMissionTimelineTrack");

  const workspaceForm = document.getElementById("workspaceTickerForm");
  const workspaceInput = document.getElementById("workspaceTickerInput");

  if (workspaceForm && workspaceInput) {
    workspaceForm.addEventListener("submit", () => rememberTicker(workspaceInput.value));
    workspaceInput.addEventListener("input", () => loadTickerSuggestions(workspaceInput.value.toUpperCase()));
  }

  renderSearchMemory();

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

      if (action === "favorite") {
        addFavoriteTicker(ticker);
        renderSearchMemory();
        await loadTickerSuggestions(ticker);
        showToast(`${ticker} added to favorites`, "success");
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

  startPolling(async () => {
    await loadSnapshot(ticker, "stockDataStatus", "stockMetrics");
    await loadMissionTimeline(ticker, "stockMissionTimelineStatus", "stockMissionTimelineSummary", "stockMissionTimelineTrack");
  }, POLL_INTERVALS["stock-workspace"]);
}

async function initScannersPage(enablePolling = true) {
  const active = document.body.dataset.activeScanner || "overview";
  setStatus("scannersStatus", "Loading scanner output...");
  const path = active === "overview" ? "/api/scanners" : `/api/scanners/${encodeURIComponent(active)}`;
  const { ok, json } = await apiGet(path);

  if (!ok || !json.success) {
    setStatus("scannersStatus", json.error || "Scanner unavailable");
    return;
  }

  const data = json.data;
  setStatus("scannersStatus", `${data.scanner || "Scanner"} | Provider: ${json.provider || "Unavailable"} | Status: ${json.data_status || "unavailable"} | Last scan: ${data.last_scan_time || "unavailable"}`);

  const summaryTarget = document.getElementById("scannersSummary");
  if (summaryTarget) {
    summaryTarget.innerHTML = "";
    [
      ["Best Opportunity", data.highest_confidence_setup?.ticker],
      ["Largest Mover", data.largest_mover?.ticker],
      ["Best Breakout", data.best_breakout_candidate?.ticker],
      ["Best Reversal", data.best_reversal_candidate?.ticker],
      ["Highest Relative Volume", data.strongest_relative_volume?.ticker],
      ["Highest Risk", data.highest_risk_setup?.ticker],
      ["Scanner Health", data.data_health],
      ["Provider Status", data.provider_status || json.data_status],
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

  if (enablePolling) {
    startPolling(() => initScannersPage(false), POLL_INTERVALS.scanners);
  }
}

async function initBrainsPage(enablePolling = true) {
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

  if (enablePolling) {
    startPolling(() => initBrainsPage(false), POLL_INTERVALS.brains);
  }
}

async function initFuturesPage(enablePolling = true) {
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

  if (enablePolling) {
    startPolling(() => initFuturesPage(false), POLL_INTERVALS.futures);
  }
}

async function initMissionsPage(enablePolling = true) {
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
  if (timelineForm && !timelineForm.dataset.bound) {
    timelineForm.dataset.bound = "true";
    timelineForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const ticker = (document.getElementById("missionTimelineTicker")?.value || rootTicker).toUpperCase();
      await loadMissionTimeline(ticker, "missionTimelineStatus", "missionTimelineSummary", "missionTimelineTrack");
      rememberTicker(ticker);
    });
  }

  await loadMissionTimeline(rootTicker, "missionTimelineStatus", "missionTimelineSummary", "missionTimelineTrack");

  if (enablePolling) {
    startPolling(() => refreshMissionSummaryWidgets(), POLL_INTERVALS.missions);
  }
}

function init() {
  setConnectionStatus("offline");
  updateStreamStatus("UNAVAILABLE");
  setupRealtimeMode();
  setupTopTickerMemory();
  loadTickerSuggestions();
  initMissionPanel();

  if (state.mode === "streaming") {
    ensureSocketConnection();
    syncMarketStatus();
  } else {
    updateStreamStatus("POLLING");
  }

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
