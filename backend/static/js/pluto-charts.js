/* Candlestick charts for the scanner and trade detail.
   One request per ticker+interval. Repeats on the same page share a
   client cache, and the server caches Alpaca bars, so a refresh of the
   scanner table does not refetch candles. */
(() => {
  const clientCache = new Map();

  const cssColor = (name, fallback) => {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  };

  const parseCandleTime = (dateStr) => {
    const text = String(dateStr || "");
    const hasClock = text.includes("T");
    const iso = hasClock ? (/[Zz]$|[+-]\d\d:?\d\d$/.test(text) ? text : `${text}Z`) : `${text.slice(0, 10)}T00:00:00Z`;
    const ms = new Date(iso).getTime();
    return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
  };

  const MARKS = {
    entry: { variable: "--accent", fallback: "#b08cff", title: "Entry", dashed: false, position: "belowBar", shape: "arrowUp" },
    exit: { variable: "--text-primary", fallback: "#f4f2f8", title: "Exit", dashed: false, position: "aboveBar", shape: "arrowDown" },
    stop: { variable: "--negative", fallback: "#ff5d6c", title: "Stop", dashed: true, position: "belowBar", shape: "circle" },
    target: { variable: "--positive", fallback: "#3dce7a", title: "Target", dashed: true, position: "aboveBar", shape: "circle" },
  };

  const noteFor = (el) => el.parentElement?.querySelector("[data-chart-note]") || null;

  const setNote = (el, text) => {
    const note = noteFor(el);
    if (note) note.textContent = text;
  };

  const mountRecord = (el) => {
    const lib = window.LightweightCharts;
    if (!lib || typeof lib.createChart !== "function" || !lib.CandlestickSeries) return null;
    const positive = cssColor("--positive", "#3dce7a");
    const negative = cssColor("--negative", "#ff5d6c");
    const chart = lib.createChart(el, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: cssColor("--text-muted", "#847e92"),
        fontFamily: "Inter, sans-serif",
        fontSize: 12,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,0.05)" } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: false },
      crosshair: { mode: 0 },
    });
    const series = chart.addSeries(lib.CandlestickSeries, {
      upColor: positive,
      downColor: negative,
      borderVisible: false,
      wickUpColor: positive,
      wickDownColor: negative,
    });
    const markers = typeof lib.createSeriesMarkers === "function" ? lib.createSeriesMarkers(series, []) : null;
    return { chart, series, markers, lines: [] };
  };

  const nearestTime = (target, times) => {
    if (!times.length || target == null) return null;
    let best = times[0];
    let bestGap = Math.abs(best - target);
    for (const time of times) {
      const gap = Math.abs(time - target);
      if (gap < bestGap) {
        best = time;
        bestGap = gap;
      }
    }
    return best;
  };

  const draw = (el, payload) => {
    let record = el._plutoChart;
    if (!record) {
      record = mountRecord(el);
      if (!record) {
        setNote(el, "Chart library failed to load.");
        return;
      }
      el._plutoChart = record;
    }
    const candles = Array.isArray(payload.candles) ? payload.candles : [];
    record.chart.applyOptions({ timeScale: { timeVisible: payload.interval && payload.interval !== "1d" } });
    if (!candles.length) {
      record.series.setData([]);
      setNote(el, payload.error || "No candles for this ticker yet.");
      return;
    }
    const data = candles
      .map((candle) => {
        const time = parseCandleTime(candle.date);
        if (time == null) return null;
        return { time, open: candle.open, high: candle.high, low: candle.low, close: candle.close };
      })
      .filter(Boolean)
      .sort((a, b) => a.time - b.time);
    record.series.setData(data);
    record.lines.forEach((line) => {
      try {
        record.series.removePriceLine(line);
      } catch (_error) {
        /* already removed */
      }
    });
    record.lines = [];
    const times = data.map((bar) => bar.time);
    const markerPoints = [];
    (payload.marks || []).forEach((mark) => {
      const style = MARKS[mark.kind] || MARKS.entry;
      const color = cssColor(style.variable, style.fallback);
      const price = Number(mark.price);
      if (Number.isFinite(price) && price > 0) {
        record.lines.push(
          record.series.createPriceLine({
            price,
            color,
            lineWidth: 1,
            lineStyle: style.dashed ? 2 : 0,
            axisLabelVisible: true,
            title: style.title,
          })
        );
      }
      const snapped = nearestTime(parseCandleTime(mark.time), times);
      if (snapped != null) {
        markerPoints.push({
          time: snapped,
          position: style.position,
          color,
          shape: style.shape,
          text: style.title,
        });
      }
    });
    if (record.markers && typeof record.markers.setMarkers === "function") {
      record.markers.setMarkers(markerPoints);
    }
    record.chart.timeScale().fitContent();
    const labeled = (payload.marks || []).map((mark) => MARKS[mark.kind]?.title).filter(Boolean);
    const unique = [...new Set(labeled)];
    setNote(
      el,
      unique.length
        ? `${payload.ticker || ""} · ${unique.join(", ")} marked`
        : `${payload.ticker || ""} · no saved entry, exit, stop, or target on this ticker`
    );
  };

  const loadChart = async (ticker, interval) => {
    const symbol = String(ticker || "").trim().toUpperCase();
    const frame = interval || "1d";
    if (!symbol) throw new Error("Choose a ticker.");
    const key = `${symbol}:${frame}`;
    if (!clientCache.has(key)) {
      const pending = fetch(`/api/chart-data/${encodeURIComponent(symbol)}?interval=${encodeURIComponent(frame)}`, {
        headers: { Accept: "application/json" },
      }).then(async (response) => {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.success === false) {
          const message = payload.error && payload.error.message ? payload.error.message : "Chart data unavailable.";
          throw new Error(message);
        }
        return payload.data || payload;
      });
      clientCache.set(key, pending);
      pending.catch(() => clientCache.delete(key));
    }
    return clientCache.get(key);
  };

  const show = async (el, ticker, interval) => {
    if (!(el instanceof HTMLElement)) return;
    setNote(el, "Loading candles…");
    try {
      const payload = await loadChart(ticker, interval);
      draw(el, payload);
    } catch (error) {
      setNote(el, error.message || "Chart data unavailable.");
    }
  };

  const bindScanner = () => {
    const panel = document.querySelector("[data-scanner-chart]");
    const canvas = document.getElementById("scannerChart");
    const table = document.getElementById("scannerTable");
    if (!(panel instanceof HTMLElement) || !(canvas instanceof HTMLElement) || !(table instanceof HTMLElement)) return;

    let ticker = (panel.dataset.ticker || "").trim().toUpperCase();
    let interval = "1d";

    const highlight = () => {
      table.querySelectorAll("tbody tr").forEach((row) => {
        row.classList.toggle("is-selected", (row.dataset.ticker || "").toUpperCase() === ticker);
      });
      const label = document.getElementById("scannerChartTicker");
      if (label) label.textContent = ticker || "—";
    };

    const select = (next, force = false) => {
      const symbol = String(next || "").trim().toUpperCase();
      if (!symbol || (symbol === ticker && !force)) {
        highlight();
        return;
      }
      ticker = symbol;
      panel.dataset.ticker = symbol;
      highlight();
      show(canvas, symbol, interval);
    };

    table.addEventListener("click", (event) => {
      const row = event.target instanceof Element ? event.target.closest("tbody tr") : null;
      if (!row || !row.dataset.ticker) return;
      select(row.dataset.ticker, true);
    });

    panel.querySelectorAll("[data-chart-interval]").forEach((button) => {
      button.addEventListener("click", () => {
        const next = button.dataset.chartInterval || "1d";
        if (next === interval) return;
        interval = next;
        panel.querySelectorAll("[data-chart-interval]").forEach((item) => item.classList.toggle("is-active", item === button));
        if (ticker) show(canvas, ticker, interval);
      });
    });

    const observer = new MutationObserver(() => highlight());
    const body = table.querySelector("tbody");
    if (body) observer.observe(body, { childList: true });

    highlight();
    if (ticker) show(canvas, ticker, interval);
  };

  const bindMountedCharts = () => {
    const nodes = document.querySelectorAll("[data-pluto-chart]");
    if (!nodes.length || !("IntersectionObserver" in window)) {
      nodes.forEach((node) => {
        if (node.dataset.ticker) show(node, node.dataset.ticker, node.dataset.interval || "1d");
      });
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const node = entry.target;
          observer.unobserve(node);
          if (node.dataset.ticker) show(node, node.dataset.ticker, node.dataset.interval || "1d");
        });
      },
      { rootMargin: "80px" }
    );
    nodes.forEach((node) => observer.observe(node));
  };

  const bindTradeDetail = () => {
    const panel = document.querySelector("[data-trade-detail-chart]");
    const canvas = document.getElementById("tradeDetailChart");
    if (!(panel instanceof HTMLElement) || !(canvas instanceof HTMLElement)) return;
    const rows = document.querySelectorAll("[data-trade-detail-row]");
    const select = (row) => {
      const ticker = (row.dataset.ticker || "").trim().toUpperCase();
      if (!ticker) return;
      rows.forEach((item) => item.classList.toggle("is-selected", item === row));
      const label = document.getElementById("tradeDetailTicker");
      if (label) label.textContent = ticker;
      panel.hidden = false;
      show(canvas, ticker, "1d");
    };
    rows.forEach((row) => {
      row.addEventListener("click", () => select(row));
    });
    const first = rows[0];
    if (first && first.dataset.ticker) select(first);
  };

  const start = () => {
    bindScanner();
    bindMountedCharts();
    bindTradeDetail();
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
