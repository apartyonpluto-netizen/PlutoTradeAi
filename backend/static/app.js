const pageId = document.body.dataset.page;
const rootTicker = (document.body.dataset.ticker || "SPY").toUpperCase();

const POLL_INTERVALS = {
  dashboard: 20000,
  "stock-workspace": 10000,
  portfolio: 15000,
  "paper-trading": 15000,
  performance: 20000,
  alerts: 12000,
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
  runtimeConfig: null,
  apiCache: new Map(),
  inFlightGets: new Map(),
  signalsPage: 1,
  paperPage: 1,
  paperSort: { key: "created_at", direction: "desc" },
  tradesSort: { key: "created_at", direction: "desc" },
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

function fmtCurrency(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "Unavailable";
  }
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(Number(value));
}

function fmtSignedCurrency(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "Unavailable";
  }
  const num = Number(value);
  return `${num >= 0 ? "+" : "-"}${fmtCurrency(Math.abs(num))}`;
}

function fmtPct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "Unavailable";
  }
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function formatDirection(direction) {
  return String(direction || "NONE").toUpperCase();
}

function signalTone(direction) {
  const normalized = formatDirection(direction);
  if (normalized === "LONG") return "long";
  if (normalized === "SHORT") return "short";
  if (normalized === "EXIT") return "exit";
  if (normalized === "WATCH") return "watch";
  return "neutral";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
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
  if (normalized === "DELAYED") return "CACHED";
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
  const backendBadge = document.getElementById("backendDataBadge");
  if (backendBadge) {
    backendBadge.textContent = `BACKEND DATA: ${state.streamStatus}`;
  }
  setConnectionStatus(state.status);
}

async function syncRuntimeConfig() {
  const { ok, json } = await apiGet("/api/runtime/config");
  if (!ok || !json.success) {
    return;
  }
  state.runtimeConfig = json.data || {};

  const chartBadge = document.getElementById("chartProviderBadge");
  const paperBadge = document.getElementById("paperBrokerBadge");
  const executionBadge = document.getElementById("executionProviderBadge");
  const signalBadge = document.getElementById("signalSourceBadge");
  if (chartBadge) {
    chartBadge.textContent = `CHART: ${(state.runtimeConfig.chart_provider || "TRADINGVIEW").toUpperCase()}`;
  }
  if (paperBadge) {
    paperBadge.textContent = `PAPER BROKER: ${(state.runtimeConfig.paper_broker || "INTERNAL").toUpperCase()}`;
  }
  if (executionBadge) {
    executionBadge.textContent = `EXECUTION: ${(state.runtimeConfig.execution_provider || "DISABLED").toUpperCase()}`;
  }
  if (signalBadge) {
    signalBadge.textContent = `SIGNAL SOURCE: ${(state.runtimeConfig.signal_provider || "NONE").toUpperCase()}`;
  }
}

async function refreshSignalSource() {
  const statusEl = document.getElementById("signalSourceStatus");
  const sourceBadge = document.getElementById("signalSourceBadge");
  const { ok, json } = await apiGet("/api/signals?limit=1");
  if (!ok || !json.success) {
    if (statusEl) {
      statusEl.textContent = "Signal queue unavailable";
    }
    return;
  }

  const first = (json.data.signals || [])[0];
  if (!first) {
    if (statusEl) {
      statusEl.textContent = "No signal received yet.";
    }
    if (sourceBadge) {
      sourceBadge.textContent = "SIGNAL SOURCE: NONE";
    }
    return;
  }

  if (statusEl) {
    statusEl.textContent = `${first.symbol} ${first.direction} | ${first.strategy} | Status: ${first.status} | Received: ${first.received_at}`;
  }
  if (sourceBadge) {
    sourceBadge.textContent = `SIGNAL SOURCE: ${String(first.source || "NONE").toUpperCase()}`;
  }
}

function ensureTradingViewScript() {
  return new Promise((resolve, reject) => {
    if (window.TradingView) {
      resolve();
      return;
    }

    const existing = document.getElementById("tradingview-script");
    if (existing) {
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", () => reject(new Error("TradingView script failed to load")));
      return;
    }

    const script = document.createElement("script");
    script.id = "tradingview-script";
    script.src = "https://s3.tradingview.com/tv.js";
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("TradingView script failed to load"));
    document.head.appendChild(script);
  });
}

async function renderTradingViewChart(symbol, interval) {
  const status = document.getElementById("tradingViewChartStatus");
  const fallback = document.getElementById("tradingViewChartFallback");
  const host = document.getElementById("tradingViewChart");
  if (!host) {
    return;
  }

  if (status) {
    status.textContent = "Loading TradingView chart...";
  }
  if (fallback) {
    fallback.textContent = "";
  }

  host.innerHTML = "";

  try {
    await ensureTradingViewScript();
    if (!window.TradingView || typeof window.TradingView.widget !== "function") {
      throw new Error("TradingView widget API unavailable");
    }

    const widgetId = "tv_chart_container";
    const container = document.createElement("div");
    container.id = widgetId;
    container.style.width = "100%";
    container.style.height = "360px";
    host.appendChild(container);

    window.TradingView.widget({
      width: "100%",
      height: 360,
      symbol,
      interval,
      timezone: "Etc/UTC",
      theme: "dark",
      style: "1",
      locale: "en",
      enable_publishing: false,
      allow_symbol_change: false,
      hide_top_toolbar: false,
      save_image: false,
      container_id: widgetId,
    });

    if (status) {
      status.textContent = `TradingView visual chart loaded: ${symbol} (${interval})`;
    }
  } catch (_error) {
    if (status) {
      status.textContent = "TradingView chart unavailable";
    }
    if (fallback) {
      fallback.textContent = "Chart load failed. Backend market data remains available below.";
    }
  }
}

async function apiGet(url, options = {}) {
  const { ttlMs = 0, force = false } = options;
  if (!force && ttlMs > 0) {
    const cached = state.apiCache.get(url);
    if (cached && (Date.now() - cached.createdAt) <= ttlMs) {
      return cached.value;
    }
  }

  if (!force && state.inFlightGets.has(url)) {
    return state.inFlightGets.get(url);
  }

  const start = performance.now();
  const isMarketEndpoint = url.startsWith("/api/market") || url.startsWith("/api/stock") || url.startsWith("/api/chart");
  const requestPromise = (async () => {
    try {
      const response = await fetch(url);
      const json = await response.json();
      const result = { ok: response.ok, json };
      setConnectionStatus("connected", performance.now() - start);
      if (isMarketEndpoint) {
        updateStreamStatus(json.data_status);
      }
      if (ttlMs > 0 && response.ok) {
        state.apiCache.set(url, { createdAt: Date.now(), value: result });
      }
      return result;
    } catch (_error) {
      setConnectionStatus("reconnecting");
      if (isMarketEndpoint) {
        updateStreamStatus("UNAVAILABLE");
      }
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
    } finally {
      state.inFlightGets.delete(url);
    }
  })();

  state.inFlightGets.set(url, requestPromise);
  return requestPromise;
}

