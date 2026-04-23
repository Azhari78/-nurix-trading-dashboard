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
const chartOverlaySpreadNode = document.getElementById("chart-ov-spread");
const chartOverlayEdgeNode = document.getElementById("chart-ov-edge");
const chartOverlayRankNode = document.getElementById("chart-ov-rank");

const execOutMarketState = document.getElementById("exec-out-market-state");
const execOutAiGate = document.getElementById("exec-out-ai-gate");
const execOutStrengthGate = document.getElementById("exec-out-strength-gate");
const execOutVolumeGate = document.getElementById("exec-out-volume-gate");
const execOutEmaGate = document.getElementById("exec-out-ema-gate");
const execOutMacdGate = document.getElementById("exec-out-macd-gate");
const execOutVolatilityGate = document.getElementById("exec-out-volatility-gate");
const execOutLongReady = document.getElementById("exec-out-long-ready");
const execOutShortReady = document.getElementById("exec-out-short-ready");
const execOutPositionCap = document.getElementById("exec-out-position-cap");
const execOutDailyRisk = document.getElementById("exec-out-daily-risk");
const execOutHalt = document.getElementById("exec-out-halt");
const execOutActivePosition = document.getElementById("exec-out-active-position");
const execOutNextAction = document.getElementById("exec-out-next-action");
const gateOutTracked = document.getElementById("gate-out-tracked");
const gateOutBlocked = document.getElementById("gate-out-blocked");
const gateOutReadyLong = document.getElementById("gate-out-ready-long");
const gateOutReadyShort = document.getElementById("gate-out-ready-short");
const gateOutAi = document.getElementById("gate-out-ai");
const gateOutStrength = document.getElementById("gate-out-strength");
const gateOutVolume = document.getElementById("gate-out-volume");
const gateOutEma = document.getElementById("gate-out-ema");
const gateOutMacd = document.getElementById("gate-out-macd");
const gateOutRsi = document.getElementById("gate-out-rsi");
const gateOutVolatility = document.getElementById("gate-out-volatility");
const gateOutTopBlocked = document.getElementById("gate-out-top-blocked");

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
const autoTradeAdaptiveNode = document.getElementById("auto-trade-adaptive");
const autoTradeCopyNode = document.getElementById("auto-trade-copy");
const autoTradeEventsNode = document.getElementById("auto-trade-events");
const copyTradeEventsNode = document.getElementById("copy-trade-events");
const tradeJournalBody = document.getElementById("trade-journal-body");
const exportJournalCsvBtn = document.getElementById("export-journal-csv");

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
let latestAutoTrade = {};
let latestChartPayload = null;
let latestChartTimeframe = "1m";
let chartPriceLines = [];
let crosshairSyncGuard = false;
let chartSeriesLookup = {
  price: new Map(),
  rsi: new Map(),
  macd: new Map(),
  priceTimes: [],
  rsiTimes: [],
  macdTimes: [],
};
let chartAutoFitArmed = true;
let lastChartViewKey = "";

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

function timeframeToSeconds(timeframe) {
  const tf = String(timeframe || "").toLowerCase().trim();
  if (tf.endsWith("m")) return Math.max(1, Number.parseInt(tf, 10) || 1) * 60;
  if (tf.endsWith("h")) return Math.max(1, Number.parseInt(tf, 10) || 1) * 3600;
  if (tf.endsWith("d")) return Math.max(1, Number.parseInt(tf, 10) || 1) * 86400;
  return 60;
}

function normalizeChartTime(rawTime) {
  if (typeof rawTime === "number" && Number.isFinite(rawTime)) return rawTime;
  if (rawTime && typeof rawTime === "object") {
    const year = Number(rawTime.year);
    const month = Number(rawTime.month);
    const day = Number(rawTime.day);
    if (
      Number.isFinite(year)
      && Number.isFinite(month)
      && Number.isFinite(day)
      && month >= 1
      && month <= 12
      && day >= 1
      && day <= 31
    ) {
      return Math.floor(Date.UTC(year, month - 1, day) / 1000);
    }
  }
  return Number.NaN;
}

function nearestTime(times, target, maxDeltaSeconds) {
  if (!Array.isArray(times) || times.length === 0 || !Number.isFinite(target)) return null;
  let best = null;
  let bestDelta = Number.POSITIVE_INFINITY;
  for (let i = 0; i < times.length; i += 1) {
    const t = Number(times[i]);
    if (!Number.isFinite(t)) continue;
    const delta = Math.abs(t - target);
    if (delta < bestDelta) {
      best = t;
      bestDelta = delta;
    }
  }
  if (!Number.isFinite(bestDelta) || bestDelta > maxDeltaSeconds) return null;
  return best;
}

function valueAtOrNear(map, times, target, maxDeltaSeconds) {
  if (!Number.isFinite(target)) return null;
  if (map.has(target)) {
    const exact = Number(map.get(target));
    return Number.isFinite(exact) ? exact : null;
  }
  const nearTime = nearestTime(times, target, maxDeltaSeconds);
  if (!Number.isFinite(nearTime)) return null;
  const nearValue = Number(map.get(nearTime));
  return Number.isFinite(nearValue) ? nearValue : null;
}

function clampValue(value, low, high) {
  return Math.max(low, Math.min(high, value));
}

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
      armChartAutoFit();
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
    updateExecutionPanel();
    updateChartOverlay();
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

  updateExecutionPanel();
  updateChartOverlay();
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

function setChartOverlayValue(node, text, tone = "") {
  if (!node) return;
  node.textContent = text;
  node.classList.remove("pos", "neg");
  if (tone === "pos") node.classList.add("pos");
  if (tone === "neg") node.classList.add("neg");
}

function estimateExpectedEdgePct(summary, autoTrade, side) {
  const spreadPct = Number(summary?.spread_pct);
  const spreadCostPct = Number.isFinite(spreadPct) ? Math.max(0, spreadPct) : 0;
  const feePct = Number(autoTrade?.estimated_fee_pct);
  const slippagePct = Number(autoTrade?.estimated_slippage_pct);
  const totalCostPct = spreadCostPct
    + (Number.isFinite(feePct) ? feePct : 0)
    + (Number.isFinite(slippagePct) ? slippagePct : 0);
  const takeProfitPct = side === "SHORT"
    ? Number(autoTrade?.short_take_profit_pct)
    : Number(autoTrade?.long_take_profit_pct);
  if (!Number.isFinite(takeProfitPct)) return null;
  return takeProfitPct - totalCostPct;
}

