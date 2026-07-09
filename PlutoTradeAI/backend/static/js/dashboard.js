(function () {
    const requestJson = async (url, options = {}) => {
        const response = await fetch(url, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.ok === false) {
            throw new Error(payload.error || "Request failed.");
        }
        return payload;
    };

    const ensureToastWrap = () => {
        let wrap = document.querySelector(".toast-wrap");
        if (wrap) return wrap;
        wrap = document.createElement("div");
        wrap.className = "toast-wrap";
        document.body.appendChild(wrap);
        return wrap;
    };

    const toast = (message, tone = "") => {
        const wrap = ensureToastWrap();
        const item = document.createElement("div");
        item.className = `toast ${tone}`.trim();
        item.textContent = message;
        wrap.appendChild(item);
        setTimeout(() => item.remove(), 2800);
    };

    const clock = document.getElementById("liveClock");
    const marketStatus = document.getElementById("marketStatus");
    const notificationCount = document.getElementById("notificationCount");

    const updateClock = () => {
        if (clock) clock.textContent = new Date().toLocaleTimeString();
    };

    const updateMarketStatus = () => {
        if (!marketStatus) return;
        const now = new Date();
        const eastern = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
        const day = eastern.getDay();
        const total = eastern.getHours() * 60 + eastern.getMinutes();
        marketStatus.textContent = day >= 1 && day <= 5 && total >= 570 && total < 960 ? "Open" : "Closed";
    };

    const refreshNotificationCount = () => {
        if (!(notificationCount instanceof HTMLElement)) return;
        const count = document.querySelectorAll("#notificationList .alert-item, #notificationsList .alert-item").length;
        notificationCount.textContent = String(count);
    };

    const renderAlerts = (container, alerts) => {
        if (!(container instanceof HTMLElement)) return;
        if (!alerts.length) {
            container.innerHTML = "<p>No active notifications.</p>";
            refreshNotificationCount();
            return;
        }
        container.innerHTML = alerts
            .map(
                (alert) => `
                <div class="alert-item" data-alert-id="${alert.id}">
                    <div><b>${alert.type}</b><p>${alert.ticker || ""} ${alert.message || ""}</p></div>
                    <button type="button" class="btn-neutral dismiss-alert" data-alert-id="${alert.id}">Dismiss</button>
                </div>`
            )
            .join("");
        refreshNotificationCount();
    };

    const bindNotificationDrawer = () => {
        const drawer = document.getElementById("notificationDrawer");
        const open = document.getElementById("notificationDrawerToggle");
        const close = document.getElementById("notificationDrawerClose");
        const markAll = document.getElementById("notificationMarkAll");
        const list = document.getElementById("notificationList");
        if (!drawer || !open || !close || !list) return;

        const refreshAlerts = async () => {
            const payload = await requestJson("/api/alerts");
            renderAlerts(list, payload.alerts || []);
        };

        open.addEventListener("click", () => {
            drawer.classList.add("open");
            refreshAlerts().catch((error) => toast(error.message, "error"));
        });
        close.addEventListener("click", () => drawer.classList.remove("open"));
        setInterval(() => refreshAlerts().catch(() => {}), 30000);

        if (markAll instanceof HTMLButtonElement) {
            markAll.addEventListener("click", async () => {
                const ids = Array.from(list.querySelectorAll(".alert-item")).map((row) => row.getAttribute("data-alert-id") || "");
                try {
                    await requestJson("/api/alerts", {
                        method: "POST",
                        body: JSON.stringify({ action: "dismiss_all", ids }),
                    });
                    renderAlerts(list, []);
                    toast("All notifications marked read.");
                } catch (error) {
                    toast(error.message, "error");
                }
            });
        }

        drawer.addEventListener("click", async (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement) || !target.classList.contains("dismiss-alert")) return;
            try {
                await requestJson("/api/alerts", {
                    method: "POST",
                    body: JSON.stringify({ action: "dismiss", id: target.dataset.alertId || "" }),
                });
                target.closest(".alert-item")?.remove();
                refreshNotificationCount();
            } catch (error) {
                toast(error.message, "error");
            }
        });

        const manualAlertForm = document.getElementById("manualAlertForm");
        if (manualAlertForm instanceof HTMLFormElement) {
            manualAlertForm.addEventListener("submit", async (event) => {
                event.preventDefault();
                const form = new FormData(manualAlertForm);
                try {
                    await requestJson("/api/alerts", {
                        method: "POST",
                        body: JSON.stringify({
                            action: "add",
                            type: "System",
                            ticker: String(form.get("ticker") || ""),
                            message: String(form.get("message") || ""),
                        }),
                    });
                    manualAlertForm.reset();
                    refreshAlerts().catch(() => {});
                    toast("Notification added.");
                } catch (error) {
                    toast(error.message, "error");
                }
            });
        }
    };

    const bindScanner = () => {
        const button = document.getElementById("refreshScannerButton");
        const tableBody = document.getElementById("scannerTableBody");
        if (!(button instanceof HTMLButtonElement) || !(tableBody instanceof HTMLElement)) return;
        const stamp = document.getElementById("scannerUpdatedAt");
        const badge = document.getElementById("scannerLiveBadge");
        const errors = document.getElementById("scannerErrors");

        const renderRows = (rows) => {
            if (!rows.length) {
                tableBody.innerHTML = '<tr><td colspan="8">No scanner results yet.</td></tr>';
                return;
            }
            tableBody.innerHTML = rows
                .map(
                    (row) => `
                    <tr>
                        <td>${row.ticker}</td>
                        <td>$${row.price}</td>
                        <td class="${row.percent_change > 0 ? "positive" : "negative"}">${row.percent_change}%</td>
                        <td>${row.relative_volume}</td>
                        <td>${row.volume}</td>
                        <td>${row.scanner_score}</td>
                        <td><span class="tag ${row.on_watchlist ? "tag-live" : "tag-dark"}">${row.on_watchlist ? "ON" : "OFF"}</span></td>
                        <td>${row.last_updated ? String(row.last_updated).slice(0, 19) : "n/a"}</td>
                    </tr>`
                )
                .join("");
        };

        const refresh = async (forceRefresh) => {
            button.disabled = true;
            button.textContent = forceRefresh ? "Refreshing..." : "Updating...";
            if (forceRefresh) {
                tableBody.innerHTML = '<tr><td colspan="8">Loading live market data...</td></tr>';
            }
            try {
                const payload = await requestJson(`/api/scanner?refresh=${forceRefresh ? "true" : "false"}`);
                renderRows(payload.rows || []);
                if (stamp) stamp.textContent = payload.last_updated || "n/a";
                if (badge) badge.textContent = `Live updates every 20s · ${new Date().toLocaleTimeString()}`;
                if (errors) {
                    errors.innerHTML = payload.errors?.length ? `<p class="negative">${payload.errors.join(" | ")}</p>` : "";
                }
            } catch (error) {
                toast(error.message, "error");
            } finally {
                button.disabled = false;
                button.textContent = "Refresh";
            }
        };

        button.addEventListener("click", () => refresh(true).catch(() => {}));
        setInterval(() => refresh(false).catch(() => {}), 20000);
    };

    const bindWatchlist = () => {
        const form = document.getElementById("watchlistForm");
        const tableBody = document.getElementById("watchlistTableBody");
        if (!(form instanceof HTMLFormElement) || !(tableBody instanceof HTMLElement)) return;
        const search = document.getElementById("watchlistSearch");
        const category = document.getElementById("watchlistCategoryFilter");
        const status = document.getElementById("watchlistStatusFilter");
        const sort = document.getElementById("watchlistSortBy");

        const applyFilters = () => {
            const searchValue = search instanceof HTMLInputElement ? search.value.trim().toUpperCase() : "";
            const categoryValue = category instanceof HTMLSelectElement ? category.value.trim().toUpperCase() : "";
            const statusValue = status instanceof HTMLSelectElement ? status.value.trim().toUpperCase() : "";
            const sortValue = sort instanceof HTMLSelectElement ? sort.value : "ticker";

            const rows = Array.from(tableBody.querySelectorAll("tr[data-ticker]"));
            rows.sort((a, b) => {
                if (!(a instanceof HTMLElement) || !(b instanceof HTMLElement)) return 0;
                if (sortValue === "ai_score") {
                    const aScore = Number(a.querySelector('input[data-field="ai_score"]')?.value || "0");
                    const bScore = Number(b.querySelector('input[data-field="ai_score"]')?.value || "0");
                    return bScore - aScore;
                }
                const aValue = a.querySelector("td")?.textContent || "";
                const bValue = b.querySelector("td")?.textContent || "";
                return aValue.localeCompare(bValue);
            });
            rows.forEach((row) => tableBody.appendChild(row));

            rows.forEach((row) => {
                if (!(row instanceof HTMLElement)) return;
                const ticker = (row.dataset.ticker || "").toUpperCase();
                const categoryText = (row.querySelector('input[data-field="category"]')?.value || "").toUpperCase();
                const statusText = (row.querySelector('input[data-field="status"]')?.value || "").toUpperCase();
                const notes = (row.querySelector('input[data-field="notes"]')?.value || "").toUpperCase();
                const matches =
                    (!searchValue || ticker.includes(searchValue) || categoryText.includes(searchValue) || notes.includes(searchValue)) &&
                    (!categoryValue || categoryText === categoryValue) &&
                    (!statusValue || statusText === statusValue);
                row.style.display = matches ? "" : "none";
            });
        };

        [search, category, status, sort].forEach((control) => {
            if (!control) return;
            control.addEventListener("input", applyFilters);
            control.addEventListener("change", applyFilters);
        });

        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const data = new FormData(form);
            try {
                const payload = await requestJson("/api/watchlist/add", {
                    method: "POST",
                    body: JSON.stringify({
                        ticker: data.get("ticker"),
                        category: data.get("category"),
                        status: data.get("status"),
                        ai_score: data.get("ai_score"),
                        notes: data.get("notes"),
                    }),
                });
                const item = payload.item;
                tableBody.querySelector(".watchlist-empty-row")?.remove();
                tableBody.insertAdjacentHTML(
                    "beforeend",
                    `<tr data-ticker="${item.ticker}">
                        <td>${item.ticker}</td>
                        <td><input type="text" value="${item.category}" data-field="category"></td>
                        <td><input type="text" value="${item.status}" data-field="status"></td>
                        <td><input type="number" value="${item.ai_score}" data-field="ai_score"></td>
                        <td><input type="text" value="${item.notes}" data-field="notes"></td>
                        <td><div class="inline-actions"><button type="button" class="btn-neutral save-watchlist">Save</button><button type="button" class="btn-neutral delete-watchlist">Delete</button></div></td>
                    </tr>`
                );
                form.reset();
                applyFilters();
                toast(`${item.ticker} added.`);
            } catch (error) {
                toast(error.message, "error");
            }
        });

        tableBody.addEventListener("click", async (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;
            const row = target.closest("tr[data-ticker]");
            if (!(row instanceof HTMLElement)) return;
            const ticker = row.dataset.ticker || "";
            if (target.classList.contains("save-watchlist")) {
                const payload = { ticker };
                row.querySelectorAll("input[data-field]").forEach((input) => {
                    if (!(input instanceof HTMLInputElement)) return;
                    payload[input.dataset.field] = input.value;
                });
                try {
                    await requestJson("/api/watchlist/update", {
                        method: "POST",
                        body: JSON.stringify(payload),
                    });
                    toast(`${ticker} saved.`);
                } catch (error) {
                    toast(error.message, "error");
                }
            }
            if (target.classList.contains("delete-watchlist")) {
                try {
                    await requestJson("/api/watchlist/delete", {
                        method: "POST",
                        body: JSON.stringify({ ticker }),
                    });
                    row.remove();
                    if (!tableBody.querySelector("tr[data-ticker]")) {
                        tableBody.innerHTML = '<tr class="watchlist-empty-row"><td colspan="6">No watchlist tickers yet.</td></tr>';
                    }
                    toast(`${ticker} deleted.`);
                } catch (error) {
                    toast(error.message, "error");
                }
            }
        });

        applyFilters();
    };

    const bindSuggestionActions = () => {
        document.addEventListener("click", async (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement)) return;
            if (target.classList.contains("dismiss-suggestion")) {
                target.closest("li")?.remove();
                return;
            }
            if (!target.classList.contains("add-suggestion")) return;
            try {
                await requestJson("/api/watchlist/add", {
                    method: "POST",
                    body: JSON.stringify({
                        ticker: target.dataset.ticker || "",
                        category: target.dataset.category || "AI Discovery",
                        status: target.dataset.status || "Candidate",
                        ai_score: target.dataset.aiScore || "65",
                        notes: target.dataset.notes || "",
                    }),
                });
                target.textContent = "Added";
                target.setAttribute("disabled", "true");
                toast(`${target.dataset.ticker || "Ticker"} added to watchlist.`);
            } catch (error) {
                toast(error.message, "error");
            }
        });
    };

    const bindNotificationsPage = () => {
        const list = document.getElementById("notificationsList");
        const markAll = document.getElementById("notificationsMarkAll");
        if (!(list instanceof HTMLElement)) return;
        list.addEventListener("click", async (event) => {
            const target = event.target;
            if (!(target instanceof HTMLElement) || !target.classList.contains("dismiss-alert")) return;
            try {
                await requestJson("/api/alerts", {
                    method: "POST",
                    body: JSON.stringify({ action: "dismiss", id: target.dataset.alertId || "" }),
                });
                target.closest(".alert-item")?.remove();
                toast("Alert dismissed.");
            } catch (error) {
                toast(error.message, "error");
            }
        });
        if (markAll instanceof HTMLButtonElement) {
            markAll.addEventListener("click", async () => {
                const ids = Array.from(list.querySelectorAll(".alert-item")).map((item) => item.getAttribute("data-alert-id") || "");
                try {
                    await requestJson("/api/alerts", {
                        method: "POST",
                        body: JSON.stringify({ action: "dismiss_all", ids }),
                    });
                    list.innerHTML = "<p>No active notifications.</p>";
                    toast("All notifications marked read.");
                } catch (error) {
                    toast(error.message, "error");
                }
            });
        }
    };

    const bindAccountHub = () => {
        const cards = document.querySelectorAll(".account-card");
        if (!cards.length) return;
        cards.forEach((card) => {
            card.addEventListener("click", async (event) => {
                const target = event.target;
                if (!(target instanceof HTMLElement)) return;
                const button = target.closest("button");
                if (!(button instanceof HTMLButtonElement)) return;
                const platform = card.getAttribute("data-platform") || "";
                if (!platform) return;
                try {
                    let endpoint = "";
                    if (button.classList.contains("account-connect")) endpoint = "/api/accounts/connect";
                    if (button.classList.contains("account-disconnect")) endpoint = "/api/accounts/disconnect";
                    if (button.classList.contains("account-test")) endpoint = "/api/accounts/test";
                    if (!endpoint) return;
                    const payload = await requestJson(endpoint, {
                        method: "POST",
                        body: JSON.stringify({ platform }),
                    });
                    const account = payload.account || {};
                    card.querySelectorAll("[data-account-field]").forEach((node) => {
                        if (!(node instanceof HTMLElement)) return;
                        const key = node.getAttribute("data-account-field") || "";
                        if (!key) return;
                        const value = account[key];
                        node.textContent = Array.isArray(value) ? value.join(", ") : String(value || "Never");
                    });
                    toast(`${platform} updated.`);
                } catch (error) {
                    toast(error.message, "error");
                }
            });

            const toggle = card.querySelector(".trading-toggle");
            if (toggle instanceof HTMLInputElement) {
                toggle.addEventListener("change", async () => {
                    const previous = !toggle.checked;
                    try {
                        await requestJson("/api/accounts/connect", {
                            method: "POST",
                            body: JSON.stringify({
                                platform: card.getAttribute("data-platform") || "",
                                trading_enabled: toggle.checked,
                            }),
                        });
                        toast("Live trading preference updated.");
                    } catch (error) {
                        toggle.checked = previous;
                        toast(error.message, "error");
                    }
                });
            }
        });
    };

    const bindUpcomingOpportunities = () => {
        const opportunityList = document.getElementById("upcomingOpportunitiesList");
        const missionQueueList = document.getElementById("missionQueueList");
        const timelineWrap = document.getElementById("opportunitiesTimeline");
        const missionAlertsFeed = document.getElementById("missionAlertsFeed");
        if (
            !(opportunityList instanceof HTMLElement) ||
            !(missionQueueList instanceof HTMLElement) ||
            !(timelineWrap instanceof HTMLElement) ||
            !(missionAlertsFeed instanceof HTMLElement)
        ) {
            return;
        }

        const seenMissionAlerts = new Set(
            Array.from(missionAlertsFeed.querySelectorAll("li[data-alert-key]"))
                .map((node) => node.getAttribute("data-alert-key") || "")
                .filter(Boolean)
        );

        const renderOpportunities = (opportunities = []) => {
            if (!opportunities.length) {
                opportunityList.innerHTML = '<article class="glass-panel panel"><p>No upcoming opportunities identified yet.</p></article>';
                return;
            }
            opportunityList.innerHTML = opportunities
                .sort((a, b) => Number(b.confidence_score || 0) - Number(a.confidence_score || 0))
                .map(
                    (item) => `
                    <article class="opportunity-card glass-panel glow-hover ${Number(item.confidence_score) >= 85 ? "high-conviction" : ""}" data-confidence="${item.confidence_score}">
                        <header class="opportunity-header">
                            <div>
                                <h4>${item.ticker}</h4>
                                <p>${item.company_name}</p>
                            </div>
                            <div class="opportunity-tags">
                                <span class="tag">${item.ai_bias}</span>
                                <span class="tag">${item.trade_quality}</span>
                                <span class="tag">${item.risk}</span>
                            </div>
                        </header>
                        <div class="opportunity-metrics">
                            <p><b>Current Price:</b> $${item.current_price}</p>
                            <p><b>Confidence:</b> ${item.confidence_score}%</p>
                            <p><b>Expected Horizon:</b> ${item.expected_time_horizon}</p>
                            <p><b>Expected Move:</b> ${item.expected_move}</p>
                        </div>
                        <div class="price-watch-grid">
                            <h5>Price Watch</h5>
                            <p><b>Ideal Entry Zone:</b> $${item.price_watch.ideal_entry_low}–$${item.price_watch.ideal_entry_high}</p>
                            <p><b>Support:</b> $${item.price_watch.support}</p>
                            <p><b>Resistance:</b> $${item.price_watch.resistance}</p>
                            <p><b>Breakout Price:</b> $${item.price_watch.breakout_price}</p>
                            <p><b>Breakdown Price:</b> $${item.price_watch.breakdown_price}</p>
                            <p><b>Reversal Zone:</b> $${item.price_watch.reversal_zone_low}–$${item.price_watch.reversal_zone_high}</p>
                            <p><b>Target 1:</b> $${item.price_watch.target_1}</p>
                            <p><b>Target 2:</b> $${item.price_watch.target_2}</p>
                            <p><b>Invalidation:</b> ${item.ai_bias === "PUT" ? "Above" : "Below"} $${item.price_watch.invalidation_level}</p>
                        </div>
                        <div class="thesis-block">
                            <h5>Trade Thesis</h5>
                            <p>${item.trade_thesis}</p>
                        </div>
                        <div class="case-grid">
                            <div>
                                <h5>Bull Case</h5>
                                <ul class="feed-list">${(item.bull_case || []).map((line) => `<li>${line}</li>`).join("")}</ul>
                            </div>
                            <div>
                                <h5>Bear Case</h5>
                                <ul class="feed-list">${(item.bear_case || []).map((line) => `<li>${line}</li>`).join("")}</ul>
                            </div>
                        </div>
                        <div class="options-research-block">
                            <h5>Options Research</h5>
                            <div class="options-grid">
                                ${(item.options_research || [])
                                    .map(
                                        (option) => `
                                    <article class="glass-panel options-card">
                                        <h6>${option.profile}</h6>
                                        <p><b>AI Bias:</b> ${option.ai_bias}</p>
                                        <p><b>Expiration Date:</b> ${option.expiration_date}</p>
                                        <p><b>Suggested Strike Area:</b> ${option.suggested_strike_area}</p>
                                        <p><b>Estimated Premium:</b> ${option.estimated_premium}</p>
                                        <p><b>Estimated Break-even:</b> ${option.estimated_break_even}</p>
                                        <p><b>Risk Rating:</b> ${option.risk_rating}</p>
                                        <p><b>Expected Hold Time:</b> ${option.expected_hold_time}</p>
                                        <p><b>Expected Volatility:</b> ${option.expected_volatility}</p>
                                        <p class="option-language">${option.language}</p>
                                    </article>`
                                    )
                                    .join("")}
                            </div>
                        </div>
                    </article>`
                )
                .join("");
        };

        const renderMissionQueue = (queue = []) => {
            if (!queue.length) {
                missionQueueList.innerHTML = "<li>No queued missions.</li>";
                return;
            }
            missionQueueList.innerHTML = queue
                .sort((a, b) => Number(a.priority || 999) - Number(b.priority || 999))
                .map(
                    (item) => `
                        <li>
                            <b>Priority ${item.priority} · ${item.ticker} · ${item.ai_bias}</b>
                            <p>Confidence ${item.confidence}%</p>
                            <p>Waiting for: ${item.waiting_for}</p>
                        </li>`
                )
                .join("");
        };

        const renderTimeline = (timeline = {}) => {
            const buckets = ["Today", "Tomorrow", "Next 3 Days", "Next Week", "Next Month"];
            timelineWrap.innerHTML = buckets
                .map((bucket) => {
                    const items = Array.isArray(timeline[bucket]) ? timeline[bucket] : [];
                    const inner = items.length
                        ? items.map((item) => `<li><b>${item.ticker}</b> · ${item.label} · ${item.confidence}</li>`).join("")
                        : "<li>No scheduled setups.</li>";
                    return `<div class="timeline-bucket"><h4>${bucket}</h4><ul class="feed-list">${inner}</ul></div>`;
                })
                .join("");
        };

        const renderMissionAlerts = (alerts = []) => {
            if (!alerts.length) {
                missionAlertsFeed.innerHTML = "<li>No mission alerts currently.</li>";
                return;
            }
            missionAlertsFeed.innerHTML = alerts
                .map((alert) => {
                    const key = `${alert.ticker}-${alert.message}`;
                    if (!seenMissionAlerts.has(key)) {
                        seenMissionAlerts.add(key);
                        toast(`${alert.ticker}: ${alert.message}`);
                    }
                    return `<li data-alert-key="${key}">
                        <b>${alert.type} · ${alert.ticker}</b>
                        <p>${alert.message}</p>
                        <p>Confidence: ${alert.confidence}</p>
                        <p>Suggested Action: ${alert.suggested_action}</p>
                    </li>`;
                })
                .join("");
        };

        const refresh = async (forceRefresh = false) => {
            try {
                const payload = await requestJson(`/api/opportunities?refresh=${forceRefresh ? "true" : "false"}`);
                renderOpportunities(payload.opportunities || []);
                renderMissionQueue(payload.mission_queue || []);
                renderTimeline(payload.timeline || {});
                renderMissionAlerts(payload.mission_alerts || []);
            } catch (error) {
                toast(error.message, "error");
            }
        };

        refresh(false).catch(() => {});
        setInterval(() => refresh(false).catch(() => {}), 45000);
    };

    updateClock();
    updateMarketStatus();
    setInterval(updateClock, 1000);
    setInterval(updateMarketStatus, 60000);
    bindNotificationDrawer();
    bindScanner();
    bindWatchlist();
    bindSuggestionActions();
    bindNotificationsPage();
    bindAccountHub();
    bindUpcomingOpportunities();
})();
