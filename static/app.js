const WS_URL = `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}/ws`;

const statusDot = document.getElementById("status-dot");
const statusNode = document.getElementById("status");
const lastUpdateNode = document.getElementById("last-update");
const streamBanner = document.getElementById("stream-banner");
const streamBannerText = document.getElementById("stream-banner-text");
const symbolSelect = document.getElementById("symbol-select");
const watchlistBody = document.getElementById("watchlist-body");
const timeframeButtons = Array.from(document.querySelectorAll(".time-btn"));

const statSymbol = document.getElementById("stat-symbol");
const statPrice = document.getElementById("stat-price");
const statChange = document.getElementById("stat-change");
const statRsi = document.getElementById("stat-rsi");
const statEma = document.getElementById("stat-ema");
const statSignal = document.getElementById("stat-signal");
const statStrength = document.getElementById("stat-strength");
const moversGainersNode = document.getElementById("movers-gainers");
const moversLosersNode = document.getElementById("movers-losers");
const moversVolumeNode = document.getElementById("movers-volume");
const orderflowSymbolNode = document.getElementById("orderflow-symbol");
const orderflowMetaNode = document.getElementById("orderflow-meta");
const orderflowErrorNode = document.getElementById("orderflow-error");
const orderbookBidsBody = document.getElementById("orderbook-bids-body");
const orderbookAsksBody = document.getElementById("orderbook-asks-body");
const recentTradesBody = document.getElementById("recent-trades-body");

const priceChartContainer = document.getElementById("price-chart");
const rsiChartContainer = document.getElementById("rsi-chart");
const macdChartContainer = document.getElementById("macd-chart");

const riskAccountInput = document.getElementById("risk-account");
const riskPercentInput = document.getElementById("risk-percent");
const riskStopInput = document.getElementById("risk-stop");
const riskTpInput = document.getElementById("risk-tp");
const riskDirectionInput = document.getElementById("risk-direction");
const riskLeverageInput = document.getElementById("risk-leverage");

const riskOutDirection = document.getElementById("risk-out-direction");
const riskOutRiskAmount = document.getElementById("risk-out-risk-amount");
const riskOutEntry = document.getElementById("risk-out-entry");
const riskOutStopPrice = document.getElementById("risk-out-stop-price");
const riskOutTpPrice = document.getElementById("risk-out-tp-price");
const riskOutSize = document.getElementById("risk-out-size");
const riskOutNotional = document.getElementById("risk-out-notional");
const riskOutRr = document.getElementById("risk-out-rr");
const riskOutPnl = document.getElementById("risk-out-pnl");

const alertCountNode = document.getElementById("alert-count");
const alertsListNode = document.getElementById("alerts-list");
const toggleSoundBtn = document.getElementById("toggle-sound");
const toggleBrowserAlertBtn = document.getElementById("toggle-browser-alert");
const clearAlertsBtn = document.getElementById("clear-alerts");

const autoTradeStatusNode = document.getElementById("auto-trade-status");
const autoTradeModeNode = document.getElementById("auto-trade-mode");
const autoTradePnlNode = document.getElementById("auto-trade-pnl");
const autoTradeRiskNode = document.getElementById("auto-trade-risk");
const autoTradeSymbolsNode = document.getElementById("auto-trade-symbols");
const autoTradeSelectedNode = document.getElementById("auto-trade-selected");
const autoTradeNoteNode = document.getElementById("auto-trade-note");
const autoTradeEventsNode = document.getElementById("auto-trade-events");

const walletStatusNode = document.getElementById("wallet-status");
const walletExchangeNode = document.getElementById("wallet-exchange");
const walletTotalNode = document.getElementById("wallet-total-usdt");
const walletDailyPnlNode = document.getElementById("wallet-daily-pnl");
const walletDayStartNode = document.getElementById("wallet-day-start");
const walletUsdtNode = document.getElementById("wallet-usdt");
const walletAssetCountNode = document.getElementById("wallet-asset-count");
const walletErrorNode = document.getElementById("wallet-error");
const walletAssetsBody = document.getElementById("wallet-assets-body");

let ws = null;
let reconnectTimer = null;
let staleTimer = null;
let reconnectAttempt = 0;
let lastSnapshotAt = 0;

let selectedSymbol = "BTC/USDT";
let selectedTimeframe = "1m";
let marketRows = [];
let currentSummary = null;

let allAlerts = [];
let alertCutoffId = 0;
let seenAlertIds = new Set();
let soundEnabled = true;
let browserAlertEnabled = false;
let audioContext = null;

let priceChart;
let rsiChart;
let macdChart;

let candleSeries;
let ema20Series;
let ema50Series;
let volumeSeries;

let rsiSeries;
let rsiUpperSeries;
let rsiLowerSeries;

let macdSeries;
let macdSignalSeries;
let macdHistogramSeries;

function escapeHtml(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtNumber(value, decimals = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toFixed(decimals);
}

function fmtPrice(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const num = Number(value);
  if (num >= 1000) return num.toFixed(2);
  if (num >= 1) return num.toFixed(4);
  return num.toFixed(6);
}

function fmtPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const num = Number(value);
  const sign = num > 0 ? "+" : "";
  return `${sign}${num.toFixed(2)}%`;
}

