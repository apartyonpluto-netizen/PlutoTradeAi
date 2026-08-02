const onReady = (callback) => {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", callback);
    return;
  }
  callback();
};

const escapeHtml = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");

const ensureToastContainer = () => {
  let container = document.getElementById("toastContainer");
  if (container) return container;
  container = document.createElement("div");
  container.id = "toastContainer";
  container.className = "toast-container";
  document.body.appendChild(container);
  return container;
};

const showToast = (message, tone = "info") => {
  const container = ensureToastContainer();
  const toast = document.createElement("div");
  toast.className = `toast toast-${tone}`;
  toast.textContent = message;
  container.appendChild(toast);
  window.setTimeout(() => {
    toast.remove();
    if (!container.children.length) container.remove();
  }, 3200);
};

const seenMissionAlertIds = new Set();

const ensureMissionAlertContainer = () => {
  let container = document.getElementById("missionAlertContainer");
  if (container) return container;
  container = document.createElement("div");
  container.id = "missionAlertContainer";
  container.className = "mission-alert-container";
  document.body.appendChild(container);
  return container;
};

const showMissionAlert = (alert) => {
  if (!alert?.id || seenMissionAlertIds.has(alert.id)) return;
  seenMissionAlertIds.add(alert.id);
  const container = ensureMissionAlertContainer();
  const card = document.createElement("article");
  card.className = "mission-alert-card";
  card.innerHTML = `
    <small>Mission Alert</small>
    <b>${escapeHtml(alert.ticker || "MARKET")} ${escapeHtml(alert.message || "")}</b>
    <span>${escapeHtml(alert.category || "System")}</span>
  `;
  container.appendChild(card);
  window.setTimeout(() => {
    card.remove();
    if (!container.children.length) container.remove();
  }, 6000);
};

const requestJson = async (url, options = {}) => {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  const envelopeError =
    payload && payload.error && typeof payload.error === "object" ? payload.error.message : payload.error;
  if (!response.ok || payload.ok === false || payload.success === false) {
    throw new Error(envelopeError || "Request failed.");
  }
  if (payload && payload.data && typeof payload.data === "object") {
    return { ...payload.data, ...payload };
  }
  return payload || {};
};

const setAlertCounts = (unreadCount) => {
  const countNode = document.getElementById("alertCount");
  const sidebarCountNode = document.getElementById("systemAlertCount");
  if (countNode) countNode.textContent = String(unreadCount);
  if (sidebarCountNode) sidebarCountNode.textContent = String(unreadCount);
};

const renderAlertList = (listNode, alerts = []) => {
  if (!listNode) return;
  if (!alerts.length) {
    listNode.innerHTML = '<p class="muted">No active alerts.</p>';
    return;
  }
  listNode.innerHTML = alerts
    .map(
      (alert) => `
      <div class="alert-row ${alert.read ? "alert-read" : ""}" data-alert-id="${escapeHtml(alert.id)}">
        <div>
          <b>${escapeHtml(alert.category || alert.type || "System")}</b>
          <p>${escapeHtml(alert.ticker || "")} ${escapeHtml(alert.message || "")}</p>
        </div>
        <div class="action-row">
          ${alert.read ? "" : `<button class="ghost-button mark-read-alert" type="button" data-alert-id="${escapeHtml(alert.id)}">Read</button>`}
          <button class="ghost-button dismiss-alert" type="button" data-alert-id="${escapeHtml(alert.id)}">Dismiss</button>
        </div>
      </div>`
    )
    .join("");
};