async function apiPost(url, payload) {
  const start = performance.now();
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      credentials: "same-origin",
    });
    let json;
    try {
      json = await response.json();
    } catch (_parseError) {
      json = { success: false, error: `Server returned HTTP ${response.status} without a JSON body`, data: {} };
    }
    setConnectionStatus("connected", performance.now() - start);
    // A state-changing call makes every cached read potentially stale.
    state.apiCache.clear();
    return { ok: response.ok, status: response.status, json };
  } catch (_error) {
    setConnectionStatus("reconnecting");
    return {
      ok: false,
      status: 0,
      json: {
        success: false,
        error: "Could not reach the server; the action was NOT confirmed. Reload and check the signal status before retrying.",
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

function renderSummaryCards(targetId, cards) {
  const target = document.getElementById(targetId);
  if (!target) return;
  target.innerHTML = "";
  cards.forEach((card) => {
    const node = document.createElement("article");
    node.className = `metric-card ${card.tone ? `metric-card--${card.tone}` : ""}`;
    node.innerHTML = `
      <div class="metric-card__label">${card.label}</div>
      <div class="metric-card__value">${card.value}</div>
      <div class="metric-card__subtext">${card.subtext || ""}</div>
    `;
    target.appendChild(node);
  });
}

function renderStackList(targetId, rows, emptyMessage = "Nothing to show.") {
  const target = document.getElementById(targetId);
  if (!target) return;
  target.innerHTML = "";
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = emptyMessage;
    target.appendChild(empty);
    return;
  }
  rows.forEach((row) => {
    const node = document.createElement("article");
    node.className = `stack-card ${row.tone ? `stack-card--${row.tone}` : ""}`;
    node.innerHTML = `
      <div class="stack-card__header">
        <div>
          <div class="stack-card__title">${row.title}</div>
          <div class="stack-card__meta">${row.meta || ""}</div>
        </div>
        <div class="stack-card__value">${row.value || ""}</div>
      </div>
      <div class="stack-card__body">${row.body || ""}</div>
    `;
    target.appendChild(node);
  });
}

function sortRows(rows, sortState) {
  const direction = sortState.direction === "asc" ? 1 : -1;
  return [...rows].sort((left, right) => {
    const a = left?.[sortState.key];
    const b = right?.[sortState.key];
    if (a === b) return 0;
    if (a === null || a === undefined) return 1;
    if (b === null || b === undefined) return -1;
    if (typeof a === "number" && typeof b === "number") return (a - b) * direction;
    return String(a).localeCompare(String(b)) * direction;
  });
}

function paginate(rows, page, perPage = 8) {
  const safePage = Math.max(1, page);
  const start = (safePage - 1) * perPage;
  return rows.slice(start, start + perPage);
}

function renderMiniLineChart(targetId, values, options = {}) {
  const target = document.getElementById(targetId);
  if (!target) return;
  target.innerHTML = "";
  if (!values.length) {
    target.innerHTML = '<div class="empty-state">No chart points are available for this period yet. Try a broader timeframe or retry after provider refresh.</div>';
    return;
  }
  const width = 520;
  const height = 180;
  const padding = 16;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values.map((value, index) => {
    const x = padding + ((width - padding * 2) * index) / Math.max(values.length - 1, 1);
    const y = height - padding - (((value - min) / span) * (height - padding * 2));
    return `${x},${y}`;
  }).join(" ");
  const stroke = options.stroke || "#4fe0b9";
  target.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" class="chart-svg">
      <defs>
        <linearGradient id="${targetId}-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${stroke}" stop-opacity="0.35"></stop>
          <stop offset="100%" stop-color="${stroke}" stop-opacity="0.02"></stop>
        </linearGradient>
      </defs>
      <polyline fill="none" stroke="${stroke}" stroke-width="3" points="${points}"></polyline>
    </svg>
  `;
}

function renderMiniBars(targetId, items) {
  const target = document.getElementById(targetId);
  if (!target) return;
  target.innerHTML = "";
  if (!items.length) {
    target.innerHTML = '<div class="empty-state">No distribution data yet.</div>';
    return;
  }
  const max = Math.max(...items.map((item) => Number(item.value) || 0), 1);
  items.forEach((item) => {
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `
      <div class="bar-row__label">${item.label}</div>
      <div class="bar-row__track"><span class="bar-row__fill" style="width:${(Number(item.value) / max) * 100}%"></span></div>
      <div class="bar-row__value">${item.display ?? item.value}</div>
    `;
    target.appendChild(row);
  });
}

async function loadWorkspaceOverlay(ticker) {
  const statusId = "workspaceOverlayStatus";
  setStatus(statusId, "Loading AI overlay metrics...");
  const [overview, trend, levels, risk, signals] = await Promise.all([
    apiGet(`/api/brains/${encodeURIComponent(ticker)}`, { ttlMs: 8000 }),
    apiGet(`/api/brains/trend/${encodeURIComponent(ticker)}`, { ttlMs: 8000 }),
    apiGet(`/api/brains/support-resistance/${encodeURIComponent(ticker)}`, { ttlMs: 8000 }),
    apiGet(`/api/brains/risk/${encodeURIComponent(ticker)}`, { ttlMs: 8000 }),
    apiGet("/api/signals?limit=20", { ttlMs: 5000 }),
  ]);

  if (!overview.ok || !overview.json.success) {
    setStatus(statusId, overview.json.error || "AI overlay could not be generated right now. Quote data may still be available.");
    renderMetricGrid("workspaceOverlayCards", []);
    return;
  }

  const signalRows = (signals.json?.data?.signals || []).filter((row) => row.symbol === ticker);
  const latestSignal = signalRows[0];
  renderMetricGrid("workspaceOverlayCards", [
    { label: "AI Confidence", value: `${overview.json.data.confidence ?? "--"}%` },
    { label: "Trend", value: trend.json?.data?.trend || "Unavailable" },
    { label: "Support", value: levels.json?.data?.support !== undefined ? fmtCurrency(levels.json.data.support) : "Unavailable" },
    { label: "Resistance", value: levels.json?.data?.resistance !== undefined ? fmtCurrency(levels.json.data.resistance) : "Unavailable" },
    { label: "Risk/Reward", value: risk.json?.data?.risk_reward_ratio ?? "Unavailable" },
    { label: "Data Freshness", value: overview.json.data.data_quality || overview.json.data.data_status || "Unavailable" },
  ]);
  const signalTarget = document.getElementById("workspaceSignalContext");
  if (signalTarget) {
    signalTarget.innerHTML = latestSignal ? `
      <div class="detail-item"><strong>Direction</strong>: ${escapeHtml(latestSignal.direction)}</div>
      <div class="detail-item"><strong>Status</strong>: ${escapeHtml(latestSignal.status)}</div>
      <div class="detail-item"><strong>Strategy</strong>: ${escapeHtml(latestSignal.strategy)}</div>
      <div class="detail-item"><strong>Entry</strong>: ${latestSignal.analysis?.entry_zone ?? latestSignal.price ?? "Unavailable"}</div>
      <div class="detail-item"><strong>Stop</strong>: ${latestSignal.analysis?.stop_loss ?? latestSignal.stop_loss ?? "Unavailable"}</div>
      <div class="detail-item"><strong>Target</strong>: ${latestSignal.analysis?.primary_target ?? latestSignal.take_profit ?? "Unavailable"}</div>
    ` : '<div class="empty-state">No TradingView signal has been recorded for this symbol yet.</div>';
  }
  setStatus(statusId, `Confidence ${overview.json.data.confidence ?? "--"}% | Trend ${trend.json?.data?.trend || "Unavailable"} | Risk ${risk.json?.data?.risk_status || "Unavailable"}`);
}

function calculateConfidenceAverage(signals) {
  const values = signals.map((row) => Number(row.analysis?.confidence_score ?? row.confidence_hint)).filter((value) => !Number.isNaN(value));
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function calculateTodaysPl(performance, account) {
  const curve = performance.equity_curve || [];
  if (curve.length < 2) {
    return Number(account.realized_pl || 0) + Number(account.unrealized_pl || 0);
  }
  const today = new Date().toISOString().slice(0, 10);
  const todayRows = curve.filter((point) => String(point.timestamp || "").startsWith(today));
  if (todayRows.length >= 2) {
    return Number(todayRows[todayRows.length - 1].equity) - Number(todayRows[0].equity);
  }
  return Number(account.realized_pl || 0) + Number(account.unrealized_pl || 0);
}

async function loadDashboardCommandCenter() {
  const [accountResp, perfResp, signalsResp] = await Promise.all([
    apiGet("/api/paper/account", { ttlMs: 8000 }),
    apiGet("/api/paper/performance", { ttlMs: 8000 }),
    apiGet("/api/signals?limit=20", { ttlMs: 5000 }),
  ]);

  const account = accountResp.json?.data || {};
  const performance = perfResp.json?.data || {};
  const signals = signalsResp.json?.data?.signals || [];
  const activeSignals = signals.filter((row) => ["VALIDATED", "ANALYZING", "AWAITING_APPROVAL"].includes(row.status));
  const confidenceAverage = calculateConfidenceAverage(signals);
  const todaysPl = calculateTodaysPl(performance, account);

  renderSummaryCards("dashboardCommandCards", [
    { label: "Portfolio Value", value: fmtCurrency(account.equity), subtext: "Internal paper equity", tone: "neutral" },
    { label: "Buying Power", value: fmtCurrency(account.buying_power), subtext: "Available for new paper positions", tone: "neutral" },
    { label: "Open Positions", value: String(account.open_positions ?? 0), subtext: "Currently active", tone: "neutral" },
    { label: "Unrealized P/L", value: fmtSignedCurrency(account.unrealized_pl), subtext: "Mark-to-market", tone: Number(account.unrealized_pl || 0) >= 0 ? "long" : "short" },
    { label: "Today's P/L", value: fmtSignedCurrency(todaysPl), subtext: "Session-to-session change", tone: Number(todaysPl || 0) >= 0 ? "long" : "short" },
    { label: "Win Rate", value: fmtPct(performance.win_rate || 0), subtext: `${performance.wins || 0} wins / ${performance.losses || 0} losses`, tone: "neutral" },
    { label: "Confidence Average", value: confidenceAverage !== null ? `${confidenceAverage.toFixed(1)}%` : "Unavailable", subtext: "Across recent signals", tone: "neutral" },
    { label: "Active Signals", value: String(activeSignals.length), subtext: "Awaiting action or analysis", tone: "watch" },
  ]);

  setStatus("dashboardSignalsStatus", `Signals loaded: ${signals.length}`);
  renderStackList("dashboardSignalsFeed", activeSignals.slice(0, 4).map((row) => ({
    title: `${row.symbol} ${row.direction}`,
    meta: `${row.strategy} | ${row.status}`,
    value: row.analysis?.confidence_score ? `${row.analysis.confidence_score}%` : "--",
    body: `Entry ${row.analysis?.entry_zone ?? row.price ?? "--"} | Stop ${row.analysis?.stop_loss ?? row.stop_loss ?? "--"} | Target ${row.analysis?.primary_target ?? row.take_profit ?? "--"}`,
    tone: signalTone(row.direction),
  })), "No active signals.");

  setStatus("dashboardPortfolioStatus", `Equity ${fmtCurrency(account.equity)} | Open positions ${account.open_positions ?? 0}`);
  const portfolioTarget = document.getElementById("dashboardPortfolioSummary");
  if (portfolioTarget) {
    portfolioTarget.innerHTML = [
      ["Cash", fmtCurrency(account.cash)],
      ["Buying Power", fmtCurrency(account.buying_power)],
      ["Realized P/L", fmtSignedCurrency(account.realized_pl)],
      ["Unrealized P/L", fmtSignedCurrency(account.unrealized_pl)],
      ["Expectancy", performance.expectancy ?? "Unavailable"],
      ["Max Drawdown", fmtPct(performance.maximum_drawdown || 0)],
    ].map(([label, value]) => `<div class="detail-item"><strong>${label}</strong>: ${value}</div>`).join("");
  }
}

async function loadPortfolioOverview() {
  const [accountResp, positionsResp, tradesResp, performanceResp, signalsResp] = await Promise.all([
    apiGet("/api/paper/account", { ttlMs: 5000 }),
    apiGet("/api/paper/positions", { ttlMs: 5000 }),
    apiGet("/api/paper/trades", { ttlMs: 5000 }),
    apiGet("/api/paper/performance", { ttlMs: 5000 }),
    apiGet("/api/signals?limit=20", { ttlMs: 5000 }),
  ]);

  const account = accountResp.json?.data || {};
  const positions = positionsResp.json?.data?.positions || [];
  const trades = tradesResp.json?.data?.trades || [];
  const performance = performanceResp.json?.data || {};
  const signals = signalsResp.json?.data?.signals || [];
  renderSummaryCards("portfolioSummaryCards", [
    { label: "Portfolio Value", value: fmtCurrency(account.equity), subtext: "Internal paper ledger", tone: "neutral" },
    { label: "Buying Power", value: fmtCurrency(account.buying_power), subtext: "Deployable cash", tone: "neutral" },
    { label: "Open Positions", value: String(account.open_positions ?? 0), subtext: "Current exposure", tone: "neutral" },
    { label: "Unrealized P/L", value: fmtSignedCurrency(account.unrealized_pl), subtext: "Open mark-to-market", tone: Number(account.unrealized_pl || 0) >= 0 ? "long" : "short" },
  ]);

  setStatus("portfolioPositionsStatus", `${positions.length} open positions`);
  renderStackList("portfolioPositionsPreview", positions.slice(0, 5).map((row) => ({
    title: `${row.symbol} ${row.direction}`,
    meta: `Qty ${row.quantity} | Entry ${fmtCurrency(row.entry_price)}`,
    value: fmtSignedCurrency(row.unrealized_pl),
    body: `Stop ${fmtCurrency(row.stop_loss)} | Target ${fmtCurrency(row.take_profit)}`,
    tone: signalTone(row.direction),
  })), "No open positions.");

  const pendingSignals = signals.filter((row) => row.status === "AWAITING_APPROVAL");
  setStatus("portfolioSignalStatus", `${pendingSignals.length} signals awaiting approval`);
  renderStackList("portfolioSignalPreview", pendingSignals.slice(0, 5).map((row) => ({
    title: `${row.symbol} ${row.direction}`,
    meta: `${row.strategy} | ${row.received_at}`,
    value: row.analysis?.confidence_score ? `${row.analysis.confidence_score}%` : "--",
    body: `Risk/Reward ${row.analysis?.risk_reward_ratio ?? "--"} | Status ${row.status}`,
    tone: signalTone(row.direction),
  })), "No pending signals.");

  const closedTrades = trades.filter((row) => row.status === "CLOSED");
  setStatus("portfolioTradesStatus", `${closedTrades.length} closed trades recorded`);
  renderStackList("portfolioTradesPreview", closedTrades.slice(0, 5).map((row) => ({
    title: `${row.symbol} ${row.direction}`,
    meta: `${row.created_at || ""}`,
    value: fmtSignedCurrency(row.realized_pl),
    body: `Entry ${fmtCurrency(row.entry_price)} | Exit ${fmtCurrency(row.exit_price)} | Status ${row.status}`,
    tone: Number(row.realized_pl || 0) >= 0 ? "long" : "short",
  })), "No closed trades yet.");

  setStatus("portfolioPerformanceStatus", `Win rate ${fmtPct(performance.win_rate || 0)} | Expectancy ${performance.expectancy ?? "--"}`);
  const perfTarget = document.getElementById("portfolioPerformanceSummary");
  if (perfTarget) {
    perfTarget.innerHTML = [
      ["Win Rate", fmtPct(performance.win_rate || 0)],
      ["Average Win", fmtCurrency(performance.average_win)],
      ["Average Loss", fmtCurrency(performance.average_loss)],
      ["Expectancy", performance.expectancy ?? "Unavailable"],
      ["Maximum Drawdown", fmtPct(performance.maximum_drawdown || 0)],
      ["Trades Total", performance.trades_total ?? 0],
    ].map(([label, value]) => `<div class="detail-item"><strong>${label}</strong>: ${value}</div>`).join("");
  }
}

function filterSignals(rows) {
  const query = (document.getElementById("signalSearchInput")?.value || "").trim().toUpperCase();
  const direction = document.getElementById("signalDirectionFilter")?.value || "ALL";
  const status = document.getElementById("signalStatusFilter")?.value || "ALL";
  return rows.filter((row) => {
    const matchesQuery = !query || [row.symbol, row.strategy, row.status].join(" ").toUpperCase().includes(query);
    const matchesDirection = direction === "ALL" || row.direction === direction;
    const matchesStatus = status === "ALL" || row.status === status;
    return matchesQuery && matchesDirection && matchesStatus;
  });
}

const signalActionsInFlight = new Set();

async function takeSignalAction(signalId, action) {
  if (signalActionsInFlight.has(signalId)) {
    return;
  }
  const needsReason = action === "decline";
  const message = action === "approve" ? "Approve this signal and create a paper trade?" : "Decline this signal?";
  if (!window.confirm(message)) {
    return;
  }
  let payload = {};
  if (needsReason) {
    const reason = window.prompt("Decline rationale", "Risk not acceptable");
    if (reason === null) {
      return;
    }
    payload = { reason };
  }
  signalActionsInFlight.add(signalId);
  try {
    const url = `/api/signals/${encodeURIComponent(signalId)}/${action}`;
    let result = await apiPost(url, payload);
    if (!result.ok && result.json?.data?.code === "STALE_DATA" && action === "approve") {
      if (window.confirm(`${result.json.error}\n\nApprove anyway using stale data?`)) {
        result = await apiPost(url, { ...payload, acknowledge_stale: true });
      }
    }
    const { ok, json } = result;
    let text;
    if (ok && json.success) {
      text = json.data?.idempotent ? `Signal was already ${action}d` : `Signal ${action}d`;
    } else if (result.status === 401) {
      text = "Sign in to approve or decline signals";
    } else if (result.status === 403) {
      text = json.error || "Your account is not permitted to do this";
    } else {
      text = json.error || `Signal ${action} failed`;
    }
    showToast(text, ok && json.success ? "success" : "error");
  } finally {
    signalActionsInFlight.delete(signalId);
  }
  if (pageId === "alerts") {
    await initAlertsPage(false);
  }
  if (pageId === "portfolio") {
    await loadPortfolioOverview();
  }
}

async function initAlertsPage(enablePolling = true) {
  const { ok, json } = await apiGet("/api/signals?limit=100", { ttlMs: 3000, force: true });
  if (!ok || !json.success) {
    setStatus("signalFeedStatus", json.error || "Signal feed unavailable");
    return;
  }
  const allSignals = json.data.signals || [];
  const filtered = filterSignals(allSignals);
  const pageRows = paginate(filtered, state.signalsPage, 8);
  const target = document.getElementById("signalFeed");
  const empty = document.getElementById("signalFeedEmpty");
  if (target) {
    target.innerHTML = "";
    pageRows.forEach((row) => {
      const node = document.createElement("article");
      node.className = `signal-card signal-card--${signalTone(row.direction)}`;
      const canApprove = row.status === "AWAITING_APPROVAL";
      const canDecline = ["VALIDATED", "ANALYZING", "AWAITING_APPROVAL"].includes(row.status);
      const reason = row.status_reason ? `<div class="signal-card__meta">${escapeHtml(row.status_reason)}</div>` : "";
      const id = escapeHtml(row.signal_id);
      node.innerHTML = `
        <div class="signal-card__header">
          <div>
            <div class="signal-card__ticker">${escapeHtml(row.symbol)}</div>
            <div class="signal-card__meta">${escapeHtml(row.strategy)} | ${escapeHtml(row.timeframe)} | ${escapeHtml(row.received_at)}</div>
          </div>
          <span class="signal-badge signal-badge--${signalTone(row.direction)}">${escapeHtml(row.direction)}</span>
        </div>
        <div class="signal-card__grid">
          <div><span>Confidence</span><strong>${escapeHtml(row.analysis?.confidence_score ?? row.confidence_hint ?? "--")}</strong></div>
          <div><span>Risk</span><strong>${escapeHtml(row.analysis?.risk_reward_ratio ?? "--")}</strong></div>
          <div><span>Entry</span><strong>${escapeHtml(row.analysis?.entry_zone ?? row.price ?? "--")}</strong></div>
          <div><span>Stop</span><strong>${escapeHtml(row.analysis?.stop_loss ?? row.stop_loss ?? "--")}</strong></div>
          <div><span>Targets</span><strong>${escapeHtml(row.analysis?.primary_target ?? row.take_profit ?? "--")}${row.analysis?.secondary_target ? ` / ${escapeHtml(row.analysis.secondary_target)}` : ""}</strong></div>
          <div><span>Status</span><strong>${escapeHtml(row.status)}</strong></div>
        </div>
        ${reason}
        <div class="signal-card__footer">
          <span class="status-pill">Freshness: ${escapeHtml(row.analysis?.data_freshness || "UNKNOWN")}</span>
          <div class="toolbar-group">
            <button class="stock-action signal-approve" data-signal-id="${id}" ${canApprove ? "" : "disabled"}>Approve</button>
            <button class="stock-action signal-decline" data-signal-id="${id}" ${canDecline ? "" : "disabled"}>Decline</button>
          </div>
        </div>
      `;
      target.appendChild(node);
    });
    target.querySelectorAll(".signal-approve").forEach((button) => button.addEventListener("click", () => takeSignalAction(button.dataset.signalId, "approve")));
    target.querySelectorAll(".signal-decline").forEach((button) => button.addEventListener("click", () => takeSignalAction(button.dataset.signalId, "decline")));
  }
  if (empty) {
    empty.hidden = filtered.length > 0;
  }
  const label = document.getElementById("signalPaginationLabel");
  if (label) {
    const totalPages = Math.max(1, Math.ceil(filtered.length / 8));
    label.textContent = `Page ${state.signalsPage} / ${totalPages}`;
  }
  setStatus("signalFeedStatus", `${filtered.length} signals in view | ${allSignals.length} total loaded`);

  const refreshBtn = document.getElementById("refreshSignalsButton");
  if (refreshBtn && !refreshBtn.dataset.bound) {
    refreshBtn.dataset.bound = "true";
    refreshBtn.addEventListener("click", () => initAlertsPage(false));
  }
  ["signalSearchInput", "signalDirectionFilter", "signalStatusFilter"].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !el.dataset.bound) {
      el.dataset.bound = "true";
      el.addEventListener("input", () => {
        state.signalsPage = 1;
        initAlertsPage(false);
      });
      el.addEventListener("change", () => {
        state.signalsPage = 1;
        initAlertsPage(false);
      });
    }
  });
  const prevBtn = document.getElementById("signalPrevPage");
  const nextBtn = document.getElementById("signalNextPage");
  if (prevBtn && !prevBtn.dataset.bound) {
    prevBtn.dataset.bound = "true";
    prevBtn.addEventListener("click", () => {
      state.signalsPage = Math.max(1, state.signalsPage - 1);
      initAlertsPage(false);
    });
  }
  if (nextBtn && !nextBtn.dataset.bound) {
    nextBtn.dataset.bound = "true";
    nextBtn.addEventListener("click", () => {
      state.signalsPage += 1;
      initAlertsPage(false);
    });
  }
  if (enablePolling) {
    startPolling(() => initAlertsPage(false), POLL_INTERVALS.alerts);
  }
}

function filterPaperRows(rows) {
  const query = (document.getElementById("paperSearchInput")?.value || "").trim().toUpperCase();
  const direction = document.getElementById("paperDirectionFilter")?.value || "ALL";
  const status = document.getElementById("paperStatusFilter")?.value || "ALL";
  return rows.filter((row) => {
    const haystack = [row.symbol, row.direction, row.status, row.position_id, row.trade_id].join(" ").toUpperCase();
    const matchesQuery = !query || haystack.includes(query);
    const matchesDirection = direction === "ALL" || row.direction === direction;
    const matchesStatus = status === "ALL" || row.status === status;
    return matchesQuery && matchesDirection && matchesStatus;
  });
}

function wireSortableTable(tableId, sortState, rerender) {
  const table = document.getElementById(tableId);
  if (!table || table.dataset.bound) return;
  table.dataset.bound = "true";
  table.querySelectorAll("th[data-sort]").forEach((header) => {
    header.addEventListener("click", () => {
      const key = header.dataset.sort;
      if (sortState.key === key) {
        sortState.direction = sortState.direction === "asc" ? "desc" : "asc";
      } else {
        sortState.key = key;
        sortState.direction = "asc";
      }
      rerender();
    });
  });
}

async function initPaperTradingPage(enablePolling = true) {
  const [positionsResp, tradesResp] = await Promise.all([
    apiGet("/api/paper/positions", { ttlMs: 4000, force: true }),
    apiGet("/api/paper/trades", { ttlMs: 4000, force: true }),
  ]);
  const allPositions = positionsResp.json?.data?.positions || [];
  const allTrades = tradesResp.json?.data?.trades || [];

  const filteredPositions = sortRows(filterPaperRows(allPositions), state.paperSort);
  const filteredTrades = sortRows(filterPaperRows(allTrades), state.tradesSort);
  const pagePositions = paginate(filteredPositions, state.paperPage, 6);
  const pageTrades = paginate(filteredTrades, state.paperPage, 6);

  const openBody = document.querySelector("#paperOpenTable tbody");
  if (openBody) {
    openBody.innerHTML = "";
    pagePositions.forEach((row) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(row.symbol || "-")}</td>
        <td><span class="signal-badge signal-badge--${signalTone(row.direction)}">${row.direction || "-"}</span></td>
        <td>${row.quantity ?? "-"}</td>
        <td>${fmtCurrency(row.entry_price)}</td>
        <td>${fmtCurrency(row.last_mark_price)}</td>
        <td class="${Number(row.unrealized_pl || 0) >= 0 ? "positive" : "negative"}">${fmtSignedCurrency(row.unrealized_pl)}</td>
        <td>${fmtCurrency(row.take_profit)}</td>
      `;
      openBody.appendChild(tr);
    });
  }
  const tradeBody = document.querySelector("#paperTradesTable tbody");
  if (tradeBody) {
    tradeBody.innerHTML = "";
    pageTrades.forEach((row) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${escapeHtml(row.symbol || "-")}</td>
        <td><span class="signal-badge signal-badge--${signalTone(row.direction)}">${row.direction || "-"}</span></td>
        <td>${escapeHtml(row.status || "-")}</td>
        <td>${fmtCurrency(row.entry_price)}</td>
        <td>${fmtCurrency(row.avg_exit_price ?? row.exit_price)}</td>
        <td class="${Number(row.net_realized_pl ?? row.realized_pl ?? 0) >= 0 ? "positive" : "negative"}">${fmtSignedCurrency(row.net_realized_pl ?? row.realized_pl)}</td>
        <td>${row.created_at || "-"}</td>
      `;
      tradeBody.appendChild(tr);
    });
  }

  const pages = Math.max(1, Math.ceil(Math.max(filteredPositions.length, filteredTrades.length) / 6));
  const label = document.getElementById("paperPaginationLabel");
  if (label) label.textContent = `Page ${state.paperPage} / ${pages}`;
  setStatus("paperOpenStatus", `${filteredPositions.length} positions in view`);
  setStatus("paperTradesStatus", `${filteredTrades.length} trades in view`);
  const openEmpty = document.getElementById("paperOpenEmpty");
  if (openEmpty) openEmpty.hidden = filteredPositions.length > 0;
  const tradesEmpty = document.getElementById("paperTradesEmpty");
  if (tradesEmpty) tradesEmpty.hidden = filteredTrades.length > 0;

  ["paperSearchInput", "paperDirectionFilter", "paperStatusFilter"].forEach((id) => {
    const el = document.getElementById(id);
    if (el && !el.dataset.bound) {
      el.dataset.bound = "true";
      el.addEventListener("input", () => {
        state.paperPage = 1;
        initPaperTradingPage(false);
      });
      el.addEventListener("change", () => {
        state.paperPage = 1;
        initPaperTradingPage(false);
      });
    }
  });
  const prevBtn = document.getElementById("paperPrevPage");
  const nextBtn = document.getElementById("paperNextPage");
  if (prevBtn && !prevBtn.dataset.bound) {
    prevBtn.dataset.bound = "true";
    prevBtn.addEventListener("click", () => {
      state.paperPage = Math.max(1, state.paperPage - 1);
      initPaperTradingPage(false);
    });
  }
  if (nextBtn && !nextBtn.dataset.bound) {
    nextBtn.dataset.bound = "true";
    nextBtn.addEventListener("click", () => {
      state.paperPage += 1;
      initPaperTradingPage(false);
    });
  }
  wireSortableTable("paperOpenTable", state.paperSort, () => initPaperTradingPage(false));
  wireSortableTable("paperTradesTable", state.tradesSort, () => initPaperTradingPage(false));

  if (enablePolling) {
    startPolling(() => initPaperTradingPage(false), POLL_INTERVALS["paper-trading"]);
  }
}

async function initPerformancePage(enablePolling = true) {
  const [performanceResp, tradesResp, signalsResp] = await Promise.all([
    apiGet("/api/paper/performance", { ttlMs: 4000, force: true }),
    apiGet("/api/paper/trades", { ttlMs: 4000, force: true }),
    apiGet("/api/signals?limit=200", { ttlMs: 4000, force: true }),
  ]);
  const performance = performanceResp.json?.data || {};
  const trades = tradesResp.json?.data?.trades || [];
  const closedTrades = trades.filter((row) => row.status === "CLOSED");
  const signals = signalsResp.json?.data?.signals || [];

  renderSummaryCards("performanceMetricCards", [
    { label: "Equity", value: fmtCurrency(performance.equity), subtext: "Current portfolio value", tone: "neutral" },
    { label: "Win %", value: fmtPct(performance.win_rate || 0), subtext: `${performance.wins || 0} winning trades`, tone: "neutral" },
    { label: "Drawdown", value: fmtPct(performance.maximum_drawdown || 0), subtext: "Peak-to-trough", tone: "short" },
    { label: "Expectancy", value: performance.expectancy ?? "Unavailable", subtext: "Per closed trade", tone: "neutral" },
  ]);

  const equityValues = (performance.equity_curve || []).map((point) => Number(point.equity)).filter((value) => !Number.isNaN(value));
  renderMiniLineChart("equityCurveChart", equityValues, { stroke: "#4fe0b9" });
  renderMiniLineChart("dailyPlChart", equityValues.map((value, index, list) => index === 0 ? 0 : value - list[index - 1]), { stroke: "#ffc857" });

  const drawdowns = [];
  let peak = 0;
  equityValues.forEach((value) => {
    peak = Math.max(peak, value);
    drawdowns.push(peak > 0 ? ((peak - value) / peak) * 100 : 0);
  });
  renderMiniLineChart("drawdownChart", drawdowns, { stroke: "#ef6461" });

  renderMiniBars("tradeDistributionChart", [
    { label: "Wins", value: performance.wins || 0 },
    { label: "Losses", value: performance.losses || 0 },
    { label: "Closed", value: performance.trades_total || 0 },
  ]);

  renderMiniBars("riskDistributionChart", closedTrades.slice(0, 8).map((row) => ({
    label: row.symbol,
    value: Math.abs(Number(row.entry_price || 0) - Number(row.stop_loss || 0)) || 0.1,
    display: fmtCurrency(Math.abs(Number(row.entry_price || 0) - Number(row.stop_loss || 0))),
  })));

  const accuracyBuckets = signals.filter((row) => row.status === "PAPER_EXECUTED").slice(0, 8).map((row) => ({
    label: row.symbol,
    value: Number(row.analysis?.confidence_score || row.confidence_hint || 0),
    display: `${Number(row.analysis?.confidence_score || row.confidence_hint || 0).toFixed(0)}%`,
  }));
  renderMiniBars("confidenceAccuracyChart", accuracyBuckets);

  setStatus("equityCurveStatus", `Points: ${equityValues.length}`);
  setStatus("dailyPlStatus", `Closed trades: ${performance.trades_total || 0}`);
  setStatus("drawdownStatus", `Max drawdown ${fmtPct(performance.maximum_drawdown || 0)}`);

  const sortedByRealized = [...closedTrades].sort((a, b) => Number(b.realized_pl || 0) - Number(a.realized_pl || 0));
  renderStackList("bestTradesList", sortedByRealized.slice(0, 5).map((row) => ({
    title: `${row.symbol} ${row.direction}`,
    meta: row.closed_at || row.created_at || "",
    value: fmtSignedCurrency(row.realized_pl),
    body: `Entry ${fmtCurrency(row.entry_price)} | Exit ${fmtCurrency(row.exit_price)}`,
    tone: "long",
  })), "No closed trades yet.");
  renderStackList("worstTradesList", sortedByRealized.slice(-5).reverse().map((row) => ({
    title: `${row.symbol} ${row.direction}`,
    meta: row.closed_at || row.created_at || "",
    value: fmtSignedCurrency(row.realized_pl),
    body: `Entry ${fmtCurrency(row.entry_price)} | Exit ${fmtCurrency(row.exit_price)}`,
    tone: "short",
  })), "No closed trades yet.");

  if (enablePolling) {
    startPolling(() => initPerformancePage(false), POLL_INTERVALS.performance);
  }
}

function bindKeyboardShortcuts() {
  if (document.body.dataset.shortcutsBound === "true") return;
  document.body.dataset.shortcutsBound = "true";
  document.addEventListener("keydown", (event) => {
    if (event.target && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) {
      return;
    }
    if (event.key === "/") {
      event.preventDefault();
      document.getElementById("tickerInput")?.focus();
    }
    if (event.key.toLowerCase() === "s") {
      window.location.href = "/opportunities/alerts";
    }
    if (event.key.toLowerCase() === "p") {
      window.location.href = "/portfolio";
    }
    if (event.key === "Escape") {
      document.getElementById("cancelMissionPanel")?.click();
    }
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

  const { ok, json } = await apiGet(`/api/symbols/search?q=${encodeURIComponent(seed)}`);
  const serverRows = ok && json.success ? (json.data.items || []) : [];
  const serverItems = serverRows.map((row) => row.symbol).filter(Boolean);
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

  const addCandle = typeof chart.addCandlestickSeries === "function"
    ? (opts) => chart.addCandlestickSeries(opts)
    : (opts) => chart.addSeries(LightweightCharts.CandlestickSeries, opts);

  const addHistogram = typeof chart.addHistogramSeries === "function"
    ? (opts) => chart.addHistogramSeries(opts)
    : (opts) => chart.addSeries(LightweightCharts.HistogramSeries, opts);

  const candleSeries = addCandle({
    upColor: "#23d18b",
    downColor: "#e34f4f",
    borderVisible: false,
    wickUpColor: "#23d18b",
    wickDownColor: "#e34f4f",
  });

  const volumeSeries = addHistogram({
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
  const series = typeof chart.addLineSeries === "function"
    ? chart.addLineSeries({ color, lineWidth: 1 })
    : chart.addSeries(LightweightCharts.LineSeries, { color, lineWidth: 1 });
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
  const { ok, json } = await apiGet(`/api/stocks/${encodeURIComponent(ticker)}/overview`);
  if (!ok || !json.success) {
    setStatus(statusId, json.error || "Research overview unavailable. This may be a provider outage or unsupported symbol. Retry to use cached data if available.");
    renderMetricGrid(metricId, []);
    return null;
  }

  const d = json.data || {};
  const identity = d.identity || {};
  const market = d.market_data || {};
  const tech = d.technicals || {};
  const freshness = market.freshness || {};
  const providers = d.providers || {};

  const metrics = [
    { label: "Symbol", value: identity.symbol || ticker },
    { label: "Name", value: identity.name || "Unavailable" },
    { label: "Exchange", value: identity.exchange || "Unavailable" },
    { label: "Asset Type", value: identity.asset_type || "Unavailable" },
    { label: "Sector", value: identity.sector || "Unavailable" },
    { label: "Industry", value: identity.industry || "Unavailable" },
    { label: "Current Price", value: market.price !== null && market.price !== undefined ? `$${fmtNumber(market.price)}` : "Unavailable" },
    { label: "Daily Change", value: `${fmtNumber(market.change)} (${fmtNumber(market.change_percent)}%)` },
    { label: "Open / Prev Close", value: `${market.open ?? "--"} / ${market.previous_close ?? "--"}` },
    { label: "High / Low", value: `${market.high ?? "--"} / ${market.low ?? "--"}` },
    { label: "Bid / Ask", value: `${market.bid ?? "--"} / ${market.ask ?? "--"}` },
    { label: "Volume", value: market.volume ?? "Unavailable" },
    { label: "Avg Volume", value: market.average_volume ?? "Unavailable" },
    { label: "Relative Volume", value: market.relative_volume ? `${fmtNumber(market.relative_volume)}x` : "Unavailable" },
    { label: "Market Cap", value: market.market_cap ?? identity.market_cap ?? "Unavailable" },
    { label: "52W High / Low", value: `${market.week_52_high ?? "--"} / ${market.week_52_low ?? "--"}` },
    { label: "Trend", value: tech.trend || "Unavailable" },
    { label: "Momentum", value: tech.momentum ?? "Unavailable" },
    { label: "Volatility", value: tech.volatility ?? "Unavailable" },
    { label: "RSI", value: tech.rsi ?? "Unavailable" },
    { label: "Support / Resistance", value: `${tech.support_levels?.major_support ?? "--"} / ${tech.resistance_levels?.major_resistance ?? "--"}` },
    { label: "Breakout Status", value: tech.breakout_status || "Unavailable" },
    { label: "Gap Status", value: tech.gap_status || "Unavailable" },
    { label: "Data Provider", value: providers.quote_provider || json.provider || "Unavailable" },
    { label: "Status", value: freshness.status || json.data_status || "Unavailable" },
    { label: "Age", value: freshness.age_seconds !== null && freshness.age_seconds !== undefined ? `${freshness.age_seconds}s` : "Unavailable" },
    { label: "Last Updated", value: freshness.last_updated || d.last_updated || "Unavailable" },
  ];

  renderMetricGrid(metricId, metrics);
  setStatus(statusId, `Quote: ${providers.quote_provider || json.provider} | History: ${providers.history_provider || "Unavailable"} | News: ${providers.news_provider || "Unavailable"} | Status: ${freshness.status || json.data_status || "unavailable"} | Updated: ${freshness.last_updated || json.timestamp}`);
  return json;
}

async function loadChart(ticker, timeframe, chartCtx, statusId) {
  setStatus(statusId, "Loading chart...");
  const { ok, json } = await apiGet(`/api/chart/${encodeURIComponent(ticker)}?timeframe=${encodeURIComponent(timeframe)}`);
  if (!ok || !json.success) {
    setStatus(statusId, json.error || "Chart provider timeout. If cache exists, historical candles will be shown on next refresh.");
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

  let quoteStatus = quote.cache_status || quote.source_status || "UNAVAILABLE";
  if ((quote.source_status || "").toUpperCase() === "DELAYED" && (quote.cache_status || "").toUpperCase() === "LIVE") {
    quoteStatus = "CACHED";
  }

  const metricRows = [...metricsHost.querySelectorAll(".metric")];
  metricRows.forEach((row) => {
    const label = row.querySelector(".label")?.textContent || "";
    const value = row.querySelector(".value");
    if (!value) return;
    if (label === "Current Price" && quote.price !== null && quote.price !== undefined) {
      value.textContent = `$${fmtNumber(quote.price)}`;
    }
    if (label === "Status") {
      value.textContent = quoteStatus;
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
    statusEl.textContent = `Provider: ${quote.provider || "Unavailable"} | Status: ${quoteStatus} | Age: ${quote.age_seconds ?? "--"}s | Timestamp: ${quote.timestamp || "unavailable"}`;
  }
}

function handleSocketMarketUpdate(payload) {
  const quote = payload?.quote;
  if (!quote || !quote.symbol) return;
  let streamHint = quote.cache_status || quote.source_status || "UNAVAILABLE";
  if ((quote.source_status || "").toUpperCase() === "DELAYED" && (quote.cache_status || "").toUpperCase() === "LIVE") {
    streamHint = "CACHED";
  }
  updateStreamStatus(streamHint);

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
    state.socket = io({ transports: ["polling"], reconnection: true, reconnectionAttempts: Infinity });
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
  await loadDashboardCommandCenter();

  wireTimeframeButtons(async (timeframe) => {
    await loadChart(rootTicker, timeframe, chartCtx, "dashboardChartStatus");
  });

  startPolling(async () => {
    await loadSnapshot(rootTicker, "dashboardChartStatus", "dashboardSnapshot");
    await refreshMissionSummaryWidgets();
    await loadDashboardCommandCenter();
  }, POLL_INTERVALS.dashboard);
}

async function initStockWorkspace() {
  const ticker = rootTicker;
  const chartCtx = createChart("stockChart");
  if (!chartCtx) return;

  const tvIntervalSelect = document.getElementById("tvInterval");
  const tvReloadButton = document.getElementById("reloadTradingViewChart");
  const tvInterval = tvIntervalSelect ? tvIntervalSelect.value : "60";
  await renderTradingViewChart(ticker, tvInterval);
  if (tvReloadButton) {
    tvReloadButton.addEventListener("click", async () => {
      const selected = tvIntervalSelect ? tvIntervalSelect.value : "60";
      await renderTradingViewChart(ticker, selected);
    });
  }
  if (tvIntervalSelect) {
    tvIntervalSelect.addEventListener("change", async () => {
      await renderTradingViewChart(ticker, tvIntervalSelect.value);
    });
  }

  await loadSnapshot(ticker, "stockDataStatus", "stockMetrics");
  await loadChart(ticker, "1M", chartCtx, "stockChartStatus");
  await loadMissionTimeline(ticker, "stockMissionTimelineStatus", "stockMissionTimelineSummary", "stockMissionTimelineTrack");
  await refreshSignalSource();
  await loadWorkspaceOverlay(ticker);

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
        document.getElementById("tradingViewChart")?.scrollIntoView({ behavior: "smooth" });
        return;
      }

      if (action === "favorite") {
        addFavoriteTicker(ticker);
        renderSearchMemory();
        await loadTickerSuggestions(ticker);
        showToast(`${ticker} added to favorites`, "success");
        return;
      }

      if (action === "watchlist") {
        const payload = {
          ticker_symbol: ticker,
          mission_type: "Research",
          quick_action: "Analyze Everywhere",
          priority: "★★★ Medium",
          assigned_scanners: ["Scanner Center"],
          assigned_brains: ["Confidence Engine"],
          monitoring_flags: ["Monitor Continuously"],
        };
        const { ok, json } = await apiPost("/api/watchlist", payload);
        if (!ok || !json.success) {
          showToast(json.error || "Unable to add watchlist symbol", "error");
          return;
        }
        showToast(`${ticker} added to watchlist`, "success");
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
    await refreshSignalSource();
    await loadWorkspaceOverlay(ticker);
  }, POLL_INTERVALS["stock-workspace"]);
}

async function initScannersPage(enablePolling = true) {
  const active = document.body.dataset.activeScanner || "overview";
  setStatus("scannersStatus", "Loading scanner output...");
  const path = "/api/market/scanners";
  const { ok, json } = await apiGet(path);

  if (!ok || !json.success) {
    setStatus("scannersStatus", json.error || "Market scanner is temporarily unavailable. Showing the last successful snapshot when cache is present.");
    return;
  }

  const data = json.data || {};
  const scanners = data.scanners || {};
  const normalizeName = (value) => String(value || "overview").toLowerCase().replaceAll("-", "_");
  const activeName = normalizeName(active);
  const keyMap = {
    overview: "plutotrade_high_confidence",
    movers: "top_gainers",
    volume: "unusual_relative_volume",
    reversals: "reversal_candidates",
    breakouts: "breakout_candidates",
    "extended_hours": "most_active",
    futures: "high_volatility",
    "sector_rotation": "near_resistance",
    watchlist: "plutotrade_high_confidence",
    upcoming: "momentum_leaders",
  };
  const activeScannerKey = keyMap[activeName] || "plutotrade_high_confidence";
  const rows = active === "overview" ? (scanners.plutotrade_high_confidence || []) : (scanners[activeScannerKey] || []);

  setStatus("scannersStatus", `${active} | Provider: ${json.provider || "Unavailable"} | Status: ${data.freshness?.status || json.data_status || "unavailable"} | Last scan: ${data.generated_at || "unavailable"}`);

  const summaryTarget = document.getElementById("scannersSummary");
  if (summaryTarget) {
    summaryTarget.innerHTML = "";
    [
      ["Universe Limit", data.universe?.configured_limit],
      ["Snapshot Symbols", data.universe?.snapshot_size],
      ["Freshness", data.freshness?.status],
      ["Refresh Age", data.freshness?.age_seconds !== undefined ? `${data.freshness.age_seconds}s` : "Unavailable"],
      ["Last Scan", data.generated_at],
    ].forEach(([label, value]) => {
      if (!value) return;
      const row = document.createElement("div");
      row.className = "detail-item";
      row.innerHTML = `<strong>${label}</strong>: ${value}`;
      summaryTarget.appendChild(row);
    });
  }

  const body = document.querySelector("#scannersTable tbody");
  if (!body) return;

  body.innerHTML = "";
  if (!rows.length) {
    setStatus("scannersStatus", `${active}: No stocks currently match this scanner's thresholds.`);
  }
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><a href="/stock/${encodeURIComponent(row.symbol || row.ticker || "")}">${row.symbol || row.ticker || "-"}</a></td>
      <td>${row.price ?? "-"}</td>
      <td>${row.percent_change ?? "-"}</td>
      <td>${row.relative_volume ?? "-"}</td>
      <td>${row.confidence ?? row.scanner_score ?? "-"}</td>
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
    setStatus("brainsStatus", json.error || "AI brain output is unavailable right now. Quote and technical layers may still be healthy.");
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
    setStatus("futuresStatus", json.error || "Futures research is unavailable at the moment. Retry to refresh provider connectivity.");
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
    setStatus("missionControlStatus", json.error || "Mission profiles are unavailable. Stored mission data may still be recoverable from session cache.");
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
  syncRuntimeConfig();
  bindKeyboardShortcuts();
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
  if (pageId === "alerts") {
    initAlertsPage();
  }
  if (pageId === "portfolio") {
    loadPortfolioOverview();
    startPolling(() => loadPortfolioOverview(), POLL_INTERVALS.portfolio);
  }
  if (pageId === "paper-trading") {
    initPaperTradingPage();
  }
  if (pageId === "performance") {
    initPerformancePage();
  }
}

init();