function fmtMoney(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function fmtQty(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  const num = Number(value);
  if (num >= 1000) return num.toFixed(2);
  if (num >= 1) return num.toFixed(4);
  return num.toFixed(6);
}

function signalClass(signal) {
  if (signal === "BUY") return "buy";
  if (signal === "SELL") return "sell";
  return "hold";
}

function strengthClass(strength) {
  if (strength === "STRONG BUY") return "strength-strong-buy";
  if (strength === "BUY") return "strength-buy";
  if (strength === "SELL") return "strength-sell";
  if (strength === "STRONG SELL") return "strength-strong-sell";
  return "strength-hold";
}

function setStatus(text, connected) {
  statusNode.textContent = text;
  statusDot.classList.toggle("online", Boolean(connected));
}

function setStreamBanner(message, level = "info") {
  if (!streamBanner || !streamBannerText) return;
  streamBannerText.textContent = message;
  streamBanner.classList.remove("hidden", "info", "warning", "error-state");
  streamBanner.classList.add(level);
}

function hideStreamBanner() {
  if (!streamBanner) return;
  streamBanner.classList.add("hidden");
}

function scheduleStaleCheck() {
  if (staleTimer) {
    clearTimeout(staleTimer);
  }

  staleTimer = setTimeout(() => {
    if (ws && ws.readyState === WebSocket.OPEN && lastSnapshotAt > 0) {
      const ageSeconds = Math.floor((Date.now() - lastSnapshotAt) / 1000);
      if (ageSeconds >= 10) {
        setStreamBanner(
          `Waiting for next update... last tick was ${ageSeconds}s ago.`,
          "warning",
        );
      }
    }
    scheduleStaleCheck();
  }, 1000);
}

function setActiveTimeframeButton(timeframe) {
  timeframeButtons.forEach((button) => {
    const active = button.dataset.timeframe === timeframe;
    button.classList.toggle("active", active);
  });
}

function syncSymbolOptions(symbols) {
  const nextSymbols = Array.isArray(symbols) ? symbols : [];
  const current = Array.from(symbolSelect.options).map((o) => o.value);

  const equal =
    current.length === nextSymbols.length &&
    current.every((value, index) => value === nextSymbols[index]);

  if (!equal) {
    symbolSelect.innerHTML = "";
    nextSymbols.forEach((symbol) => {
      const option = document.createElement("option");
      option.value = symbol;
      option.textContent = symbol;
      symbolSelect.appendChild(option);
    });
  }

  if (!nextSymbols.includes(selectedSymbol) && nextSymbols.length > 0) {
    selectedSymbol = nextSymbols[0];
  }

  symbolSelect.value = selectedSymbol;
}

function renderWatchlist(rows) {
  watchlistBody.innerHTML = "";

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    if (row.symbol === selectedSymbol) tr.classList.add("active-row");

    const changeClass = row.change_24h > 0 ? "pos" : row.change_24h < 0 ? "neg" : "";
    const strengthText = row.strength || "HOLD";
    const confidence = Number(row.strength_confidence);
    const confidenceText = Number.isNaN(confidence) ? "" : ` (${confidence}%)`;

    tr.innerHTML = `
      <td>
        <div>${escapeHtml(row.symbol || "-")}</div>
        ${row.error ? `<div class="error">${escapeHtml(row.error)}</div>` : ""}
      </td>
      <td>${fmtPrice(row.price)}</td>
      <td class="${changeClass}">${fmtPercent(row.change_24h)}</td>
      <td>${fmtNumber(row.rsi, 2)}</td>
      <td><span class="signal ${signalClass(row.signal)}">${escapeHtml(row.signal || "HOLD")}</span></td>
      <td><span class="strength-badge ${strengthClass(strengthText)}">${escapeHtml(`${strengthText}${confidenceText}`)}</span></td>
    `;

    tr.addEventListener("click", () => {
      if (!row.symbol) return;
      selectedSymbol = row.symbol;
      symbolSelect.value = selectedSymbol;
      renderWatchlist(marketRows);
      sendViewUpdate();
    });

    watchlistBody.appendChild(tr);
  });
}

function updateStats(summary) {
  currentSummary = summary;

  if (!summary) {
    statSymbol.textContent = "-";
    statPrice.textContent = "-";
    statChange.textContent = "-";
    statRsi.textContent = "-";
    statEma.textContent = "-";
    statSignal.textContent = "-";
    statStrength.textContent = "-";
    updateRiskPanel();
    return;
  }

  statSymbol.textContent = summary.symbol || "-";
  statPrice.textContent = fmtPrice(summary.price);

  statChange.textContent = fmtPercent(summary.change_24h);
  statChange.classList.remove("pos", "neg");
  if (summary.change_24h > 0) statChange.classList.add("pos");
  if (summary.change_24h < 0) statChange.classList.add("neg");

  statRsi.textContent = fmtNumber(summary.rsi, 2);
  statEma.textContent = `${fmtPrice(summary.ema20)} / ${fmtPrice(summary.ema50)}`;

  statSignal.textContent = summary.signal || "HOLD";
  statSignal.classList.remove("pos", "neg");
  if (summary.signal === "BUY") statSignal.classList.add("pos");
  if (summary.signal === "SELL") statSignal.classList.add("neg");

  const strengthValue = summary.strength || "HOLD";
  const strengthConfidence = Number(summary.strength_confidence);
  const strengthConfidenceText = Number.isNaN(strengthConfidence)
    ? ""
    : ` (${strengthConfidence}%)`;
  const aiBias = String(summary.ai_bias || "HOLD").toUpperCase();
  const aiConfidence = Number(summary.ai_confidence);
  const aiConfidenceText = Number.isNaN(aiConfidence) ? "" : ` (${aiConfidence}%)`;
  statStrength.textContent = `${strengthValue}${strengthConfidenceText} • AI ${aiBias}${aiConfidenceText}`;
  statStrength.classList.remove("pos", "neg");
  if (strengthValue.includes("BUY")) statStrength.classList.add("pos");
  if (strengthValue.includes("SELL")) statStrength.classList.add("neg");

  updateRiskPanel();
}