const bindAlertDrawer = () => {
  const drawer = document.getElementById("alertDrawer");
  const openBtn = document.getElementById("alertDrawerToggle");
  const closeBtn = document.getElementById("closeAlertDrawer");
  const markAllBtn = document.getElementById("markAllReadButton");
  const list = document.getElementById("alertList");
  if (!drawer || !openBtn || !closeBtn || !list) return;

  const refreshAlerts = async () => {
    const payload = await requestJson("/api/alerts");
    renderAlertList(list, payload.alerts || []);
    setAlertCounts(payload.unread_count ?? 0);
  };

  const sendAlertAction = async (action, id = "") => {
    const payload = await requestJson("/api/alerts", {
      method: "POST",
      body: JSON.stringify({ action, id }),
    });
    renderAlertList(list, payload.alerts || []);
    setAlertCounts(payload.unread_count ?? 0);
  };

  openBtn.addEventListener("click", () => {
    drawer.classList.add("open");
    refreshAlerts().catch((error) => showToast(error.message, "error"));
  });
  closeBtn.addEventListener("click", () => drawer.classList.remove("open"));

  drawer.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const alertId = target.dataset.alertId || "";
    try {
      if (target.classList.contains("dismiss-alert")) {
        await sendAlertAction("dismiss", alertId);
      } else if (target.classList.contains("mark-read-alert")) {
        await sendAlertAction("mark_read", alertId);
      } else {
        return;
      }
      showToast("Notification updated.", "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  if (markAllBtn instanceof HTMLButtonElement) {
    markAllBtn.addEventListener("click", async () => {
      try {
        await sendAlertAction("mark_all_read");
        showToast("All notifications marked read.", "success");
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }

  const manualAlertForm = document.getElementById("manualAlertForm");
  if (manualAlertForm instanceof HTMLFormElement) {
    manualAlertForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = new FormData(manualAlertForm);
      try {
        const payload = await requestJson("/api/alerts", {
          method: "POST",
          body: JSON.stringify({
            action: "add",
            type: "manual",
            ticker: (form.get("ticker") || "").toString(),
            message: (form.get("message") || "").toString(),
          }),
        });
        manualAlertForm.reset();
        renderAlertList(list, payload.alerts || []);
        setAlertCounts(payload.unread_count ?? 0);
        showToast("Alert added.", "success");
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }

  window.setInterval(() => {
    refreshAlerts().catch(() => {});
  }, 30000);
};

const buildWatchlistRow = (item) => `
  <tr data-ticker="${escapeHtml(item.ticker)}">
    <td>${escapeHtml(item.ticker)}</td>
    <td><input type="text" value="${escapeHtml(item.category)}" data-field="category"></td>
    <td><input type="text" value="${escapeHtml(item.status)}" data-field="status"></td>
    <td><input type="number" value="${escapeHtml(item.ai_score)}" data-field="ai_score"></td>
    <td><input type="text" value="${escapeHtml(item.notes)}" data-field="notes"></td>
    <td class="action-row">
      <button class="save-watchlist" type="button">Save</button>
      <button class="delete-watchlist ghost-button" type="button">Delete</button>
    </td>
  </tr>`;

const bindWatchlistPage = () => {
  const form = document.getElementById("watchlistForm");
  const table = document.getElementById("watchlistTable");
  if (!(form instanceof HTMLFormElement) || !table) return;
  const tbody = table.querySelector("tbody");
  if (!(tbody instanceof HTMLElement)) return;
  const controls = document.getElementById("watchlistControls");
  const applyBtn = document.getElementById("applyWatchlistFilters");
  const countNode = document.getElementById("watchlistCount");
  const suggestionsList = document.getElementById("suggestionsList");

  const renderRows = (rows) => {
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="muted">No watchlist tickers match current filters.</td></tr>';
    } else {
      tbody.innerHTML = rows.map((item) => buildWatchlistRow(item)).join("");
    }
    if (countNode) countNode.textContent = `${rows.length} entries`;
  };

  const loadRows = async () => {
    if (!(controls instanceof HTMLFormElement)) return;
    const params = new URLSearchParams(new FormData(controls));
    const payload = await requestJson(`/api/watchlist?${params.toString()}`);
    const rows = payload.watchlist || [];
    renderRows(rows);
    document.dispatchEvent(
      new CustomEvent("watchlist:changed", {
        detail: {
          rows,
          tickers: rows.map((item) => String(item.ticker || "").toUpperCase()).filter(Boolean),
        },
      })
    );
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    try {
      await requestJson("/api/watchlist/add", {
        method: "POST",
        body: JSON.stringify({
          ticker: data.get("ticker"),
          category: data.get("category"),
          status: data.get("status"),
          ai_score: data.get("ai_score"),
          notes: data.get("notes"),
        }),
      });
      form.reset();
      await loadRows();
      showToast("Ticker added to watchlist.", "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  table.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const row = target.closest("tr[data-ticker]");
    if (!(row instanceof HTMLElement)) return;
    const ticker = row.dataset.ticker || "";

    if (target.classList.contains("save-watchlist")) {
      const payload = { ticker };
      row.querySelectorAll("input[data-field]").forEach((input) => {
        if (input instanceof HTMLInputElement) payload[input.dataset.field] = input.value;
      });
      try {
        await requestJson("/api/watchlist/update", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        showToast(`${ticker} updated.`, "success");
      } catch (error) {
        showToast(error.message, "error");
      }
    }

    if (target.classList.contains("delete-watchlist")) {
      try {
        await requestJson("/api/watchlist/delete", {
          method: "POST",
          body: JSON.stringify({ ticker }),
        });
        await loadRows();
        showToast(`${ticker} removed.`, "success");
      } catch (error) {
        showToast(error.message, "error");
      }
    }
  });

  if (applyBtn instanceof HTMLButtonElement) {
    applyBtn.addEventListener("click", () => {
      loadRows().catch((error) => showToast(error.message, "error"));
    });
  }

  if (suggestionsList instanceof HTMLElement) {
    suggestionsList.addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const row = target.closest("[data-suggestion-ticker]");
      if (!(row instanceof HTMLElement)) return;
      const ticker = row.dataset.suggestionTicker || "";
      if (target.classList.contains("dismiss-suggestion")) {
        row.remove();
        return;
      }
      if (!target.classList.contains("add-suggestion-to-watchlist")) return;
      try {
        await requestJson("/api/watchlist/add", {
          method: "POST",
          body: JSON.stringify({
            ticker,
            category: "Scanner",
            status: "Candidate",
            ai_score: "65",
            notes: "Added from scanner suggestions.",
          }),
        });
        row.remove();
        await loadRows();
        showToast(`${ticker} added to watchlist.`, "success");
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }
};

const bindScannerPage = () => {
  const button = document.getElementById("refreshScannerButton");
  const table = document.getElementById("scannerTable");
  const loading = document.getElementById("scannerLoadingState");
  if (!(button instanceof HTMLButtonElement) || !table) return;

  const refresh = async (forceRefresh) => {
    button.disabled = true;
    button.textContent = forceRefresh ? "Refreshing..." : "Updating...";
    if (loading instanceof HTMLElement) loading.hidden = false;
    try {
      const payload = await requestJson(`/api/scanner?refresh=${forceRefresh ? "true" : "false"}`);
      const tbody = table.querySelector("tbody");
      if (!(tbody instanceof HTMLElement)) return;
      tbody.innerHTML = (payload.rows || [])
        .map(
          (row) => `
          <tr>
            <td>${escapeHtml(row.ticker)}</td>
            <td>$${escapeHtml(row.price)}</td>
            <td class="${row.percent_change >= 0 ? "tone-positive" : "tone-caution"}">${escapeHtml(row.percent_change)}%</td>
            <td>${escapeHtml(row.relative_volume)}x</td>
            <td>${escapeHtml(row.volume)}</td>
            <td>${escapeHtml(row.scanner_score)}</td>
            <td>${escapeHtml(row.last_updated)}</td>
            <td>${row.on_watchlist ? "Tracked" : "Not tracked"}</td>
          </tr>`
        )
        .join("");
      const stamp = document.getElementById("scannerUpdatedAt");
      if (stamp) stamp.textContent = payload.last_updated || "";
      const errors = document.getElementById("scannerErrors");
      if (errors instanceof HTMLElement) {
        errors.innerHTML = payload.errors?.length ? `<p class="error">${payload.errors.join(" | ")}</p>` : "";
      }
      const liveBadge = document.getElementById("scannerLiveBadge");
      if (liveBadge) liveBadge.textContent = `Live updates every 20s · ${new Date().toLocaleTimeString()}`;
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      if (loading instanceof HTMLElement) loading.hidden = true;
      button.disabled = false;
      button.textContent = "Refresh";
    }
  };

  button.addEventListener("click", () => {
    refresh(true).catch(() => {});
  });
  window.setInterval(() => {
    refresh(false).catch(() => {});
  }, 20000);
};

const parseTickerList = (rawValue) =>
  Array.from(
    new Set(
      String(rawValue || "")
        .split(",")
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean)
    )
  );

const formatChartValue = (value) => {
  if (value === null || value === undefined || value === "") return "Data unavailable";
  if (typeof value === "number") return value.toFixed(2);
  if (typeof value === "object") {
    if ("low" in value && "high" in value) return `${value.low} - ${value.high}`;
    return "Data unavailable";
  }
  return String(value);
};

const renderChartMarksCard = (levels) => {
  const ticker = levels?.ticker || "";
  if (!levels || levels.insufficient_data || levels.status === "insufficient data") {
    return `
    <article class="ai-chart-mark-card">
      <div class="panel-head"><h3>${escapeHtml(ticker)}</h3><span>Insufficient data</span></div>
      <p class="muted">${escapeHtml(levels?.reason || "No reliable OHLCV dataset returned.")}</p>
    </article>
    `;
  }

  const resistance = levels.major_resistance_levels?.[0] ?? levels.breakout_level;
  const support = levels.major_support_levels?.[0] ?? levels.breakdown_level;
  const reversalZone = levels.reversal_zone ? `${levels.reversal_zone.low} - ${levels.reversal_zone.high}` : "Data unavailable";
  const invalidation = `Below ${formatChartValue(levels.breakdown_level)} / Above ${formatChartValue(levels.breakout_level)}`;

  return `
    <article class="ai-chart-mark-card">
    <div class="panel-head"><h3>${escapeHtml(ticker)}</h3><span>${escapeHtml(levels.generated_at || "")}</span></div>
    <div class="ai-chart-mark-grid">
      <div><small>Mark Resistance</small><b>${escapeHtml(formatChartValue(resistance))}</b></div>
      <div><small>Mark Support</small><b>${escapeHtml(formatChartValue(support))}</b></div>
      <div><small>Watch Breakout</small><b>${escapeHtml(formatChartValue(levels.breakout_level))}</b></div>
      <div><small>Watch Breakdown</small><b>${escapeHtml(formatChartValue(levels.breakdown_level))}</b></div>
      <div><small>Reversal zone</small><b>${escapeHtml(reversalZone)}</b></div>
      <div><small>Invalidation below/above</small><b>${escapeHtml(invalidation)}</b></div>
    </div>
    </article>
  `;
};

const bindAiChartMarks = () => {
  const container = document.querySelector("[data-ai-chart-marks]");
  if (!(container instanceof HTMLElement)) return;

  const refresh = async (tickersOverride = null) => {
    const tickers = Array.isArray(tickersOverride) ? tickersOverride : parseTickerList(container.dataset.tickers || "");
    if (!tickers.length) {
    container.innerHTML = '<p class="muted">No watchlist tickers available for chart marks yet.</p>';
    return;
    }
    const params = new URLSearchParams();
    params.set("tickers", tickers.join(","));
    container.innerHTML = '<p class="muted">Loading AI chart marks...</p>';
    const payload = await requestJson(`/api/chart-levels/watchlist?${params.toString()}`);
    const rows = payload.rows || [];
    if (!rows.length) {
    container.innerHTML = '<p class="muted">No chart marks available.</p>';
    return;
    }
    container.innerHTML = rows.map((item) => renderChartMarksCard(item)).join("");
  };

  refresh(null).catch((error) => {
    container.innerHTML = `<p class="error">${escapeHtml(error.message)}</p>`;
  });
  document.addEventListener("watchlist:changed", (event) => {
    const tickers = Array.isArray(event?.detail?.tickers) ? event.detail.tickers : [];
    container.dataset.tickers = tickers.join(",");
    refresh(tickers).catch(() => {});
  });
};

const renderOptionsCard = (suggestion) => {
  const expirations = Array.isArray(suggestion.expirations) ? suggestion.expirations : [];
  const directionClass =
    suggestion.suggested_direction === "CALL"
      ? "tone-positive"
      : suggestion.suggested_direction === "PUT"
        ? "tone-caution"
        : "";

  const expirationMarkup = expirations.length
    ? expirations
        .map(
          (item) => `
          <div class="options-expiration-card">
            <b>${escapeHtml(item.timeframe || "Expiration")}</b>
            <div class="options-field-grid">
              <div><small>Date</small><span>${escapeHtml(item.expiration_date || "Data unavailable")}</span></div>
              <div><small>Contract</small><span>${escapeHtml(item.suggested_contract_type || "Data unavailable")}</span></div>
              <div><small>Strike Area</small><span>${escapeHtml(item.suggested_strike_area || "Data unavailable")}</span></div>
              <div><small>Premium</small><span>${escapeHtml(item.estimated_option_premium || "Data unavailable")}</span></div>
              <div><small>Break-even</small><span>${escapeHtml(item.break_even_price || "Data unavailable")}</span></div>
            </div>
            <p><small>Risk warning:</small> ${escapeHtml(item.risk_warning || "Data unavailable")}</p>
            <p><small>Why selected:</small> ${escapeHtml(item.selection_reason || "Data unavailable")}</p>
          </div>`
        )
        .join("")
    : '<p class="muted">Data unavailable</p>';

  return `
    <article class="options-suggestion-card">
      <div class="panel-head">
        <h3>${escapeHtml(suggestion.ticker || "")}</h3>
        <span class="${directionClass}">AI favors: ${escapeHtml(suggestion.suggested_direction || "WAIT")}</span>
      </div>
      <p><b>Confidence:</b> ${escapeHtml(suggestion.confidence_score ?? "0")}%</p>
      <p><b>Reason:</b> ${escapeHtml(suggestion.reason_for_direction || "Data unavailable")}</p>
      <div class="options-field-grid">
        <div><small>Current stock price</small><span>${escapeHtml(suggestion.current_stock_price || "Data unavailable")}</span></div>
        <div><small>Expected move</small><span>${escapeHtml(suggestion.expected_move || "Data unavailable")}</span></div>
        <div><small>Key support</small><span>${escapeHtml(suggestion.key_support || "Data unavailable")}</span></div>
        <div><small>Key resistance</small><span>${escapeHtml(suggestion.key_resistance || "Data unavailable")}</span></div>
        <div><small>Breakout price</small><span>${escapeHtml(suggestion.breakout_price || "Data unavailable")}</span></div>
        <div><small>Breakdown price</small><span>${escapeHtml(suggestion.breakdown_price || "Data unavailable")}</span></div>
        <div><small>Risk level</small><span>${escapeHtml(suggestion.risk_level || "Data unavailable")}</span></div>
      </div>
      <div class="options-expiration-grid">${expirationMarkup}</div>
      <p class="muted">${escapeHtml(suggestion.disclaimer || "For research only. Not financial advice.")}</p>
    </article>
  `;
};

const bindOptionsSuggestions = () => {
  const containers = Array.from(document.querySelectorAll("[data-options-suggestions]"));
  if (!containers.length) return;

  containers.forEach(async (container) => {
    if (!(container instanceof HTMLElement)) return;
    const tickers = parseTickerList(container.dataset.tickers || "");
    if (!tickers.length) {
      container.innerHTML = '<p class="muted">No tickers available for options suggestions yet.</p>';
      return;
    }

    container.innerHTML = '<p class="muted">Loading options suggestions...</p>';
    const suggestions = await Promise.all(
      tickers.map(async (ticker) => {
        try {
          const payload = await requestJson(`/api/options/${encodeURIComponent(ticker)}`);
          return payload.options || null;
        } catch (error) {
          return {
            ticker,
            suggested_direction: "WAIT",
            confidence_score: 0,
            reason_for_direction:
              "AI favors WAIT because options chain data is unavailable. Watch for a possible setup and wait for confirmation.",
            current_stock_price: "Data unavailable",
            expected_move: "Data unavailable",
            key_support: "Data unavailable",
            key_resistance: "Data unavailable",
            breakout_price: "Data unavailable",
            breakdown_price: "Data unavailable",
            risk_level: "Data unavailable",
            expirations: [],
            disclaimer: "For research only. Not financial advice.",
          };
        }
      })
    );

    const readySuggestions = suggestions.filter(Boolean);
    if (!readySuggestions.length) {
      container.innerHTML = '<p class="muted">Data unavailable.</p>';
      return;
    }
    container.innerHTML = readySuggestions.map((item) => renderOptionsCard(item)).join("");
  });
};

const bindSettingsPage = () => {
  const trustedForm = document.getElementById("trustedAccountForm");
  const settingsForm = document.getElementById("platformSettingsForm");
  const trustedList = document.getElementById("trustedAccountsList");
  const missionResetButton = document.getElementById("manualMissionBriefResetButton");

  if (trustedForm instanceof HTMLFormElement) {
    trustedForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(trustedForm);
      try {
        await requestJson("/api/trusted-accounts", {
          method: "POST",
          body: JSON.stringify({ username: data.get("username") }),
        });
        showToast("Trusted account added.", "success");
        window.location.reload();
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }

  if (trustedList instanceof HTMLElement) {
    trustedList.addEventListener("click", async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || !target.classList.contains("remove-trusted-account")) return;
      try {
        await requestJson("/api/trusted-accounts", {
          method: "DELETE",
          body: JSON.stringify({ username: target.dataset.username }),
        });
        target.closest(".alert-row")?.remove();
        showToast("Trusted account removed.", "success");
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }

  if (settingsForm instanceof HTMLFormElement) {
    settingsForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = new FormData(settingsForm);
      try {
        await requestJson("/api/settings", {
          method: "POST",
          body: JSON.stringify({
            theme: data.get("theme"),
            ai_confidence_threshold: Number(data.get("ai_confidence_threshold")),
            scanner_frequency_seconds: Number(data.get("scanner_frequency_seconds")),
            market_hours: data.get("market_hours"),
            paper_trading_enabled: data.get("paper_trading_enabled") === "on",
            auto_suggestions_enabled: data.get("auto_suggestions_enabled") === "on",
            show_mission_brief_again: data.get("show_mission_brief_again") === "on",
          }),
        });
        showToast("Settings saved.", "success");
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }

  if (missionResetButton instanceof HTMLButtonElement) {
    missionResetButton.addEventListener("click", async () => {
      try {
        await requestJson("/api/mission-brief/reset", { method: "POST", body: JSON.stringify({}) });
        showToast("Mission Brief will show again.", "success");
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }
};

const asDisplayValue = (value) => {
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) return value.join(", ");
  if (!value) return "Never";
  return String(value);
};

const statusLightClass = (platform, status) => {
  const statusValue = (status || "").toLowerCase();
  if (statusValue === "connected") return "status-green";
  if (platform === "etrade" && statusValue === "sandbox") return "status-yellow";
  if (platform === "webull" && statusValue === "paper mode") return "status-yellow";
  if (platform === "tradingview" && statusValue === "webhook ready") return "status-yellow";
  return "status-red";
};

const bindAccountHubPage = () => {
  const cards = Array.from(document.querySelectorAll(".account-card"));
  if (!cards.length) return;

  const renderCard = (card, account) => {
    if (!(card instanceof HTMLElement) || !account) return;
    const platform = card.dataset.platform || "";
    const statusNode = card.querySelector('[data-account-field="status"]');
    if (statusNode) statusNode.textContent = asDisplayValue(account.status);
    card.querySelectorAll("[data-account-field]").forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      const key = node.dataset.accountField;
      if (!key || key === "status") return;
      if (key === "webhook_url" && account[key]) {
        node.textContent = account[key].startsWith("/") ? `${window.location.origin}${account[key]}` : account[key];
        return;
      }
      node.textContent = asDisplayValue(account[key]);
    });
    const light = card.querySelector(".status-light");
    if (light instanceof HTMLElement) {
      light.classList.remove("status-green", "status-yellow", "status-red");
      light.classList.add(statusLightClass(platform, account.status));
    }
    const toggle = card.querySelector(".trading-toggle");
    if (toggle instanceof HTMLInputElement) toggle.checked = Boolean(account.trading_enabled);
  };

  const sendAction = async (platform, path, payload = {}) => {
    const response = await requestJson(path, {
      method: "POST",
      body: JSON.stringify({ platform, ...payload }),
    });
    return response.account;
  };

  cards.forEach((card) => {
    if (!(card instanceof HTMLElement)) return;
    const platform = card.dataset.platform || "";
    if (!platform) return;

    card.addEventListener("click", async (event) => {
      const button = event.target instanceof HTMLElement ? event.target.closest("button") : null;
      if (!(button instanceof HTMLButtonElement)) return;
      button.disabled = true;
      const originalText = button.textContent;
      button.textContent = "Working...";
      try {
        let account = null;
        if (button.classList.contains("account-connect")) account = await sendAction(platform, "/api/accounts/connect");
        else if (button.classList.contains("account-disconnect"))
          account = await sendAction(platform, "/api/accounts/disconnect");
        else if (button.classList.contains("account-test")) account = await sendAction(platform, "/api/accounts/test");
        else if (button.classList.contains("generate-webhook"))
          account = await sendAction(platform, "/api/accounts/connect", { action: "generate_webhook" });
        if (account) {
          renderCard(card, account);
          showToast(`${platform.toUpperCase()} updated.`, "success");
        }
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        button.disabled = false;
        button.textContent = originalText;
      }
    });

    const tradingToggle = card.querySelector(".trading-toggle");
    if (tradingToggle instanceof HTMLInputElement) {
      tradingToggle.addEventListener("change", async () => {
        const previousValue = !tradingToggle.checked;
        try {
          const account = await sendAction(platform, "/api/accounts/connect", {
            trading_enabled: tradingToggle.checked,
          });
          renderCard(card, account);
          showToast("Trading preference updated.", "success");
        } catch (error) {
          tradingToggle.checked = previousValue;
          showToast(error.message, "error");
        }
      });
    }
  });

  const modal = document.getElementById("setupModal");
  if (!(modal instanceof HTMLElement)) return;
  const closeModal = () => {
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  };
  document.querySelectorAll(".open-setup-modal").forEach((button) => {
    button.addEventListener("click", () => {
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
    });
  });
  modal.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });
  const closeButton = modal.querySelector(".close-setup-modal");
  if (closeButton instanceof HTMLButtonElement) closeButton.addEventListener("click", closeModal);
};

const bindAutonomyControls = () => {
  const root = document.querySelector("[data-autonomy-control]");
  if (!(root instanceof HTMLElement)) return;
  const modeSelector = document.getElementById("autonomyModeSelector");
  const reasonInput = document.getElementById("autonomyModeReason");
  const setModeBtn = document.getElementById("setAutonomyModeButton");
  const stopBtn = document.getElementById("autonomyEmergencyStopButton");
  const resetBtn = document.getElementById("autonomyResetStopButton");
  const modal = document.getElementById("autonomyWarningModal");
  const confirmBtn = document.getElementById("confirmAutonomyModeButton");

  let pendingMode = "";

  const refresh = async () => {
    const payload = await requestJson("/api/autonomy/status");
    root.querySelectorAll("[data-autonomy-field]").forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      const key = node.dataset.autonomyField || "";
      const value = payload[key];
      if (typeof value === "boolean") node.textContent = value ? "Yes" : "No";
      else node.textContent = String(value ?? "n/a");
    });
  };

  if (setModeBtn instanceof HTMLButtonElement && modeSelector instanceof HTMLSelectElement && modal instanceof HTMLElement) {
    setModeBtn.addEventListener("click", () => {
      pendingMode = modeSelector.value;
      modal.classList.add("open");
      modal.setAttribute("aria-hidden", "false");
    });
  }

  if (confirmBtn instanceof HTMLButtonElement && modeSelector instanceof HTMLSelectElement) {
    confirmBtn.addEventListener("click", async () => {
      try {
        await requestJson("/api/autonomy/set-mode", {
          method: "POST",
          body: JSON.stringify({
            mode: pendingMode || modeSelector.value,
            mode_change_reason: reasonInput instanceof HTMLInputElement ? reasonInput.value : "",
          }),
        });
        showToast("Autonomy mode updated.", "success");
        if (modal instanceof HTMLElement) {
          modal.classList.remove("open");
          modal.setAttribute("aria-hidden", "true");
        }
        await refresh();
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }

  if (stopBtn instanceof HTMLButtonElement) {
    stopBtn.addEventListener("click", async () => {
      try {
        await requestJson("/api/autonomy/emergency-stop", { method: "POST", body: JSON.stringify({}) });
        await refresh();
        showToast("Emergency stop enabled.", "error");
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }
  if (resetBtn instanceof HTMLButtonElement) {
    resetBtn.addEventListener("click", async () => {
      try {
        await requestJson("/api/autonomy/reset-stop", { method: "POST", body: JSON.stringify({}) });
        await refresh();
        showToast("Emergency stop reset.", "success");
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }

  document.querySelectorAll(".close-autonomy-modal").forEach((button) => {
    button.addEventListener("click", () => {
      if (modal instanceof HTMLElement) {
        modal.classList.remove("open");
        modal.setAttribute("aria-hidden", "true");
      }
    });
  });
  refresh().catch(() => {});
};

const bindMissionAlertFloater = () => {
  const overlay = document.getElementById("missionBriefOverlay");
  const shouldFloat = !overlay || overlay.classList.contains("dismissed");
  if (!shouldFloat) return;

  const refresh = async () => {
    const payload = await requestJson("/api/alerts");
    const alerts = Array.isArray(payload.alerts) ? payload.alerts : [];
    alerts
      .filter((item) => {
        const type = String(item.type || "").toLowerCase();
        return ["mission-alert", "breakout-alert", "volume-spike", "tradingview-alert", "economic-event"].includes(type);
      })
      .slice(0, 2)
      .forEach(showMissionAlert);
  };

  refresh().catch(() => {});
  window.setInterval(() => {
    refresh().catch(() => {});
  }, 25000);
};

const bindNotificationsPage = () => {
  const list = document.getElementById("notificationsAlertList");
  if (!(list instanceof HTMLElement)) return;
  const markAll = document.getElementById("notificationsMarkAllRead");

  const reload = async () => {
    const payload = await requestJson("/api/alerts");
    renderAlertList(list, payload.alerts || []);
    setAlertCounts(payload.unread_count ?? 0);
  };

  if (markAll instanceof HTMLButtonElement) {
    markAll.addEventListener("click", async () => {
      try {
        await requestJson("/api/alerts", { method: "POST", body: JSON.stringify({ action: "mark_all_read" }) });
        await reload();
      } catch (error) {
        showToast(error.message, "error");
      }
    });
  }

  list.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const alertId = target.dataset.alertId || "";
    try {
      if (target.classList.contains("dismiss-alert")) {
        await requestJson("/api/alerts", { method: "POST", body: JSON.stringify({ action: "dismiss", id: alertId }) });
      } else if (target.classList.contains("mark-read-alert")) {
        await requestJson("/api/alerts", { method: "POST", body: JSON.stringify({ action: "mark_read", id: alertId }) });
      } else {
        return;
      }
      await reload();
    } catch (error) {
      showToast(error.message, "error");
    }
  });
};

const bindMissionControlEffects = () => {
  const counters = Array.from(document.querySelectorAll("[data-count-to]"));
  counters.forEach((counter) => {
    if (!(counter instanceof HTMLElement)) return;
    const target = Number(counter.dataset.countTo || "0");
    if (!Number.isFinite(target) || target <= 0) return;
    const hasPercent = counter.textContent?.includes("%");
    const durationMs = 1200;
    const startAt = performance.now();

    const tick = (now) => {
      const progress = Math.min((now - startAt) / durationMs, 1);
      const value = Math.round(target * progress);
      counter.textContent = `${value}${hasPercent ? "%" : ""}`;
      if (progress < 1) window.requestAnimationFrame(tick);
    };
    window.requestAnimationFrame(tick);
  });
};

const bindLiveDataStatusCard = () => {
  const providerNode = document.getElementById("liveDataProvider");
  const connectionNode = document.getElementById("liveDataConnectionStatus");
  const updatedNode = document.getElementById("liveDataLastUpdate");
  const latencyNode = document.getElementById("liveDataApiLatency");
  const symbolsNode = document.getElementById("liveDataSymbolsLoaded");
  const marketSessionNode = document.getElementById("liveDataMarketSession");
  const refreshButton = document.getElementById("liveDataRefreshButton");
  if (
    !(providerNode instanceof HTMLElement) ||
    !(connectionNode instanceof HTMLElement) ||
    !(updatedNode instanceof HTMLElement) ||
    !(latencyNode instanceof HTMLElement) ||
    !(symbolsNode instanceof HTMLElement) ||
    !(marketSessionNode instanceof HTMLElement)
  ) {
    return;
  }

  const setConnectionStatus = (status) => {
    connectionNode.textContent = status;
    connectionNode.classList.remove("tone-positive", "tone-caution");
    connectionNode.classList.add(status === "🟢 Connected" ? "tone-positive" : "tone-caution");
  };

  const setOfflineState = (latencyMs) => {
    providerNode.textContent = "Yahoo Finance";
    setConnectionStatus("🔴 Offline");
    updatedNode.textContent = "Unavailable";
    latencyNode.textContent = `${Math.max(0, Math.round(latencyMs))} ms`;
    symbolsNode.textContent = "0";
    marketSessionNode.textContent = "Unknown";
  };

  const refresh = async (forceRefresh = false) => {
    const startedAt = performance.now();
    if (refreshButton instanceof HTMLButtonElement) {
      refreshButton.disabled = true;
      refreshButton.textContent = forceRefresh ? "Refreshing..." : "Updating...";
    }

    try {
      const payload = await requestJson(`/api/live-data-status?refresh=${forceRefresh ? "true" : "false"}`);
      const latencyMs = performance.now() - startedAt;
      providerNode.textContent = payload.provider || "Yahoo Finance";
      setConnectionStatus(payload.connection_status || "🔴 Offline");
      updatedNode.textContent = payload.last_update_time || "Never";
      latencyNode.textContent = `${Math.max(0, Math.round(latencyMs))} ms`;
      symbolsNode.textContent = String(payload.symbols_loaded ?? 0);
      marketSessionNode.textContent = payload.market_session || "Unknown";
    } catch (_error) {
      setOfflineState(performance.now() - startedAt);
    } finally {
      if (refreshButton instanceof HTMLButtonElement) {
        refreshButton.disabled = false;
        refreshButton.textContent = "Refresh";
      }
    }
  };

  if (refreshButton instanceof HTMLButtonElement) {
    refreshButton.addEventListener("click", () => {
      refresh(true).catch(() => {});
    });
  }

  refresh(false).catch(() => {});
  window.setInterval(() => {
    refresh(false).catch(() => {});
  }, 30000);
};

const buildPaperTradeRow = (trade) => `
  <tr data-trade-id="${escapeHtml(trade.id)}">
    <td>${escapeHtml(trade.ticker)}</td>
    <td>${escapeHtml(trade.direction)}</td>
    <td>${escapeHtml(trade.quantity)}</td>
    <td>$${escapeHtml(trade.entry_price)}</td>
    <td>${trade.exit_price ? `$${escapeHtml(trade.exit_price)}` : "—"}</td>
    <td>${trade.pnl === "" || trade.pnl === undefined || trade.pnl === null ? "—" : escapeHtml(trade.pnl)}</td>
    <td>${escapeHtml(trade.status)}</td>
    <td class="action-row">${trade.status === "Open" ? '<button class="close-paper-trade" type="button">Close</button>' : "&mdash;"}</td>
  </tr>`;

const bindPaperTradePage = () => {
  const form = document.getElementById("paperTradeForm");
  const table = document.getElementById("paperTradeTable");
  if (!(form instanceof HTMLFormElement) || !table) return;
  const tbody = table.querySelector("tbody");
  if (!(tbody instanceof HTMLElement)) return;
  const countNode = document.getElementById("paperTradeCount");
  const entriesNode = document.querySelector('[data-summary="entries_today"]');

  const renderRows = (rows) => {
    tbody.innerHTML = rows.length
      ? rows.map((trade) => buildPaperTradeRow(trade)).join("")
      : '<tr><td colspan="8" class="muted">No paper trades yet. Execute one above to get started.</td></tr>';
    if (countNode) countNode.textContent = `${rows.length} entries`;
  };

  const loadTrades = async () => {
    const payload = await requestJson("/api/paper-trade/list");
    renderRows(payload.trades || []);
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    try {
      await requestJson("/api/paper-trade/execute", {
        method: "POST",
        body: JSON.stringify({
          ticker: data.get("ticker"),
          direction: data.get("direction"),
          quantity: data.get("quantity"),
          reason: data.get("reason"),
        }),
      });
      form.reset();
      await loadTrades();
      showToast("Paper trade executed at live price.", "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  });

  table.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || !target.classList.contains("close-paper-trade")) return;
    const row = target.closest("tr[data-trade-id]");
    if (!(row instanceof HTMLElement)) return;
    try {
      await requestJson("/api/paper-trade/close", {
        method: "POST",
        body: JSON.stringify({ trade_id: row.dataset.tradeId }),
      });
      await loadTrades();
      showToast("Paper trade closed.", "success");
    } catch (error) {
      showToast(error.message, "error");
    }
  });
};

const bindGlobalSearch = () => {
  const input = document.getElementById("globalSearch");
  const results = document.getElementById("globalSearchResults");
  if (!(input instanceof HTMLInputElement) || !(results instanceof HTMLElement)) return;

  let debounceTimer = null;
  let activeIndex = -1;
  let currentItems = [];

  const closeResults = () => {
    results.hidden = true;
    results.innerHTML = "";
    activeIndex = -1;
    currentItems = [];
  };

  const goToTicker = (symbol) => {
    window.location.href = `/lookup/${encodeURIComponent(symbol)}`;
  };

  const renderResults = (items) => {
    currentItems = items;
    activeIndex = -1;
    if (!items.length) {
      results.innerHTML = '<div class="global-search-empty">No matching tickers found.</div>';
      results.hidden = false;
      return;
    }
    results.innerHTML = items
      .map(
        (item, index) => `
      <div class="global-search-result" data-index="${index}" data-symbol="${escapeHtml(item.symbol)}">
        <b>${escapeHtml(item.symbol)}</b>
        <span>${escapeHtml(item.name)} · ${escapeHtml(item.exchange)}</span>
      </div>`
      )
      .join("");
    results.hidden = false;
  };

  input.addEventListener("input", () => {
    const query = input.value.trim();
    if (debounceTimer) window.clearTimeout(debounceTimer);
    if (query.length < 1) {
      closeResults();
      return;
    }
    debounceTimer = window.setTimeout(async () => {
      try {
        const payload = await requestJson(`/api/ticker-search?q=${encodeURIComponent(query)}`);
        renderResults(payload.results || []);
      } catch (error) {
        closeResults();
      }
    }, 250);
  });

  results.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    const row = target.closest(".global-search-result");
    if (!(row instanceof HTMLElement) || !row.dataset.symbol) return;
    goToTicker(row.dataset.symbol);
  });

  input.addEventListener("keydown", (event) => {
    if (results.hidden || !currentItems.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      activeIndex = Math.min(activeIndex + 1, currentItems.length - 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
    } else if (event.key === "Enter") {
      if (activeIndex >= 0 && currentItems[activeIndex]) {
        event.preventDefault();
        goToTicker(currentItems[activeIndex].symbol);
      }
      return;
    } else if (event.key === "Escape") {
      closeResults();
      return;
    } else {
      return;
    }
    Array.from(results.children).forEach((child, index) => {
      child.classList.toggle("active", index === activeIndex);
    });
  });

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Node)) return;
    if (!input.parentElement?.contains(event.target)) closeResults();
  });
};

onReady(() => {
  bindAlertDrawer();
  bindGlobalSearch();
  bindWatchlistPage();
  bindPaperTradePage();
  bindAiChartMarks();
  bindScannerPage();
  bindOptionsSuggestions();
  bindSettingsPage();
  bindAccountHubPage();
  bindAutonomyControls();
  bindNotificationsPage();
  bindMissionControlEffects();
  bindLiveDataStatusCard();
  bindMissionAlertFloater();
});