function estimateRankScore(summary, autoTrade, side) {
  if (!summary || typeof summary !== "object") return null;

  const aiConfidence = Number(summary.ai_confidence) || 0;
  const aiScoreAbs = Math.abs(Number(summary.ai_score) || 0);
  const strengthConfidence = Number(summary.strength_confidence) || 0;
  const volumeRatio = Number(summary.volume_ratio) || 0;
  const atrPct = Number(summary.atr_pct) || 0;
  const spreadPct = Number(summary.spread_pct);
  const rsi = Number(summary.rsi);
  const macd = Number(summary.macd);
  const macdSignal = Number(summary.macd_signal);

  let score = 0;
  score += aiConfidence * 0.42;
  score += strengthConfidence * 0.28;
  score += clampValue(volumeRatio, 0, 2) * 20;
  score += clampValue(aiScoreAbs, 0, 5) * 6;

  if (atrPct > 0) {
    const atrTargetRaw = Number(autoTrade?.target_atr_pct);
    const atrTarget = Math.max(0.05, Number.isFinite(atrTargetRaw) ? atrTargetRaw : 0.9);
    const atrFit = 1 - Math.min(Math.abs(atrPct - atrTarget) / atrTarget, 1);
    score += atrFit * 10;
  }

  if (Number.isFinite(spreadPct)) {
    score -= Math.max(0, spreadPct) * 120;
  }

  if (Number.isFinite(rsi)) {
    const isShort = side === "SHORT";
    const bandMinRaw = Number(isShort ? autoTrade?.short_rsi_min : autoTrade?.long_rsi_min);
    const bandMaxRaw = Number(isShort ? autoTrade?.short_rsi_max : autoTrade?.long_rsi_max);
    const defaultBandMin = isShort ? 60 : 45;
    const defaultBandMax = isShort ? 75 : 60;
    const bandMin = Number.isFinite(bandMinRaw) ? bandMinRaw : defaultBandMin;
    const bandMax = Number.isFinite(bandMaxRaw) ? bandMaxRaw : defaultBandMax;
    const bandMid = (bandMin + bandMax) / 2;
    const bandHalf = Math.max(1, (bandMax - bandMin) / 2);
    const rsiFit = Math.max(0, 1 - Math.abs(rsi - bandMid) / bandHalf);
    score += rsiFit * 8;
  }

  if (Number.isFinite(macd) && Number.isFinite(macdSignal)) {
    const macdSpread = macd - macdSignal;
    const directionalSpread = side === "SHORT" ? -macdSpread : macdSpread;
    score += clampValue(directionalSpread * 40, -6, 6);
  }

  return score;
}

function updateChartOverlay() {
  if (!chartOverlaySpreadNode || !chartOverlayEdgeNode || !chartOverlayRankNode) return;

  const summary = currentSummary;
  const autoTrade = latestAutoTrade || {};
  if (!summary || typeof summary !== "object") {
    setChartOverlayValue(chartOverlaySpreadNode, "Spread: -");
    setChartOverlayValue(chartOverlayEdgeNode, "Edge L/S: -");
    setChartOverlayValue(chartOverlayRankNode, "Rank L/S: -");
    return;
  }

  const spreadPct = Number(summary.spread_pct);
  const maxSpreadPct = Number(autoTrade.max_spread_pct);
  const spreadTone = Number.isFinite(spreadPct) && Number.isFinite(maxSpreadPct) && spreadPct > maxSpreadPct
    ? "neg"
    : Number.isFinite(spreadPct)
      ? "pos"
      : "";
  setChartOverlayValue(
    chartOverlaySpreadNode,
    `Spread: ${Number.isFinite(spreadPct) ? `${fmtNumber(spreadPct, 3)}%` : "-"}`,
    spreadTone,
  );

  const longEdge = estimateExpectedEdgePct(summary, autoTrade, "LONG");
  const shortEdge = estimateExpectedEdgePct(summary, autoTrade, "SHORT");
  const bestEdge = Math.max(
    Number.isFinite(longEdge) ? longEdge : Number.NEGATIVE_INFINITY,
    Number.isFinite(shortEdge) ? shortEdge : Number.NEGATIVE_INFINITY,
  );
  const minEdge = Number(autoTrade.min_edge_pct);
  const edgeTone = Number.isFinite(bestEdge) && Number.isFinite(minEdge) && bestEdge < minEdge
    ? "neg"
    : Number.isFinite(bestEdge)
      ? "pos"
      : "";
  setChartOverlayValue(
    chartOverlayEdgeNode,
    `Edge L/S: ${Number.isFinite(longEdge) ? `${fmtNumber(longEdge, 3)}%` : "-"} / ${
      Number.isFinite(shortEdge) ? `${fmtNumber(shortEdge, 3)}%` : "-"
    }`,
    edgeTone,
  );

  const longRank = estimateRankScore(summary, autoTrade, "LONG");
  const shortRank = estimateRankScore(summary, autoTrade, "SHORT");
  setChartOverlayValue(
    chartOverlayRankNode,
    `Rank L/S: ${Number.isFinite(longRank) ? fmtNumber(longRank, 1) : "-"} / ${
      Number.isFinite(shortRank) ? fmtNumber(shortRank, 1) : "-"
    }`,
  );
}

function armChartAutoFit() {
  chartAutoFitArmed = true;
}