function renderMoverList(container, rows, mode) {
  if (!container) return;

  if (!Array.isArray(rows) || rows.length === 0) {
    container.innerHTML = `<div class="mover-empty">No data yet.</div>`;
    return;
  }

  container.innerHTML = rows
    .map((row) => {
      const symbol = escapeHtml(row.symbol || "-");
      const price = fmtPrice(row.price);
      const change = fmtPercent(row.change_24h);
      const changeNum = Number(row.change_24h);
      const changeClass = changeNum > 0 ? "pos" : changeNum < 0 ? "neg" : "";
      const volumeText = fmtMoney(row.volume_24h);
      const signal = escapeHtml(row.signal || "HOLD");
      const signalTone = signalClass(signal);
      const rightValue =
        mode === "volume"
          ? `<div class="mover-value">${volumeText}</div>`
          : `<div class="mover-value ${changeClass}">${change}</div>`;

      return `
        <div class="mover-item">
          <div>
            <div class="mover-symbol">${symbol}</div>
            <div class="mover-sub">${price} • <span class="signal ${signalTone}">${signal}</span></div>
          </div>
          ${rightValue}
        </div>
      `;
    })
    .join("");
}

function renderMovers(movers) {
  const safeMovers = movers || {};
  renderMoverList(moversGainersNode, safeMovers.gainers || [], "change");
  renderMoverList(moversLosersNode, safeMovers.losers || [], "change");
  renderMoverList(moversVolumeNode, safeMovers.volume || [], "volume");
}

function formatTapeTime(timestamp) {
  if (!timestamp) return "--:--:--";
  const date = new Date(Number(timestamp));
  return Number.isNaN(date.getTime())
    ? "--:--:--"
    : date.toLocaleTimeString([], { hour12: false });
}

function renderOrderbookSide(node, levels, side) {
  if (!node) return;

  if (!Array.isArray(levels) || levels.length === 0) {
    node.innerHTML = `<tr><td colspan="3" class="mini-empty">No levels.</td></tr>`;
    return;
  }

  const sideClass = side === "bid" ? "side-buy" : "side-sell";
  node.innerHTML = levels
    .map((level) => `
      <tr>
        <td class="${sideClass}">${fmtPrice(level.price)}</td>
        <td>${fmtQty(level.amount)}</td>
        <td>${fmtQty(level.total)}</td>
      </tr>
    `)
    .join("");
}

