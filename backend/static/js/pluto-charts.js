/* One candlestick chart, reused. Candles come from /api/chart-data, which
   reads the cached Alpaca history. Entry, exit, stop, and target are price lines. */
(() => {
  const LIBRARY = "https://cdn.jsdelivr.net/npm/lightweight-charts@5.2.1/dist/lightweight-charts.standalone.production.js";

  const loadLibrary = () => {
    if (window.LightweightCharts) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const existing = document.querySelector("script[data-pluto-lw]");
      if (existing) {
        existing.addEventListener("load", () => resolve(), { once: true });
        existing.addEventListener("error", () => reject(new Error("chart library failed")), { once: true });
        return;
      }
      const script = document.createElement("script");
      script.src = LIBRARY;
      script.async = true;
      script.dataset.plutoLw = "1";
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("chart library failed"));
      document.head.appendChild(script);
    });
  };

  const cssColor = (name, fallback) => {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  };

  const parseTime = (dateStr) => {
    const raw = String(dateStr || "");
    const hasClock = raw.includes("T");
    const iso = hasClock ? (/[Zz]$|[+-]\d\d:?\d\d$/.test(raw) ? raw : `${raw}Z`) : `${raw}T00:00:00Z`;
    return Math.floor(new Date(iso).getTime() / 1000);
  };

  const numberOrBlank = (value) => {
    if (value === undefined || value === null || value === "") return "";
    const number = Number(value);
    return Number.isFinite(number) && number > 0 ? String(number) : "";
  };

  const bindMount = async (mount) => {
    const note = mount.parentElement ? mount.parentElement.querySelector(".pluto-chart-note") : null;
    try {
      await loadLibrary();
    } catch (error) {
      if (note) note.textContent = "Chart library failed to load.";
      return;
    }
    const { createChart, CandlestickSeries } = window.LightweightCharts;
    const chart = createChart(mount, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: cssColor("--text-muted", "#7e878e"),
        fontFamily: cssColor("--font", "Inter, sans-serif"),
        fontSize: 11,
      },
      grid: { vertLines: { visible: false }, horzLines: { color: cssColor("--line", "rgba(255,255,255,0.08)") } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true },
      crosshair: { mode: 0 },
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: cssColor("--positive", "#3dd68c"),
      downColor: cssColor("--negative", "#ff5d6c"),
      borderVisible: false,
      wickUpColor: cssColor("--positive", "#3dd68c"),
      wickDownColor: cssColor("--negative", "#ff5d6c"),
    });
    let priceLines = [];

    const clearLines = () => {
      priceLines.forEach((line) => {
        try { series.removePriceLine(line); } catch (_error) { /* already removed */ }
      });
      priceLines = [];
    };

    const addLine = (price, color, title) => {
      const value = Number(price);
      if (!Number.isFinite(value) || value <= 0) return;
      priceLines.push(series.createPriceLine({
        price: value,
        color,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title,
      }));
    };

    const draw = async (source) => {
      const ticker = String((source && (source.dataset.ticker || source.getAttribute("data-ticker"))) || mount.dataset.ticker || "").trim();
      if (!ticker) {
        if (note) note.textContent = "Pick a ticker to see the chart.";
        return;
      }
      mount.dataset.ticker = ticker;
      document.querySelectorAll(".chart-pick.is-selected").forEach((node) => node.classList.remove("is-selected"));
      if (source && source.classList) source.classList.add("is-selected");
      const params = new URLSearchParams({ interval: mount.dataset.interval || "15m" });
      ["entry", "exit", "stop", "target"].forEach((key) => {
        const value = numberOrBlank(source && source.dataset ? source.dataset[key] : "");
        if (value) params.set(key, value);
      });
      if (note) note.textContent = `Loading ${ticker}…`;
      try {
        const response = await fetch(`/api/chart-data/${encodeURIComponent(ticker)}?${params.toString()}`, {
          headers: { Accept: "application/json" },
        });
        const payload = await response.json();
        const data = payload.data || payload.chart_data || {};
        if (!response.ok || payload.success === false) {
          throw new Error((payload.error && payload.error.message) || "Chart data unavailable.");
        }
        const candles = Array.isArray(data.candles) ? data.candles : [];
        if (!candles.length) {
          series.setData([]);
          clearLines();
          if (note) note.textContent = data.error || `No candles for ${ticker}.`;
          return;
        }
        const interval = data.interval || mount.dataset.interval || "15m";
        chart.applyOptions({ timeScale: { timeVisible: interval !== "1d" } });
        series.setData(candles.map((candle) => ({
          time: parseTime(candle.date),
          open: candle.open,
          high: candle.high,
          low: candle.low,
          close: candle.close,
        })));
        chart.timeScale().fitContent();
        clearLines();
        const levels = data.levels || {};
        addLine(levels.entry, cssColor("--accent", "#22e0ff"), "Entry");
        addLine(levels.exit, cssColor("--text-primary", "#f3f6f7"), "Exit");
        addLine(levels.stop, cssColor("--negative", "#ff5d6c"), "Stop");
        addLine(levels.target, cssColor("--positive", "#3dd68c"), "Target");
        if (note) note.textContent = `${data.ticker || ticker} · ${interval}`;
      } catch (error) {
        if (note) note.textContent = error.message || "Chart data unavailable.";
      }
    };

    mount.drawPlutoChart = draw;
    const root = mount.closest("[data-chart-scope]") || document;
    root.addEventListener("click", (event) => {
      const source = event.target instanceof Element ? event.target.closest(".chart-pick") : null;
      if (!source || !root.contains(source)) return;
      draw(source).catch(() => {});
    });
    const initial = root.querySelector(".chart-pick") || mount;
    if (initial && (initial.dataset.ticker || mount.dataset.ticker)) {
      draw(initial.dataset.ticker ? initial : mount).catch(() => {});
    }
  };

  const start = () => {
    const mounts = Array.from(document.querySelectorAll("[data-pluto-chart]"));
    const bind = (mount) => {
      bindMount(mount).catch(() => {});
    };
    if (!("IntersectionObserver" in window)) {
      mounts.forEach(bind);
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        observer.unobserve(entry.target);
        bind(entry.target);
      });
    }, { rootMargin: "240px" });
    mounts.forEach((mount) => observer.observe(mount));
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