function applySmartChartFit(chart) {
  if (!priceChart || !rsiChart || !macdChart) return;
  const symbol = String(chart?.symbol || selectedSymbol || "");
  const timeframe = String(chart?.timeframe || selectedTimeframe || "1m");
  const viewKey = `${symbol}:${timeframe}`;
  if (!chartAutoFitArmed && viewKey === lastChartViewKey) return;

  priceChart.timeScale().fitContent();
  rsiChart.timeScale().fitContent();
  macdChart.timeScale().fitContent();
  lastChartViewKey = viewKey;
  chartAutoFitArmed = false;
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

function clearChartPriceLines() {
  if (!candleSeries || !Array.isArray(chartPriceLines)) return;
  chartPriceLines.forEach((line) => {
    try {
      candleSeries.removePriceLine(line);
    } catch {
      // ignore stale refs
    }
  });
  chartPriceLines = [];
}

function addChartPriceLine(price, title, color, lineStyle = 2) {
  if (!candleSeries || !Number.isFinite(price) || price <= 0) return;
  const line = candleSeries.createPriceLine({
    price,
    color,
    lineWidth: 1,
    lineStyle,
    axisLabelVisible: true,
    title,
  });
  chartPriceLines.push(line);
}

function rebuildChartSeriesLookup(chart) {
  const priceMap = new Map();
  const rsiMap = new Map();
  const macdMap = new Map();
  const priceTimes = [];
  const rsiTimes = [];
  const macdTimes = [];

  const candles = Array.isArray(chart?.candles) ? chart.candles : [];
  candles.forEach((point) => {
    const t = normalizeChartTime(point?.time);
    const close = Number(point?.close);
    if (!Number.isFinite(t) || !Number.isFinite(close)) return;
    priceMap.set(t, close);
    priceTimes.push(t);
  });

  const rsiData = Array.isArray(chart?.rsi) ? chart.rsi : [];
  rsiData.forEach((point) => {
    const t = normalizeChartTime(point?.time);
    const value = Number(point?.value);
    if (!Number.isFinite(t) || !Number.isFinite(value)) return;
    rsiMap.set(t, value);
    rsiTimes.push(t);
  });

  const macdData = Array.isArray(chart?.macd) ? chart.macd : [];
  macdData.forEach((point) => {
    const t = normalizeChartTime(point?.time);
    const value = Number(point?.value);
    if (!Number.isFinite(t) || !Number.isFinite(value)) return;
    macdMap.set(t, value);
    macdTimes.push(t);
  });

  chartSeriesLookup = {
    price: priceMap,
    rsi: rsiMap,
    macd: macdMap,
    priceTimes,
    rsiTimes,
    macdTimes,
  };
}

function markerForJournalEntry(entry) {
  const eventType = String(entry?.event_type || "").toUpperCase();
  const side = String(entry?.side || "").toUpperCase();
  const pnl = Number(entry?.pnl_usdt);
  const reason = String(entry?.reason || "").toUpperCase();
  const isProfit = Number.isFinite(pnl) ? pnl >= 0 : true;

  if (eventType === "ENTRY") {
    if (side === "SHORT") {
      return {
        position: "aboveBar",
        shape: "arrowDown",
        color: "#f97316",
        text: "ENTRY S",
      };
    }
    return {
      position: "belowBar",
      shape: "arrowUp",
      color: "#22c55e",
      text: "ENTRY L",
    };
  }

  if (eventType === "PARTIAL_EXIT") {
    return {
      position: side === "SHORT" ? "belowBar" : "aboveBar",
      shape: "circle",
      color: isProfit ? "#10b981" : "#ef4444",
      text: "PARTIAL",
    };
  }

  if (eventType === "EXIT") {
    if (reason.includes("TIME STOP")) {
      return {
        position: side === "SHORT" ? "belowBar" : "aboveBar",
        shape: "square",
        color: "#f59e0b",
        text: "TIME STOP",
      };
    }
    return {
      position: side === "SHORT" ? "belowBar" : "aboveBar",
      shape: side === "SHORT" ? "arrowUp" : "arrowDown",
      color: isProfit ? "#10b981" : "#ef4444",
      text: "EXIT",
    };
  }

  return null;
}

function buildTradeMarkers(chart, autoTrade) {
  const candles = Array.isArray(chart?.candles) ? chart.candles : [];
  const latestTimeframe = String(chart?.timeframe || latestChartTimeframe || "1m");
  const candleTimes = candles
    .map((point) => normalizeChartTime(point?.time))
    .filter((value) => Number.isFinite(value));
  if (candleTimes.length === 0) return [];

  const maxDelta = Math.max(60, timeframeToSeconds(latestTimeframe) * 2);
  const selected = String(chart?.symbol || selectedSymbol || "").toUpperCase();
  const journal = Array.isArray(autoTrade?.recent_journal) ? autoTrade.recent_journal : [];
  const scoped = journal
    .filter((entry) => String(entry?.symbol || "").toUpperCase() === selected)
    .filter((entry) => {
      const type = String(entry?.event_type || "").toUpperCase();
      return type === "ENTRY" || type === "PARTIAL_EXIT" || type === "EXIT";
    })
    .slice(-80);

  return scoped
    .map((entry) => {
      const rawTs = Number(entry?.timestamp);
      const mappedTime = nearestTime(candleTimes, rawTs, maxDelta);
      if (!Number.isFinite(rawTs) || !Number.isFinite(mappedTime)) return null;
      const marker = markerForJournalEntry(entry);
      if (!marker) return null;
      return {
        time: mappedTime,
        ...marker,
      };
    })
    .filter(Boolean);
}

function drawPositionGuides(autoTrade) {
  clearChartPriceLines();
  const position = autoTrade?.selected_position;
  if (!position || typeof position !== "object") return;

  const side = String(position.side || "LONG").toUpperCase();
  const entryPrice = Number(position.entry_price);
  if (!Number.isFinite(entryPrice) || entryPrice <= 0) return;

  const stopPct = side === "SHORT"
    ? Number(autoTrade.short_stop_loss_pct)
    : Number(autoTrade.long_stop_loss_pct);
  const takePct = side === "SHORT"
    ? Number(autoTrade.short_take_profit_pct)
    : Number(autoTrade.long_take_profit_pct);
  const breakEvenEnabled = Boolean(autoTrade.break_even_enabled);
  const breakEvenArmed = Boolean(position.break_even_armed);
  const breakEvenBufferPct = Number(autoTrade.break_even_buffer_pct);

  addChartPriceLine(entryPrice, "ENTRY", "#38bdf8", 2);
  if (Number.isFinite(stopPct) && stopPct > 0) {
    const stopPrice = side === "SHORT"
      ? entryPrice * (1 + stopPct / 100)
      : entryPrice * (1 - stopPct / 100);
    addChartPriceLine(stopPrice, "SL", "#ef4444", 0);
  }
  if (Number.isFinite(takePct) && takePct > 0) {
    const takePrice = side === "SHORT"
      ? entryPrice * (1 - takePct / 100)
      : entryPrice * (1 + takePct / 100);
    addChartPriceLine(takePrice, "TP", "#22c55e", 0);
  }
  if (breakEvenEnabled && breakEvenArmed && Number.isFinite(breakEvenBufferPct)) {
    const bePrice = side === "SHORT"
      ? entryPrice * (1 - breakEvenBufferPct / 100)
      : entryPrice * (1 + breakEvenBufferPct / 100);
    addChartPriceLine(bePrice, "BE", "#f59e0b", 1);
  }
}

function refreshChartDecorations() {
  if (!candleSeries || !latestChartPayload) {
    clearChartPriceLines();
    return;
  }
  const markers = buildTradeMarkers(latestChartPayload, latestAutoTrade);
  candleSeries.setMarkers(markers);
  drawPositionGuides(latestAutoTrade);
}

function setupCrosshairSync() {
  if (
    typeof priceChart?.setCrosshairPosition !== "function"
    || typeof priceChart?.clearCrosshairPosition !== "function"
    || typeof rsiChart?.setCrosshairPosition !== "function"
    || typeof rsiChart?.clearCrosshairPosition !== "function"
    || typeof macdChart?.setCrosshairPosition !== "function"
    || typeof macdChart?.clearCrosshairPosition !== "function"
  ) {
    return;
  }

  const syncTarget = (targetChart, targetSeries, valueKey, sourceTime) => {
    const tfSeconds = timeframeToSeconds(latestChartTimeframe);
    const value = valueAtOrNear(
      chartSeriesLookup[valueKey],
      chartSeriesLookup[`${valueKey}Times`],
      sourceTime,
      Math.max(60, tfSeconds * 2),
    );
    if (!Number.isFinite(value)) {
      targetChart.clearCrosshairPosition();
      return;
    }
    targetChart.setCrosshairPosition(value, sourceTime, targetSeries);
  };

  const clearTargets = (targets) => {
    targets.forEach((target) => {
      target.clearCrosshairPosition();
    });
  };

  const wire = (sourceChart, targets) => {
    sourceChart.subscribeCrosshairMove((param) => {
      if (crosshairSyncGuard) return;
      const sourceTime = normalizeChartTime(param?.time);
      crosshairSyncGuard = true;
      try {
        if (!Number.isFinite(sourceTime)) {
          clearTargets(targets.map((target) => target.chart));
          return;
        }
        targets.forEach((target) => {
          syncTarget(target.chart, target.series, target.valueKey, sourceTime);
        });
      } finally {
        crosshairSyncGuard = false;
      }
    });
  };

  wire(priceChart, [
    { chart: rsiChart, series: rsiSeries, valueKey: "rsi" },
    { chart: macdChart, series: macdSeries, valueKey: "macd" },
  ]);
  wire(rsiChart, [
    { chart: priceChart, series: candleSeries, valueKey: "price" },
    { chart: macdChart, series: macdSeries, valueKey: "macd" },
  ]);
  wire(macdChart, [
    { chart: priceChart, series: candleSeries, valueKey: "price" },
    { chart: rsiChart, series: rsiSeries, valueKey: "rsi" },
  ]);
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

  setupCrosshairSync();
  fitCharts();
  window.addEventListener("resize", fitCharts);
}

function renderChart(chart) {
  if (!chart) return;
  latestChartPayload = chart;
  latestChartTimeframe = String(chart.timeframe || selectedTimeframe || "1m");

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
  rebuildChartSeriesLookup(chart);
  refreshChartDecorations();
  updateChartOverlay();

  applySmartChartFit(chart);
}

function setExecutionOutput(node, value) {
  if (!node) return;
  node.textContent = value;
}

function setExecutionGateTone(node, isPass) {
  if (!node) return;
  node.classList.remove("pos", "neg");
  if (isPass === true) node.classList.add("pos");
  if (isPass === false) node.classList.add("neg");
}

function setExecutionNumericTone(node, numericValue) {
  if (!node) return;
  node.classList.remove("pos", "neg");
  if (!Number.isFinite(numericValue)) return;
  if (numericValue > 0) node.classList.add("pos");
  if (numericValue < 0) node.classList.add("neg");
}

function updateExecutionPanel() {
  const requiredNodes = [
    execOutMarketState,
    execOutAiGate,
    execOutStrengthGate,
    execOutVolumeGate,
    execOutEmaGate,
    execOutMacdGate,
    execOutVolatilityGate,
    execOutLongReady,
    execOutShortReady,
    execOutPositionCap,
    execOutDailyRisk,
    execOutHalt,
    execOutActivePosition,
    execOutNextAction,
  ];
  if (requiredNodes.some((node) => !node)) return;

  requiredNodes.forEach((node) => {
    setExecutionOutput(node, "-");
    node.classList.remove("pos", "neg");
  });

  if (!currentSummary) return;

  const auto = latestAutoTrade || {};
  const autoEnabled = Boolean(auto.enabled);
  const halted = Boolean(auto.halted);
  const strategyMode = String(auto.strategy_mode || "long_only");
  const shortEnabled = Boolean(auto.short_enabled);
  const allowLong = strategyMode !== "short_only";
  const allowShort = shortEnabled && strategyMode !== "long_only";
  const sessionBlocked = (
    Boolean(auto.session_filter_enabled)
    && String(auto.last_reason || "").toLowerCase().includes("session filter active")
  );

  const signal = String(currentSummary.signal || "HOLD").toUpperCase();
  const rsi = Number(currentSummary.rsi);
  const aiBias = String(currentSummary.ai_bias || "HOLD").toUpperCase();
  const aiConfidence = Number(currentSummary.ai_confidence);
  const aiScore = Number(currentSummary.ai_score);
  const strengthConfidence = Number(currentSummary.strength_confidence);
  const volumeRatio = Number(currentSummary.volume_ratio);
  const ema20 = Number(currentSummary.ema20);
  const ema50 = Number(currentSummary.ema50);
  const macd = Number(currentSummary.macd);
  const macdSignal = Number(currentSummary.macd_signal);
  const atrPct = Number(currentSummary.atr_pct);
  const change24h = Number(currentSummary.change_24h);
  const livePrice = Number(currentSummary.price);

  const minStrength = Number(auto.min_strength_confidence);
  const minVolumeRatio = Number(auto.min_volume_ratio);
  const aiFilterEnabled = Boolean(auto.ai_filter_enabled);
  const adaptiveMinConf = Number(auto.adaptive_ai_min_confidence);
  const fixedMinConf = Number(auto.ai_filter_min_confidence);
  const minAiConfidence = (
    Number.isFinite(adaptiveMinConf) && adaptiveMinConf > 0
      ? adaptiveMinConf
      : (Number.isFinite(fixedMinConf) ? fixedMinConf : 0)
  );
  const minAiScoreAbsRaw = Number(auto.ai_filter_min_score_abs);
  const minAiScoreAbs = Number.isFinite(minAiScoreAbsRaw) ? minAiScoreAbsRaw : 0;
  const emaConfirm = Boolean(auto.entry_confirm_ema_stack);
  const macdConfirm = Boolean(auto.entry_confirm_macd);
  const volBlockEnabled = Boolean(auto.extreme_volatility_block_enabled);
  const maxAtrPct = Number(auto.max_atr_pct);
  const maxAbsChangePct = Number(auto.max_abs_change_24h_pct);

  const aiCommonPass = (
    !aiFilterEnabled
    || (
      Number.isFinite(aiConfidence)
      && aiConfidence >= minAiConfidence
      && Math.abs(Number.isFinite(aiScore) ? aiScore : 0) >= minAiScoreAbs
    )
  );
  const longAiPass = !aiFilterEnabled || (aiCommonPass && aiBias === "BUY");
  const shortAiPass = !aiFilterEnabled || (aiCommonPass && aiBias === "SELL");

  const strengthPass = (
    !Number.isFinite(minStrength)
    || minStrength <= 0
    || (Number.isFinite(strengthConfidence) && strengthConfidence >= minStrength)
  );
  const volumePass = (
    !Number.isFinite(minVolumeRatio)
    || minVolumeRatio <= 0
    || (Number.isFinite(volumeRatio) && volumeRatio >= minVolumeRatio)
  );

  const longEmaPass = !emaConfirm || (
    Number.isFinite(ema20) && Number.isFinite(ema50) && ema20 > ema50
  );
  const shortEmaPass = !emaConfirm || (
    Number.isFinite(ema20) && Number.isFinite(ema50) && ema20 < ema50
  );
  const longMacdPass = !macdConfirm || (
    Number.isFinite(macd) && Number.isFinite(macdSignal) && macd >= macdSignal
  );
  const shortMacdPass = !macdConfirm || (
    Number.isFinite(macd) && Number.isFinite(macdSignal) && macd <= macdSignal
  );

  const atrPass = (
    !volBlockEnabled
    || !Number.isFinite(maxAtrPct)
    || maxAtrPct <= 0
    || (Number.isFinite(atrPct) && atrPct <= maxAtrPct)
  );
  const changePass = (
    !volBlockEnabled
    || !Number.isFinite(maxAbsChangePct)
    || maxAbsChangePct <= 0
    || (Number.isFinite(change24h) && Math.abs(change24h) <= maxAbsChangePct)
  );
  const volatilityPass = atrPass && changePass;

  const openPositions = Number(auto.open_positions || 0);
  const maxOpenPositions = Number(auto.max_open_positions);
  const positionCapPass = (
    !Number.isFinite(maxOpenPositions)
    || maxOpenPositions <= 0
    || openPositions < maxOpenPositions
  );

  const dailyLossLimit = Number(auto.daily_loss_limit_usdt);
  const dailyPnl = Number(auto.daily_pnl_usdt);
  const riskUsed = Number.isFinite(dailyPnl) ? Math.max(0, -dailyPnl) : Number.NaN;
  const riskLeft = (
    Number.isFinite(dailyLossLimit) && Number.isFinite(riskUsed)
      ? Math.max(0, dailyLossLimit - riskUsed)
      : Number.NaN
  );
  const dailyRiskKnown = Number.isFinite(riskLeft);
  const dailyRiskPass = !dailyRiskKnown || riskLeft > 0;

  const baseReady = (
    autoEnabled
    && !halted
    && !sessionBlocked
    && positionCapPass
    && strengthPass
    && volumePass
    && volatilityPass
  );
  const longReady = baseReady && allowLong && longAiPass && longEmaPass && longMacdPass;
  const shortReady = baseReady && allowShort && shortAiPass && shortEmaPass && shortMacdPass;

  const buildBlockedReasons = (side) => {
    const reasons = [];
    if (!autoEnabled) reasons.push("engine off");
    if (halted) reasons.push("halted");
    if (sessionBlocked) reasons.push("session");
    if (!positionCapPass) reasons.push("position cap");
    if (!strengthPass) reasons.push("strength");
    if (!volumePass) reasons.push("volume");
    if (!volatilityPass) reasons.push("volatility");
    if (side === "LONG") {
      if (!allowLong) reasons.push("mode");
      if (!longAiPass) reasons.push("AI");
      if (!longEmaPass) reasons.push("EMA");
      if (!longMacdPass) reasons.push("MACD");
    } else {
      if (!allowShort) reasons.push("mode");
      if (!shortAiPass) reasons.push("AI");
      if (!shortEmaPass) reasons.push("EMA");
      if (!shortMacdPass) reasons.push("MACD");
    }
    return reasons.slice(0, 4).join(", ");
  };

  let activePositionText = "No open position on selected symbol";
  let activePositionPnl = Number.NaN;
  const selectedPosition = auto.selected_position && typeof auto.selected_position === "object"
    ? auto.selected_position
    : null;
  if (selectedPosition) {
    const positionSide = String(selectedPosition.side || "-").toUpperCase();
    const entryPrice = Number(selectedPosition.entry_price);
    const amount = Number(selectedPosition.amount);
    if (
      Number.isFinite(livePrice)
      && Number.isFinite(entryPrice)
      && Number.isFinite(amount)
      && amount > 0
      && (positionSide === "LONG" || positionSide === "SHORT")
    ) {
      activePositionPnl = positionSide === "SHORT"
        ? (entryPrice - livePrice) * amount
        : (livePrice - entryPrice) * amount;
    }
    const pnlText = Number.isFinite(activePositionPnl) ? fmtMoney(activePositionPnl) : "-";
    activePositionText = (
      `${positionSide} @ ${fmtPrice(entryPrice)} • qty ${fmtQty(amount)} • PnL ${pnlText}`
    );
  }

  const aiGateText = aiFilterEnabled
    ? (
      `LONG ${longAiPass ? "OK" : "BLOCK"} / SHORT ${shortAiPass ? "OK" : "BLOCK"} • `
      + `bias ${aiBias} • conf ${fmtNumber(aiConfidence, 0)}% >= ${fmtNumber(minAiConfidence, 0)}% `
      + `• |score| ${fmtNumber(Math.abs(Number.isFinite(aiScore) ? aiScore : 0), 2)} >= ${fmtNumber(minAiScoreAbs, 2)}`
    )
    : "OFF";
  const strengthGateText = (
    Number.isFinite(minStrength) && minStrength > 0
      ? `${strengthPass ? "PASS" : "BLOCK"} • ${fmtNumber(strengthConfidence, 0)}% / min ${fmtNumber(minStrength, 0)}%`
      : "OFF"
  );
  const volumeGateText = (
    Number.isFinite(minVolumeRatio) && minVolumeRatio > 0
      ? `${volumePass ? "PASS" : "BLOCK"} • ${fmtNumber(volumeRatio, 2)} / min ${fmtNumber(minVolumeRatio, 2)}`
      : "OFF"
  );
  const emaGateText = emaConfirm
    ? `LONG ${longEmaPass ? "OK" : "BLOCK"} / SHORT ${shortEmaPass ? "OK" : "BLOCK"}`
    : "OFF";
  const macdGateText = macdConfirm
    ? `LONG ${longMacdPass ? "OK" : "BLOCK"} / SHORT ${shortMacdPass ? "OK" : "BLOCK"}`
    : "OFF";
  const volatilityGateText = volBlockEnabled
    ? (
      `${volatilityPass ? "PASS" : "BLOCK"} • ATR ${fmtNumber(atrPct, 2)}% / ${fmtNumber(maxAtrPct, 2)}% `
      + `• 24h ${fmtPercent(change24h)} / ±${fmtNumber(maxAbsChangePct, 2)}%`
    )
    : "OFF";

  setExecutionOutput(
    execOutMarketState,
    `${signal} • RSI ${fmtNumber(rsi, 2)} • AI ${aiBias} (${fmtNumber(aiConfidence, 0)}%)`,
  );
  setExecutionOutput(execOutAiGate, aiGateText);
  setExecutionOutput(execOutStrengthGate, strengthGateText);
  setExecutionOutput(execOutVolumeGate, volumeGateText);
  setExecutionOutput(execOutEmaGate, emaGateText);
  setExecutionOutput(execOutMacdGate, macdGateText);
  setExecutionOutput(execOutVolatilityGate, volatilityGateText);
  setExecutionOutput(
    execOutLongReady,
    longReady ? "READY" : `BLOCKED • ${buildBlockedReasons("LONG") || "-"}`,
  );
  setExecutionOutput(
    execOutShortReady,
    shortReady ? "READY" : `BLOCKED • ${buildBlockedReasons("SHORT") || "-"}`,
  );
  setExecutionOutput(
    execOutPositionCap,
    `${positionCapPass ? "PASS" : "BLOCK"} • ${openPositions}/${fmtNumber(maxOpenPositions, 0)}`,
  );
  setExecutionOutput(
    execOutDailyRisk,
    Number.isFinite(riskLeft)
      ? `${dailyRiskPass ? "PASS" : "BLOCK"} • left ${fmtMoney(riskLeft)} / ${fmtMoney(dailyLossLimit)}`
      : "-",
  );
  setExecutionOutput(
    execOutHalt,
    halted
      ? `HALTED • ${String(auto.halt_reason || "Risk guard active")}`
      : "RUNNING",
  );
  setExecutionOutput(execOutActivePosition, activePositionText);
  setExecutionOutput(execOutNextAction, String(auto.last_reason || "Waiting for market conditions"));

  execOutMarketState.classList.remove("pos", "neg");
  if (signal.includes("BUY")) execOutMarketState.classList.add("pos");
  if (signal.includes("SELL")) execOutMarketState.classList.add("neg");

  const aiGatePassForMode = (allowLong && longAiPass) || (allowShort && shortAiPass);
  const emaGatePassForMode = (!allowLong || longEmaPass) && (!allowShort || shortEmaPass);
  const macdGatePassForMode = (!allowLong || longMacdPass) && (!allowShort || shortMacdPass);

  setExecutionGateTone(execOutAiGate, aiFilterEnabled ? aiGatePassForMode : null);
  setExecutionGateTone(
    execOutStrengthGate,
    Number.isFinite(minStrength) && minStrength > 0 ? strengthPass : null,
  );
  setExecutionGateTone(
    execOutVolumeGate,
    Number.isFinite(minVolumeRatio) && minVolumeRatio > 0 ? volumePass : null,
  );
  setExecutionGateTone(execOutEmaGate, emaConfirm ? emaGatePassForMode : null);
  setExecutionGateTone(execOutMacdGate, macdConfirm ? macdGatePassForMode : null);
  setExecutionGateTone(execOutVolatilityGate, volBlockEnabled ? volatilityPass : null);
  setExecutionGateTone(execOutLongReady, longReady);
  setExecutionGateTone(execOutShortReady, shortReady);
  setExecutionGateTone(execOutPositionCap, positionCapPass);
  setExecutionGateTone(execOutDailyRisk, dailyRiskKnown ? dailyRiskPass : null);
  setExecutionGateTone(execOutHalt, !halted);
  setExecutionNumericTone(execOutActivePosition, activePositionPnl);
}

function updateGateFailAnalytics() {
  const requiredNodes = [
    gateOutTracked,
    gateOutBlocked,
    gateOutReadyLong,
    gateOutReadyShort,
    gateOutAi,
    gateOutStrength,
    gateOutVolume,
    gateOutEma,
    gateOutMacd,
    gateOutRsi,
    gateOutVolatility,
    gateOutTopBlocked,
  ];
  if (requiredNodes.some((node) => !node)) return;

  requiredNodes.forEach((node) => {
    node.textContent = "-";
    node.classList.remove("pos", "neg");
  });

  const auto = latestAutoTrade || {};
  const trackedSymbols = Array.isArray(auto.symbols) ? auto.symbols : [];
  const trackedSet = new Set(trackedSymbols);
  const scopedRows = marketRows.filter((row) => {
    const symbol = String(row?.symbol || "");
    return symbol && trackedSet.has(symbol) && !row?.error;
  });

  const trackedCount = scopedRows.length;
  const openPositions = Number(auto.open_positions || 0);
  const maxOpenPositions = Number(auto.max_open_positions);
  const positionCapPass = (
    !Number.isFinite(maxOpenPositions)
    || maxOpenPositions <= 0
    || openPositions < maxOpenPositions
  );

  const autoEnabled = Boolean(auto.enabled);
  const halted = Boolean(auto.halted);
  const strategyMode = String(auto.strategy_mode || "long_only");
  const shortEnabled = Boolean(auto.short_enabled);
  const allowLong = strategyMode !== "short_only";
  const allowShort = shortEnabled && strategyMode !== "long_only";
  const sessionBlocked = (
    Boolean(auto.session_filter_enabled)
    && String(auto.last_reason || "").toLowerCase().includes("session filter active")
  );
  const baseGlobalPass = autoEnabled && !halted && !sessionBlocked && positionCapPass;

  const minStrength = Number(auto.min_strength_confidence);
  const minVolumeRatio = Number(auto.min_volume_ratio);
  const aiFilterEnabled = Boolean(auto.ai_filter_enabled);
  const adaptiveMinConf = Number(auto.adaptive_ai_min_confidence);
  const fixedMinConf = Number(auto.ai_filter_min_confidence);
  const minAiConfidence = (
    Number.isFinite(adaptiveMinConf) && adaptiveMinConf > 0
      ? adaptiveMinConf
      : (Number.isFinite(fixedMinConf) ? fixedMinConf : 0)
  );
  const minAiScoreAbsRaw = Number(auto.ai_filter_min_score_abs);
  const minAiScoreAbs = Number.isFinite(minAiScoreAbsRaw) ? minAiScoreAbsRaw : 0;
  const emaConfirm = Boolean(auto.entry_confirm_ema_stack);
  const macdConfirm = Boolean(auto.entry_confirm_macd);
  const volBlockEnabled = Boolean(auto.extreme_volatility_block_enabled);
  const maxAtrPct = Number(auto.max_atr_pct);
  const maxAbsChangePct = Number(auto.max_abs_change_24h_pct);
  const longRsiMin = Number(auto.long_rsi_min);
  const longRsiMax = Number(auto.long_rsi_max);
  const shortRsiMin = Number(auto.short_rsi_min);
  const shortRsiMax = Number(auto.short_rsi_max);

  let blockedCount = 0;
  let longReadyCount = 0;
  let shortReadyCount = 0;
  const failCounts = {
    ai: 0,
    strength: 0,
    volume: 0,
    ema: 0,
    macd: 0,
    rsi: 0,
    volatility: 0,
  };
  const blockedSymbols = [];

  scopedRows.forEach((row) => {
    const symbol = String(row.symbol || "-");
    const rsi = Number(row.rsi);
    const aiBias = String(row.ai_bias || "HOLD").toUpperCase();
    const aiConfidence = Number(row.ai_confidence);
    const aiScore = Number(row.ai_score);
    const strengthConfidence = Number(row.strength_confidence);
    const volumeRatio = Number(row.volume_ratio);
    const ema20 = Number(row.ema20);
    const ema50 = Number(row.ema50);
    const macd = Number(row.macd);
    const macdSignal = Number(row.macd_signal);
    const atrPct = Number(row.atr_pct);
    const change24h = Number(row.change_24h);
    const price = Number(row.price);

    const aiCommonPass = (
      !aiFilterEnabled
      || (
        Number.isFinite(aiConfidence)
        && aiConfidence >= minAiConfidence
        && Math.abs(Number.isFinite(aiScore) ? aiScore : 0) >= minAiScoreAbs
      )
    );
    const longAiPass = !aiFilterEnabled || (aiCommonPass && aiBias === "BUY");
    const shortAiPass = !aiFilterEnabled || (aiCommonPass && aiBias === "SELL");

    const strengthPass = (
      !Number.isFinite(minStrength)
      || minStrength <= 0
      || (Number.isFinite(strengthConfidence) && strengthConfidence >= minStrength)
    );
    const volumePass = (
      !Number.isFinite(minVolumeRatio)
      || minVolumeRatio <= 0
      || (Number.isFinite(volumeRatio) && volumeRatio >= minVolumeRatio)
    );

    const longEmaPass = (
      Number.isFinite(price)
      && Number.isFinite(ema20)
      && Number.isFinite(ema50)
      && price > ema50
      && (!emaConfirm || ema20 > ema50)
    );
    const shortEmaPass = (
      Number.isFinite(price)
      && Number.isFinite(ema20)
      && Number.isFinite(ema50)
      && price < ema50
      && (!emaConfirm || ema20 < ema50)
    );
    const longMacdPass = !macdConfirm || (
      Number.isFinite(macd) && Number.isFinite(macdSignal) && macd >= macdSignal
    );
    const shortMacdPass = !macdConfirm || (
      Number.isFinite(macd) && Number.isFinite(macdSignal) && macd <= macdSignal
    );
    const longRsiPass = (
      Number.isFinite(rsi)
      && Number.isFinite(longRsiMin)
      && Number.isFinite(longRsiMax)
      && rsi >= longRsiMin
      && rsi <= longRsiMax
    );
    const shortRsiPass = (
      Number.isFinite(rsi)
      && Number.isFinite(shortRsiMin)
      && Number.isFinite(shortRsiMax)
      && rsi >= shortRsiMin
      && rsi <= shortRsiMax
    );

    const atrPass = (
      !volBlockEnabled
      || !Number.isFinite(maxAtrPct)
      || maxAtrPct <= 0
      || (Number.isFinite(atrPct) && atrPct <= maxAtrPct)
    );
    const changePass = (
      !volBlockEnabled
      || !Number.isFinite(maxAbsChangePct)
      || maxAbsChangePct <= 0
      || (Number.isFinite(change24h) && Math.abs(change24h) <= maxAbsChangePct)
    );
    const volatilityPass = atrPass && changePass;

    const longReady = (
      allowLong
      && baseGlobalPass
      && strengthPass
      && volumePass
      && volatilityPass
      && longAiPass
      && longEmaPass
      && longMacdPass
      && longRsiPass
    );
    const shortReady = (
      allowShort
      && baseGlobalPass
      && strengthPass
      && volumePass
      && volatilityPass
      && shortAiPass
      && shortEmaPass
      && shortMacdPass
      && shortRsiPass
    );

    if (longReady) longReadyCount += 1;
    if (shortReady) shortReadyCount += 1;

    if (longReady || shortReady) return;

    blockedCount += 1;
    const tags = [];

    const aiBlockedForMode = (
      (allowLong && !longAiPass) || (allowShort && !shortAiPass)
    );
    const emaBlockedForMode = (
      (allowLong && !longEmaPass) || (allowShort && !shortEmaPass)
    );
    const macdBlockedForMode = (
      (allowLong && !longMacdPass) || (allowShort && !shortMacdPass)
    );
    const rsiBlockedForMode = (
      (allowLong && !longRsiPass) || (allowShort && !shortRsiPass)
    );

    if (aiBlockedForMode) {
      failCounts.ai += 1;
      tags.push("AI");
    }
    if (!strengthPass) {
      failCounts.strength += 1;
      tags.push("STR");
    }
    if (!volumePass) {
      failCounts.volume += 1;
      tags.push("VOL");
    }
    if (emaBlockedForMode) {
      failCounts.ema += 1;
      tags.push("EMA");
    }
    if (macdBlockedForMode) {
      failCounts.macd += 1;
      tags.push("MACD");
    }
    if (rsiBlockedForMode) {
      failCounts.rsi += 1;
      tags.push("RSI");
    }
    if (!volatilityPass) {
      failCounts.volatility += 1;
      tags.push("ATR/24H");
    }

    if (tags.length > 0) {
      blockedSymbols.push(`${symbol}(${tags.join("/")})`);
    } else {
      blockedSymbols.push(`${symbol}(GLOBAL)`);
    }
  });

  setExecutionOutput(gateOutTracked, String(trackedCount));
  setExecutionOutput(gateOutBlocked, String(blockedCount));
  setExecutionOutput(gateOutReadyLong, String(longReadyCount));
  setExecutionOutput(gateOutReadyShort, String(shortReadyCount));
  setExecutionOutput(gateOutAi, String(failCounts.ai));
  setExecutionOutput(gateOutStrength, String(failCounts.strength));
  setExecutionOutput(gateOutVolume, String(failCounts.volume));
  setExecutionOutput(gateOutEma, String(failCounts.ema));
  setExecutionOutput(gateOutMacd, String(failCounts.macd));
  setExecutionOutput(gateOutRsi, String(failCounts.rsi));
  setExecutionOutput(gateOutVolatility, String(failCounts.volatility));
  setExecutionOutput(
    gateOutTopBlocked,
    blockedSymbols.length > 0 ? blockedSymbols.slice(0, 6).join(", ") : "No blocked symbols",
  );

  setExecutionGateTone(gateOutBlocked, blockedCount === 0);
  setExecutionGateTone(gateOutReadyLong, longReadyCount > 0);
  setExecutionGateTone(gateOutReadyShort, shortReadyCount > 0);
  setExecutionGateTone(gateOutAi, failCounts.ai === 0);
  setExecutionGateTone(gateOutStrength, failCounts.strength === 0);
  setExecutionGateTone(gateOutVolume, failCounts.volume === 0);
  setExecutionGateTone(gateOutEma, failCounts.ema === 0);
  setExecutionGateTone(gateOutMacd, failCounts.macd === 0);
  setExecutionGateTone(gateOutRsi, failCounts.rsi === 0);
  setExecutionGateTone(gateOutVolatility, failCounts.volatility === 0);
}

function renderTradeJournalRows(entries) {
  if (!tradeJournalBody) return;

  if (!Array.isArray(entries) || entries.length === 0) {
    tradeJournalBody.innerHTML =
      `<tr><td colspan="9" class="mini-empty">No journal entries yet.</td></tr>`;
    return;
  }

  tradeJournalBody.innerHTML = [...entries]
    .reverse()
    .slice(0, 120)
    .map((entry) => {
      const ts = formatAlertTime(entry.timestamp);
      const symbol = escapeHtml(entry.symbol || "-");
      const eventType = escapeHtml(entry.event_type || "-");
      const side = escapeHtml(String(entry.side || "-").toUpperCase());
      const price = fmtPrice(entry.price);
      const qty = fmtQty(entry.amount);
      const notional = entry.notional_usdt == null ? "-" : fmtMoney(entry.notional_usdt);
      const pnl = entry.pnl_usdt == null ? "-" : `${fmtNumber(entry.pnl_usdt, 2)} USDT`;
      const reason = escapeHtml(entry.reason || "-");

      return `
        <tr>
          <td>${ts}</td>
          <td>${symbol}</td>
          <td>${eventType}</td>
          <td>${side}</td>
          <td>${price}</td>
          <td>${qty}</td>
          <td>${notional}</td>
          <td>${pnl}</td>
          <td>${reason}</td>
        </tr>
      `;
    })
    .join("");
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
  latestAutoTrade = data;
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
  const autoConvertToUsdt = Boolean(data.auto_convert_to_usdt);
  const autoConvertMinUsdt = Number(data.auto_convert_min_usdt);
  const riskMultiplier = Number(data.risk_multiplier);
  const guardrailActive = Boolean(data.guardrail_active);
  const consecutiveLosses = Number(data.consecutive_losses || 0);
  const symbols = Array.isArray(data.symbols) ? data.symbols : [];
  const selected = data.selected_position || null;
  const recentEvents = Array.isArray(data.recent_events) ? data.recent_events : [];
  const recentJournal = Array.isArray(data.recent_journal) ? data.recent_journal : [];
  const lastReason = String(data.last_reason || "-");
  const adaptiveEnabled = Boolean(data.adaptive_enabled);
  const adaptiveProfile = String(data.adaptive_profile || "BALANCED");
  const adaptiveReason = String(data.adaptive_reason || "");
  const adaptiveAiMinConfidence = Number(data.adaptive_ai_min_confidence);
  const adaptiveRiskMultiplier = Number(data.adaptive_risk_multiplier);
  const adaptiveCooldownMultiplier = Number(data.adaptive_cooldown_multiplier);
  const copyTradeEnabled = Boolean(data.copy_trade_enabled);
  const copyTradeFollowers = Array.isArray(data.copy_trade_followers)
    ? data.copy_trade_followers
    : [];
  const copyTradeSlippageBps = Number(data.copy_trade_slippage_bps);
  const copyTradeRecentEvents = Array.isArray(data.copy_trade_recent_events)
    ? data.copy_trade_recent_events
    : [];
  const copyTradeOpenPositions = data.copy_trade_open_positions || {};
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
    const adaptiveText = adaptiveEnabled
      ? `${adaptiveProfile} (AI>=${Number.isNaN(adaptiveAiMinConfidence) ? "-" : adaptiveAiMinConfidence}%, risk x${Number.isNaN(adaptiveRiskMultiplier) ? "-" : fmtNumber(adaptiveRiskMultiplier, 2)}, cd x${Number.isNaN(adaptiveCooldownMultiplier) ? "-" : fmtNumber(adaptiveCooldownMultiplier, 2)})`
      : "Adaptive OFF";
    const guardrailText = guardrailActive
      ? `Guardrail ON x${Number.isNaN(riskMultiplier) ? "-" : fmtNumber(riskMultiplier, 2)}`
      : "Guardrail OFF";
    const autoConvertText = autoConvertToUsdt
      ? `Auto USDT ON (>=${Number.isNaN(autoConvertMinUsdt) ? "-" : fmtNumber(autoConvertMinUsdt, 2)} USDT)`
      : "Auto USDT OFF";
    const lossesText = `Losing Streak ${Number.isNaN(consecutiveLosses) ? 0 : consecutiveLosses}`;
    const shortText = shortEnabled ? "SHORT ON" : "SHORT OFF";
    const openText = Number.isNaN(maxOpenPositions)
      ? `${openPositions}`
      : maxOpenPositions <= 0
        ? `${openPositions}/Unlimited`
        : `${openPositions}/${maxOpenPositions}`;
    autoTradeRiskNode.textContent = `Size ${sizeText} • Min ${minText} • Limit ${limitText} • ${shortText} • ${aiText} • ${adaptiveText} • ${guardrailText} • ${autoConvertText} • ${lossesText} • Open ${openText}`;
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

  if (autoTradeAdaptiveNode) {
    const adaptiveLabel = adaptiveEnabled ? adaptiveProfile : "OFF";
    const adaptiveDetail = adaptiveReason ? ` • ${adaptiveReason}` : "";
    autoTradeAdaptiveNode.textContent = `${adaptiveLabel}${adaptiveDetail}`;
  }

  if (autoTradeCopyNode) {
    if (!copyTradeEnabled) {
      autoTradeCopyNode.textContent = "OFF";
    } else {
      const followerLabel = copyTradeFollowers.length > 0
        ? copyTradeFollowers
            .map((follower) => {
              const name = String(follower.name || "-");
              const multiplier = Number(follower.multiplier);
              const openCount = Number(copyTradeOpenPositions[name] || 0);
              return `${name} x${Number.isNaN(multiplier) ? "-" : fmtNumber(multiplier, 2)} (open ${openCount})`;
            })
            .join(", ")
        : "No followers";
      const slippageLabel = Number.isNaN(copyTradeSlippageBps)
        ? "-"
        : fmtNumber(copyTradeSlippageBps, 1);
      autoTradeCopyNode.textContent = `${followerLabel} • slippage ${slippageLabel}bps`;
    }
  }

  if (autoTradeEventsNode) {
    if (recentEvents.length === 0) {
      autoTradeEventsNode.innerHTML =
        `<tr><td colspan="6" class="mini-empty">No auto-trade events yet.</td></tr>`;
    } else {
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
  }

  if (copyTradeEventsNode) {
    if (!copyTradeEnabled || copyTradeRecentEvents.length === 0) {
      copyTradeEventsNode.innerHTML =
        `<tr><td colspan="5" class="mini-empty">No copy-trade events yet.</td></tr>`;
    } else {
      const copyEvents = [...copyTradeRecentEvents].reverse().slice(0, 30);
      copyTradeEventsNode.innerHTML = copyEvents
        .map((event) => {
          const ts = formatAlertTime(event.timestamp);
          const follower = escapeHtml(event.follower || "-");
          const symbol = escapeHtml(event.symbol || "-");
          const action = escapeHtml(event.event_type || "-");
          const pnl = event.pnl_usdt == null ? "-" : `${fmtNumber(event.pnl_usdt, 2)} USDT`;
          return `
            <tr>
              <td>${ts}</td>
              <td>${follower}</td>
              <td>${symbol}</td>
              <td>${action}</td>
              <td>${pnl}</td>
            </tr>
          `;
        })
        .join("");
    }
  }

  renderTradeJournalRows(recentJournal);
  updateExecutionPanel();
  updateGateFailAnalytics();
  refreshChartDecorations();
  updateChartOverlay();
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

  const previousSymbol = selectedSymbol;
  const previousTimeframe = selectedTimeframe;

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

  if (selectedSymbol !== previousSymbol || selectedTimeframe !== previousTimeframe) {
    armChartAutoFit();
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
  armChartAutoFit();
  sendViewUpdate();
});

timeframeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const timeframe = button.dataset.timeframe;
    if (!timeframe || timeframe === selectedTimeframe) return;

    selectedTimeframe = timeframe;
    setActiveTimeframeButton(selectedTimeframe);
    armChartAutoFit();
    sendViewUpdate();
  });
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

if (exportJournalCsvBtn) {
  exportJournalCsvBtn.addEventListener("click", () => {
    const url = "/api/trade-journal.csv?limit=2000";
    window.open(url, "_blank", "noopener,noreferrer");
  });
}

try {
  initCharts();
  renderAlerts();
  renderAutoTrade({});
  renderWallet({});
  updateExecutionPanel();
  scheduleStaleCheck();
  connectWebSocket();
} catch (error) {
  setStatus("Init failed", false);
  setStreamBanner(error.message || "Initialization failed.", "error-state");
  lastUpdateNode.textContent = error.message;
}