function renderRecentTrades(trades) {
  if (!recentTradesBody) return;

  if (!Array.isArray(trades) || trades.length === 0) {
    recentTradesBody.innerHTML = `<tr><td colspan="4" class="mini-empty">No recent trades.</td></tr>`;
    return;
  }

  recentTradesBody.innerHTML = trades
    .map((trade) => {
      const side = String(trade.side || "").toLowerCase();
      const sideLabel = side === "buy" ? "BUY" : side === "sell" ? "SELL" : "-";
      const sideClass = side === "buy" ? "side-buy" : side === "sell" ? "side-sell" : "";

      return `
        <tr>
          <td>${formatTapeTime(trade.timestamp)}</td>
          <td class="${sideClass}">${sideLabel}</td>
          <td>${fmtPrice(trade.price)}</td>
          <td>${fmtQty(trade.amount)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderOrderflow(orderflow) {
  const payload = orderflow || {};
  const orderbook = payload.orderbook || {};

  if (orderflowSymbolNode) {
    orderflowSymbolNode.textContent = payload.symbol || selectedSymbol || "-";
  }

  renderOrderbookSide(orderbookBidsBody, orderbook.bids || [], "bid");
  renderOrderbookSide(orderbookAsksBody, orderbook.asks || [], "ask");
  renderRecentTrades(payload.trades || []);

  const spread = Number(orderbook.spread);
  const spreadPct = Number(orderbook.spread_pct);
  const mid = Number(orderbook.mid);
  const hasSpread = !Number.isNaN(spread);
  const hasSpreadPct = !Number.isNaN(spreadPct);
  const hasMid = !Number.isNaN(mid);

  if (orderflowMetaNode) {
    if (hasSpread || hasMid) {
      const spreadText = hasSpread ? fmtPrice(spread) : "-";
      const spreadPctText = hasSpreadPct ? `${fmtNumber(spreadPct, 3)}%` : "-";
      const midText = hasMid ? fmtPrice(mid) : "-";
      orderflowMetaNode.textContent = `Spread: ${spreadText} (${spreadPctText}) • Mid: ${midText}`;
    } else {
      orderflowMetaNode.textContent = "Spread: -";
    }
  }

  if (orderflowErrorNode) {
    const message = typeof payload.error === "string" ? payload.error.trim() : "";
    if (message) {
      orderflowErrorNode.textContent = message;
      orderflowErrorNode.classList.add("show");
    } else {
      orderflowErrorNode.classList.remove("show");
    }
  }
}

function baseChartOptions(height) {
  return {
    width: 400,
    height,
    layout: {
      background: { type: "solid", color: "#081127" },
      textColor: "#9bb1d9",
    },
    grid: {
      vertLines: { color: "#1d2a44" },
      horzLines: { color: "#1d2a44" },
    },
    rightPriceScale: {
      borderColor: "#2b3d60",
    },
    timeScale: {
      borderColor: "#2b3d60",
      timeVisible: true,
      secondsVisible: false,
    },
    crosshair: {
      mode: 0,
    },
  };
}

function fitCharts() {
  if (!priceChart || !rsiChart || !macdChart) return;

  priceChart.applyOptions({ width: Math.max(priceChartContainer.clientWidth, 320) });
  rsiChart.applyOptions({ width: Math.max(rsiChartContainer.clientWidth, 320) });
  macdChart.applyOptions({ width: Math.max(macdChartContainer.clientWidth, 320) });
}

function initCharts() {
  if (!window.LightweightCharts) {
    throw new Error("Lightweight Charts failed to load");
  }

  priceChart = window.LightweightCharts.createChart(
    priceChartContainer,
    baseChartOptions(430),
  );

  candleSeries = priceChart.addCandlestickSeries({
    upColor: "#16c784",
    downColor: "#ff5b5b",
    borderUpColor: "#16c784",
    borderDownColor: "#ff5b5b",
    wickUpColor: "#16c784",
    wickDownColor: "#ff5b5b",
  });

  ema20Series = priceChart.addLineSeries({ color: "#f5a524", lineWidth: 2 });
  ema50Series = priceChart.addLineSeries({ color: "#60a5fa", lineWidth: 2 });

  volumeSeries = priceChart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "volume",
  });
  priceChart.priceScale("volume").applyOptions({
    scaleMargins: { top: 0.8, bottom: 0.0 },
  });

  rsiChart = window.LightweightCharts.createChart(
    rsiChartContainer,
    baseChartOptions(150),
  );
  rsiSeries = rsiChart.addLineSeries({ color: "#c084fc", lineWidth: 2 });
  rsiUpperSeries = rsiChart.addLineSeries({ color: "#64748b", lineWidth: 1 });
  rsiLowerSeries = rsiChart.addLineSeries({ color: "#64748b", lineWidth: 1 });

  macdChart = window.LightweightCharts.createChart(
    macdChartContainer,
    baseChartOptions(150),
  );
  macdSeries = macdChart.addLineSeries({ color: "#22c55e", lineWidth: 2 });
  macdSignalSeries = macdChart.addLineSeries({ color: "#fb923c", lineWidth: 2 });
  macdHistogramSeries = macdChart.addHistogramSeries({
    base: 0,
    priceFormat: { type: "price", precision: 6, minMove: 0.000001 },
  });

  fitCharts();
  window.addEventListener("resize", fitCharts);
}

function renderChart(chart) {
  if (!chart) return;

  candleSeries.setData(chart.candles || []);
  ema20Series.setData(chart.ema20 || []);
  ema50Series.setData(chart.ema50 || []);
  volumeSeries.setData(chart.volume || []);

  const rsiData = chart.rsi || [];
  rsiSeries.setData(rsiData);
  rsiUpperSeries.setData(rsiData.map((point) => ({ time: point.time, value: 70 })));
  rsiLowerSeries.setData(rsiData.map((point) => ({ time: point.time, value: 30 })));

  macdSeries.setData(chart.macd || []);
  macdSignalSeries.setData(chart.macd_signal || []);
  macdHistogramSeries.setData(chart.macd_histogram || []);

  priceChart.timeScale().fitContent();
  rsiChart.timeScale().fitContent();
  macdChart.timeScale().fitContent();
}

function getDirection(signal) {
  const manual = riskDirectionInput.value;
  if (manual === "long") return "LONG";
  if (manual === "short") return "SHORT";
  if (signal === "SELL") return "SHORT";
  return "LONG";
}

function setRiskOutput(node, value) {
  node.textContent = value;
}

function updateRiskPanel() {
  if (!currentSummary || currentSummary.price == null) {
    [
      riskOutDirection,
      riskOutRiskAmount,
      riskOutEntry,
      riskOutStopPrice,
      riskOutTpPrice,
      riskOutSize,
      riskOutNotional,
      riskOutRr,
      riskOutPnl,
    ].forEach((node) => setRiskOutput(node, "-"));
    return;
  }

  const entry = Number(currentSummary.price);
  const account = Math.max(Number(riskAccountInput.value) || 0, 0);
  const riskPct = Math.max(Number(riskPercentInput.value) || 0, 0);
  const stopPct = Math.max(Number(riskStopInput.value) || 0, 0.01);
  const tpPct = Math.max(Number(riskTpInput.value) || 0, 0.01);
  const leverage = Math.max(Number(riskLeverageInput.value) || 1, 1);

  const direction = getDirection(currentSummary.signal);
  const riskAmount = account * (riskPct / 100);

  let stopPrice;
  let takeProfitPrice;
  let riskPerUnit;
  let rewardPerUnit;

  if (direction === "SHORT") {
    stopPrice = entry * (1 + stopPct / 100);
    takeProfitPrice = entry * (1 - tpPct / 100);
    riskPerUnit = stopPrice - entry;
    rewardPerUnit = entry - takeProfitPrice;
  } else {
    stopPrice = entry * (1 - stopPct / 100);
    takeProfitPrice = entry * (1 + tpPct / 100);
    riskPerUnit = entry - stopPrice;
    rewardPerUnit = takeProfitPrice - entry;
  }

  const size = riskPerUnit > 0 ? riskAmount / riskPerUnit : 0;
  const notional = size * entry;
  const leveragedNotional = notional * leverage;
  const rr = riskPerUnit > 0 ? rewardPerUnit / riskPerUnit : 0;
  const potentialPnl = size * rewardPerUnit;

  setRiskOutput(riskOutDirection, direction);
  setRiskOutput(riskOutRiskAmount, fmtMoney(riskAmount));
  setRiskOutput(riskOutEntry, fmtPrice(entry));
  setRiskOutput(riskOutStopPrice, fmtPrice(stopPrice));
  setRiskOutput(riskOutTpPrice, fmtPrice(takeProfitPrice));
  setRiskOutput(riskOutSize, fmtNumber(size, 6));
  setRiskOutput(riskOutNotional, `${fmtMoney(notional)} (x${fmtNumber(leverage, 0)} = ${fmtMoney(leveragedNotional)})`);
  setRiskOutput(riskOutRr, `${fmtNumber(rr, 2)} : 1`);
  setRiskOutput(riskOutPnl, fmtMoney(potentialPnl));
}

function formatAlertTime(timestamp) {
  if (!timestamp) return "--:--:--";
  return new Date(Number(timestamp) * 1000).toLocaleTimeString();
}

function getVisibleAlerts() {
  return allAlerts.filter((alert) => Number(alert.id || 0) > alertCutoffId);
}

function renderAlerts() {
  const visibleAlerts = getVisibleAlerts();
  alertCountNode.textContent = `(${visibleAlerts.length})`;

  if (visibleAlerts.length === 0) {
    alertsListNode.innerHTML = `
      <article class="alert-item">
        <div class="alert-title">No alerts yet</div>
        <div class="alert-msg">Signal and RSI alerts will appear here in real time.</div>
      </article>
    `;
    return;
  }

  const reversed = [...visibleAlerts].reverse();
  alertsListNode.innerHTML = reversed
    .map((alert) => {
      const severity = escapeHtml(alert.severity || "low");
      const title = escapeHtml(alert.title || "Alert");
      const message = escapeHtml(alert.message || "");
      const symbol = escapeHtml(alert.symbol || "-");
      const id = escapeHtml(alert.id);
      const time = escapeHtml(formatAlertTime(alert.timestamp));

      return `
        <article class="alert-item ${severity}">
          <div class="alert-head">
            <span>${symbol}</span>
            <span>#${id} • ${time}</span>
          </div>
          <div class="alert-title">${title}</div>
          <div class="alert-msg">${message}</div>
        </article>
      `;
    })
    .join("");
}

function renderAutoTrade(payload) {
  const data = payload || {};
  const enabled = Boolean(data.enabled);
  const halted = Boolean(data.halted);
  const paper = Boolean(data.paper_trading);
  const exchange = String(data.exchange || "-");
  const openPositions = Number(data.open_positions || 0);
  const maxOpenPositions = Number(data.max_open_positions);
  const dailyPnl = Number(data.daily_pnl_usdt);
  const lossLimit = Number(data.daily_loss_limit_usdt);
  const tradeSizeUsdt = Number(data.trade_size_usdt);
  const tradeSizeUsdtMin = Number(data.trade_size_usdt_min);
  const tradeSizeUsdtMax = Number(data.trade_size_usdt_max);
  const tradeSizePercent = Number(data.trade_size_percent);
  const minNotionalUsdt = Number(data.min_notional_usdt);
  const strategyModeRaw = String(data.strategy_mode || "long_only");
  const shortEnabled = Boolean(data.short_enabled);
  const aiFilterEnabled = Boolean(data.ai_filter_enabled);
  const aiFilterMinConfidence = Number(data.ai_filter_min_confidence);
  const aiFilterMinScoreAbs = Number(data.ai_filter_min_score_abs);
  const symbols = Array.isArray(data.symbols) ? data.symbols : [];
  const selected = data.selected_position || null;
  const recentEvents = Array.isArray(data.recent_events) ? data.recent_events : [];
  const lastReason = String(data.last_reason || "-");
  const strategyLabel = strategyModeRaw === "both"
    ? "LONG+SHORT"
    : strategyModeRaw === "short_only"
      ? "SHORT ONLY"
      : "LONG ONLY";

  if (autoTradeStatusNode) {
    if (!enabled) {
      autoTradeStatusNode.textContent = "Disabled";
      autoTradeStatusNode.classList.remove("pos", "neg");
    } else if (halted) {
      autoTradeStatusNode.textContent = "Halted (Risk Limit)";
      autoTradeStatusNode.classList.add("neg");
      autoTradeStatusNode.classList.remove("pos");
    } else {
      autoTradeStatusNode.textContent = "Active";
      autoTradeStatusNode.classList.add("pos");
      autoTradeStatusNode.classList.remove("neg");
    }
  }

  if (autoTradeModeNode) {
    const modeText = paper ? "PAPER" : "LIVE";
    autoTradeModeNode.textContent =
      `${modeText} • ${exchange.toUpperCase()} • ${strategyLabel}`;
  }

  if (autoTradePnlNode) {
    const hasPnl = !Number.isNaN(dailyPnl);
    autoTradePnlNode.textContent = hasPnl ? `${dailyPnl.toFixed(2)} USDT` : "-";
    autoTradePnlNode.classList.remove("pos", "neg");
    if (hasPnl && dailyPnl > 0) autoTradePnlNode.classList.add("pos");
    if (hasPnl && dailyPnl < 0) autoTradePnlNode.classList.add("neg");
  }

  if (autoTradeRiskNode) {
    const limitText = Number.isNaN(lossLimit) ? "-" : `-${lossLimit.toFixed(2)} USDT`;
    const sizeRangeText =
      !Number.isNaN(tradeSizeUsdtMin) &&
      !Number.isNaN(tradeSizeUsdtMax) &&
      tradeSizeUsdtMax >= tradeSizeUsdtMin
        ? `${fmtNumber(tradeSizeUsdtMin, 2)}-${fmtNumber(tradeSizeUsdtMax, 2)} USDT`
        : (!Number.isNaN(tradeSizeUsdt) ? `${fmtNumber(tradeSizeUsdt, 2)} USDT` : "-");
    const sizeText = !Number.isNaN(tradeSizePercent) && tradeSizePercent > 0
      ? `${fmtNumber(tradeSizePercent, 2)}%`
      : sizeRangeText;
    const minText = Number.isNaN(minNotionalUsdt) ? "-" : `${fmtNumber(minNotionalUsdt, 2)} USDT`;
    const aiText = aiFilterEnabled
      ? `AI ON (>=${Number.isNaN(aiFilterMinConfidence) ? "-" : aiFilterMinConfidence}% / abs ${Number.isNaN(aiFilterMinScoreAbs) ? "-" : fmtNumber(aiFilterMinScoreAbs, 2)})`
      : "AI OFF";
    const shortText = shortEnabled ? "SHORT ON" : "SHORT OFF";
    const openText = Number.isNaN(maxOpenPositions) ? `${openPositions}` : `${openPositions}/${maxOpenPositions}`;
    autoTradeRiskNode.textContent = `Size ${sizeText} • Min ${minText} • Limit ${limitText} • ${shortText} • ${aiText} • Open ${openText}`;
  }

  if (autoTradeSymbolsNode) {
    autoTradeSymbolsNode.textContent = symbols.length > 0 ? symbols.join(", ") : "-";
  }

  if (autoTradeSelectedNode) {
    if (selected && selected.entry_price && selected.amount) {
      const side = String(selected.side || "LONG").toUpperCase();
      autoTradeSelectedNode.textContent =
        `${side} • Entry ${fmtPrice(selected.entry_price)} • Qty ${fmtQty(selected.amount)}`;
    } else {
      autoTradeSelectedNode.textContent = "No open position";
    }
  }

  if (autoTradeNoteNode) {
    autoTradeNoteNode.textContent = lastReason;
  }

  if (!autoTradeEventsNode) return;

  if (recentEvents.length === 0) {
    autoTradeEventsNode.innerHTML =
      `<tr><td colspan="6" class="mini-empty">No auto-trade events yet.</td></tr>`;
    return;
  }

  const events = [...recentEvents].reverse().slice(0, 20);
  autoTradeEventsNode.innerHTML = events
    .map((event) => {
      const ts = formatAlertTime(event.timestamp);
      const symbol = escapeHtml(event.symbol || "-");
      const action = escapeHtml(event.action || "-");
      const message = escapeHtml(event.message || "-");
      const pnl = event.pnl_usdt == null ? "-" : `${fmtNumber(event.pnl_usdt, 2)} USDT`;
      const mode = escapeHtml(String(event.mode || "-").toUpperCase());
      return `
        <tr>
          <td>${ts}</td>
          <td>${symbol}</td>
          <td>${action}</td>
          <td>${message}</td>
          <td>${pnl}</td>
          <td>${mode}</td>
        </tr>
      `;
    })
    .join("");
}

function renderWallet(payload) {
  const data = payload || {};
  const enabled = Boolean(data.enabled);
  const connected = Boolean(data.connected);
  const exchange = String(data.exchange || "-").toUpperCase();
  const totalUsdtEstimate = Number(data.total_usdt_estimate);
  const dailyPnlEstimateUsdt = Number(data.daily_pnl_estimate_usdt);
  const dailyPnlEstimatePct = Number(data.daily_pnl_estimate_pct);
  const dayStartTotalUsdt = Number(data.day_start_total_usdt);
  const pnlDayKey = String(data.pnl_day_key || "");
  const usdtFree = Number(data.usdt_free);
  const usdtTotal = Number(data.usdt_total);
  const assetCount = Number(data.asset_count || 0);
  const assets = Array.isArray(data.assets) ? data.assets : [];
  const message = typeof data.error === "string" ? data.error.trim() : "";

  if (walletStatusNode) {
    walletStatusNode.classList.remove("pos", "neg");
    if (!enabled) {
      walletStatusNode.textContent = "No API Key";
      walletStatusNode.classList.add("neg");
    } else if (!connected) {
      walletStatusNode.textContent = "Disconnected";
      walletStatusNode.classList.add("neg");
    } else {
      walletStatusNode.textContent = "Connected";
      walletStatusNode.classList.add("pos");
    }
  }

  if (walletExchangeNode) {
    walletExchangeNode.textContent = exchange;
  }

  if (walletTotalNode) {
    walletTotalNode.textContent = Number.isNaN(totalUsdtEstimate)
      ? "-"
      : fmtMoney(totalUsdtEstimate);
  }

  if (walletDailyPnlNode) {
    walletDailyPnlNode.classList.remove("pos", "neg");
    if (Number.isNaN(dailyPnlEstimateUsdt)) {
      walletDailyPnlNode.textContent = "-";
    } else {
      const absMoney = fmtMoney(Math.abs(dailyPnlEstimateUsdt));
      const moneyText = dailyPnlEstimateUsdt > 0
        ? `+${absMoney}`
        : dailyPnlEstimateUsdt < 0
          ? `-${absMoney}`
          : fmtMoney(0);
      const pctText = Number.isNaN(dailyPnlEstimatePct)
        ? ""
        : ` (${dailyPnlEstimatePct > 0 ? "+" : ""}${dailyPnlEstimatePct.toFixed(2)}%)`;
      walletDailyPnlNode.textContent = `${moneyText}${pctText}`;

      if (dailyPnlEstimateUsdt > 0) walletDailyPnlNode.classList.add("pos");
      if (dailyPnlEstimateUsdt < 0) walletDailyPnlNode.classList.add("neg");
    }
  }

  if (walletDayStartNode) {
    if (Number.isNaN(dayStartTotalUsdt)) {
      walletDayStartNode.textContent = "-";
    } else {
      const keyText = pnlDayKey ? ` (UTC ${pnlDayKey})` : "";
      walletDayStartNode.textContent = `${fmtMoney(dayStartTotalUsdt)}${keyText}`;
    }
  }

  if (walletUsdtNode) {
    const freeText = Number.isNaN(usdtFree) ? "-" : fmtMoney(usdtFree);
    const totalText = Number.isNaN(usdtTotal) ? "-" : fmtMoney(usdtTotal);
    walletUsdtNode.textContent = `${freeText} / ${totalText}`;
  }

  if (walletAssetCountNode) {
    walletAssetCountNode.textContent = Number.isNaN(assetCount) ? "-" : String(assetCount);
  }

  if (walletErrorNode) {
    if (message) {
      walletErrorNode.textContent = message;
      walletErrorNode.classList.add("show");
    } else {
      walletErrorNode.classList.remove("show");
    }
  }

  if (!walletAssetsBody) return;

  if (!connected || assets.length === 0) {
    const emptyText = message || "No wallet asset data available.";
    walletAssetsBody.innerHTML =
      `<tr><td colspan="5" class="mini-empty">${escapeHtml(emptyText)}</td></tr>`;
    return;
  }

  walletAssetsBody.innerHTML = assets
    .map((asset) => {
      const code = escapeHtml(asset.asset || "-");
      const free = fmtQty(asset.free);
      const used = fmtQty(asset.used);
      const total = fmtQty(asset.total);
      const estUsdt = asset.usdt_value == null ? "-" : fmtMoney(asset.usdt_value);

      return `
        <tr>
          <td>${code}</td>
          <td>${free}</td>
          <td>${used}</td>
          <td>${total}</td>
          <td>${estUsdt}</td>
        </tr>
      `;
    })
    .join("");
}

function playAlertSound() {
  if (!soundEnabled) return;

  try {
    if (!audioContext) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      audioContext = new Ctx();
    }

    const now = audioContext.currentTime;
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();

    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(880, now);

    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.08, now + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.15);

    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start(now);
    oscillator.stop(now + 0.16);
  } catch {
    // best effort only
  }
}

function sendBrowserNotifications(newAlerts) {
  if (!browserAlertEnabled || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;

  newAlerts.slice(-3).forEach((alert) => {
    const title = alert.title || `${alert.symbol || "Symbol"} alert`;
    const body = alert.message || "Market condition changed";
    try {
      new Notification(title, { body });
    } catch {
      // ignore
    }
  });
}

function handleIncomingAlerts(alerts) {
  allAlerts = Array.isArray(alerts) ? alerts : [];
  const visibleAlerts = getVisibleAlerts();

  const newAlerts = visibleAlerts.filter((alert) => {
    const id = Number(alert.id || 0);
    if (!id || seenAlertIds.has(id)) return false;
    seenAlertIds.add(id);
    return true;
  });

  if (newAlerts.length > 0) {
    playAlertSound();
    sendBrowserNotifications(newAlerts);
  }

  renderAlerts();
}

function sendViewUpdate() {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;

  ws.send(
    JSON.stringify({
      type: "set_view",
      symbol: selectedSymbol,
      timeframe: selectedTimeframe,
    }),
  );
}

function handleSnapshot(payload) {
  if (!payload || payload.type !== "snapshot") return;

  if (Array.isArray(payload.symbols)) {
    syncSymbolOptions(payload.symbols);
  }

  if (typeof payload.selected_symbol === "string") {
    selectedSymbol = payload.selected_symbol;
    symbolSelect.value = selectedSymbol;
  }

  if (typeof payload.timeframe === "string") {
    selectedTimeframe = payload.timeframe;
    setActiveTimeframeButton(selectedTimeframe);
  }

  marketRows = Array.isArray(payload.market) ? payload.market : [];
  renderWatchlist(marketRows);
  renderMovers(payload.movers);
  renderOrderflow(payload.orderflow);
  renderWallet(payload.wallet);

  const summary = payload.summary || marketRows.find((row) => row.symbol === selectedSymbol) || null;
  updateStats(summary);

  renderChart(payload.chart);
  handleIncomingAlerts(payload.alerts);
  renderAutoTrade(payload.auto_trade);

  if (typeof payload.error === "string" && payload.error.trim()) {
    setStreamBanner(payload.error, "warning");
  } else {
    hideStreamBanner();
  }

  lastSnapshotAt = Date.now();
  const time = payload.timestamp ? new Date(payload.timestamp * 1000) : new Date();
  lastUpdateNode.textContent = `Last update: ${time.toLocaleTimeString()}`;
}

function connectWebSocket() {
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.close();
  }

  setStatus("Connecting...", false);
  setStreamBanner("Connecting to live stream...", "info");
  lastUpdateNode.textContent = "Waiting for live tick...";

  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    reconnectAttempt = 0;
    setStatus("Connected", true);
    setStreamBanner("Live stream connected. Waiting for first tick...", "info");
    sendViewUpdate();
  };

  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);

      if (payload.type === "error") {
        setStatus("Live stream issue", false);
        setStreamBanner(
          payload.message || "Server error received from websocket stream.",
          "error-state",
        );
        lastUpdateNode.textContent = `Server error: ${payload.message || "Unknown"}`;
        return;
      }

      handleSnapshot(payload);
    } catch (error) {
      lastUpdateNode.textContent = `Parse error: ${error.message}`;
    }
  };

  ws.onerror = () => {
    setStatus("WebSocket error", false);
    setStreamBanner("Connection problem detected. Reconnecting...", "error-state");
  };

  ws.onclose = () => {
    reconnectAttempt += 1;
    const delayMs = Math.min(10000, 2000 * reconnectAttempt);

    setStatus(`Disconnected (retry ${reconnectAttempt})`, false);
    setStreamBanner(
      `Disconnected. Reconnecting in ${Math.ceil(delayMs / 1000)}s...`,
      "warning",
    );
    reconnectTimer = setTimeout(connectWebSocket, delayMs);
  };
}

symbolSelect.addEventListener("change", () => {
  selectedSymbol = symbolSelect.value;
  renderWatchlist(marketRows);
  sendViewUpdate();
});

timeframeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const timeframe = button.dataset.timeframe;
    if (!timeframe || timeframe === selectedTimeframe) return;

    selectedTimeframe = timeframe;
    setActiveTimeframeButton(selectedTimeframe);
    sendViewUpdate();
  });
});

[riskAccountInput, riskPercentInput, riskStopInput, riskTpInput, riskDirectionInput, riskLeverageInput].forEach((input) => {
  input.addEventListener("input", updateRiskPanel);
  input.addEventListener("change", updateRiskPanel);
});

toggleSoundBtn.addEventListener("click", () => {
  soundEnabled = !soundEnabled;
  toggleSoundBtn.classList.toggle("active", soundEnabled);
  toggleSoundBtn.textContent = soundEnabled ? "Sound ON" : "Sound OFF";
});

toggleBrowserAlertBtn.addEventListener("click", async () => {
  if (!("Notification" in window)) {
    lastUpdateNode.textContent = "Browser notifications are not supported in this browser.";
    return;
  }

  if (!browserAlertEnabled) {
    if (Notification.permission === "default") {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        lastUpdateNode.textContent = "Browser notification permission was denied.";
        browserAlertEnabled = false;
      } else {
        browserAlertEnabled = true;
      }
    } else if (Notification.permission === "granted") {
      browserAlertEnabled = true;
    } else {
      browserAlertEnabled = false;
      lastUpdateNode.textContent = "Browser notifications are blocked for this site.";
    }
  } else {
    browserAlertEnabled = false;
  }

  toggleBrowserAlertBtn.classList.toggle("active", browserAlertEnabled);
  toggleBrowserAlertBtn.textContent = browserAlertEnabled
    ? "Browser Alert ON"
    : "Browser Alert OFF";
});

clearAlertsBtn.addEventListener("click", () => {
  const maxId = allAlerts.reduce((max, alert) => {
    const id = Number(alert.id || 0);
    return id > max ? id : max;
  }, alertCutoffId);

  alertCutoffId = maxId;
  seenAlertIds = new Set(
    Array.from(seenAlertIds).filter((id) => id > alertCutoffId),
  );
  renderAlerts();
});

try {
  initCharts();
  renderAlerts();
  renderAutoTrade({});
  renderWallet({});
  updateRiskPanel();
  scheduleStaleCheck();
  connectWebSocket();
} catch (error) {
  setStatus("Init failed", false);
  setStreamBanner(error.message || "Initialization failed.", "error-state");
  lastUpdateNode.textContent = error.message;
}
