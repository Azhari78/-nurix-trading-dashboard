import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchBinanceAccount,
  fetchTradingStatus,
  placeBinanceOrder,
} from '../lib/tradingApi';

const DEFAULT_SETTINGS = {
  initialCapital: 1000,
  positionSizing: 'percent',
  orderSizePct: 25,
  riskPerTradePct: 1,
  fastMa: 6,
  slowMa: 20,
  useRsiFilter: true,
  rsiBuyBelow: 35,
  rsiSellAbove: 65,
  useMacdFilter: true,
  slMode: 'fixed',
  tpMode: 'fixed',
  stopLossPct: 2,
  takeProfitPct: 4,
  atrSlMultiplier: 1.5,
  atrTpMultiplier: 2.5,
  useTrailingStop: true,
  trailingStopPct: 1.2,
  useBreakEven: true,
  breakEvenTriggerPct: 1.5,
  breakEvenOffsetPct: 0.1,
  cooldownSec: 15,
  sessionFilterEnabled: false,
  sessionStartHour: 0,
  sessionEndHour: 23,
  maxDailyLossPct: 5,
  maxDrawdownPct: 12,
  maxTradesPerDay: 20,
  maxConsecutiveLosses: 3,
  feePct: 0.1,
  slippagePct: 0.02,
  alertsEnabled: false,
  webhookUrl: '',
  liveBuyUsdt: 25,
  liveSellPct: 100,
};

const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

const formatUsd = (value) => {
  if (!Number.isFinite(value)) return '$0.00';
  return value.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
};

const formatQty = (value) => {
  if (!Number.isFinite(value)) return '0';
  return value.toLocaleString(undefined, { maximumFractionDigits: 6 });
};

const formatPercent = (value) => {
  if (!Number.isFinite(value)) return '-';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
};

const formatFactor = (value) => {
  if (!Number.isFinite(value)) return value > 0 ? '∞' : '-';
  return value.toFixed(2);
};

const calcSma = (prices, period) => {
  if (!Array.isArray(prices) || prices.length < period || period <= 0) return null;
  let sum = 0;
  for (let i = prices.length - period; i < prices.length; i += 1) {
    sum += prices[i];
  }
  return sum / period;
};

const calcSmaAt = (prices, index, period) => {
  if (!Array.isArray(prices) || period <= 0 || index < period - 1) return null;
  let sum = 0;
  for (let i = index - period + 1; i <= index; i += 1) {
    sum += prices[i];
  }
  return sum / period;
};

const calcEmaSeries = (values, period) => {
  const ema = Array(values.length).fill(null);
  if (!Array.isArray(values) || values.length < period) return ema;

  const multiplier = 2 / (period + 1);
  let seedSum = 0;
  for (let i = 0; i < period; i += 1) {
    seedSum += values[i];
  }
  ema[period - 1] = seedSum / period;

  for (let i = period; i < values.length; i += 1) {
    ema[i] = ((values[i] - ema[i - 1]) * multiplier) + ema[i - 1];
  }

  return ema;
};

const calcEmaFromNullable = (values, period) => {
  const ema = Array(values.length).fill(null);
  const multiplier = 2 / (period + 1);
  const seedBuffer = [];
  let prevEma = null;

  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    if (!Number.isFinite(value)) continue;

    if (prevEma === null) {
      seedBuffer.push(value);
      if (seedBuffer.length === period) {
        prevEma = seedBuffer.reduce((sum, current) => sum + current, 0) / period;
        ema[i] = prevEma;
      }
      continue;
    }

    prevEma = ((value - prevEma) * multiplier) + prevEma;
    ema[i] = prevEma;
  }

  return ema;
};

const calcRsiSeries = (prices, period = 14) => {
  const rsi = Array(prices.length).fill(null);
  if (!Array.isArray(prices) || prices.length <= period) return rsi;

  let gains = 0;
  let losses = 0;

  for (let i = 1; i <= period; i += 1) {
    const change = prices[i] - prices[i - 1];
    gains += Math.max(change, 0);
    losses += Math.max(-change, 0);
  }

  let avgGain = gains / period;
  let avgLoss = losses / period;
  const firstRs = avgLoss === 0 ? Number.POSITIVE_INFINITY : avgGain / avgLoss;
  rsi[period] = 100 - (100 / (1 + firstRs));

  for (let i = period + 1; i < prices.length; i += 1) {
    const change = prices[i] - prices[i - 1];
    const gain = Math.max(change, 0);
    const loss = Math.max(-change, 0);

    avgGain = ((avgGain * (period - 1)) + gain) / period;
    avgLoss = ((avgLoss * (period - 1)) + loss) / period;

    const rs = avgLoss === 0 ? Number.POSITIVE_INFINITY : avgGain / avgLoss;
    rsi[i] = 100 - (100 / (1 + rs));
  }

  return rsi;
};

const calcMacdSeries = (prices) => {
  const ema12 = calcEmaSeries(prices, 12);
  const ema26 = calcEmaSeries(prices, 26);

  const macd = prices.map((_, index) => {
    if (!Number.isFinite(ema12[index]) || !Number.isFinite(ema26[index])) return null;
    return ema12[index] - ema26[index];
  });

  const signal = calcEmaFromNullable(macd, 9);
  return { macd, signal };
};

const calcAtrProxySeries = (prices, period = 14) => {
  const atr = Array(prices.length).fill(null);
  if (!Array.isArray(prices) || prices.length <= period) return atr;

  let trSum = 0;
  for (let i = 1; i <= period; i += 1) {
    trSum += Math.abs(prices[i] - prices[i - 1]);
  }
  atr[period] = trSum / period;

  for (let i = period + 1; i < prices.length; i += 1) {
    const tr = Math.abs(prices[i] - prices[i - 1]);
    atr[i] = ((atr[i - 1] * (period - 1)) + tr) / period;
  }

  return atr;
};

const getFreeBalance = (balances, asset) => {
  if (!Array.isArray(balances)) return 0;
  const found = balances.find((item) => item?.asset === asset);
  const free = Number.parseFloat(found?.free ?? '0');
  return Number.isFinite(free) ? free : 0;
};

const getDayKey = (value = new Date()) => value.toISOString().slice(0, 10);

const isWithinSession = (enabled, startHour, endHour, now = new Date()) => {
  if (!enabled) return true;

  const hour = now.getHours();
  const start = clamp(Number.isFinite(startHour) ? startHour : 0, 0, 23);
  const end = clamp(Number.isFinite(endHour) ? endHour : 23, 0, 23);

  if (start === end) return true;
  if (start < end) {
    return hour >= start && hour < end;
  }
  return hour >= start || hour < end;
};

const createInitialWallet = (capital) => ({
  cash: capital,
  positionQty: 0,
  avgEntry: 0,
  realizedPnl: 0,
  trades: 0,
  highestPrice: 0,
  dynamicStop: 0,
  breakEvenArmed: false,
  entryAt: null,
});

const createInitialGuardState = (equity) => ({
  dayKey: getDayKey(),
  dayStartEquity: equity,
  tradesToday: 0,
  consecutiveLosses: 0,
  peakEquity: equity,
  currentDrawdownPct: 0,
  maxDrawdownPct: 0,
  haltedReason: null,
});

export default function AutoTradingBot({ symbol, currentPrice, marketChange24h, indicatorSnapshot }) {
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const [executionMode, setExecutionMode] = useState('paper');
  const [liveTestMode, setLiveTestMode] = useState(true);
  const [isAutoEnabled, setIsAutoEnabled] = useState(false);
  const [priceHistory, setPriceHistory] = useState([]);
  const [logs, setLogs] = useState([]);
  const [tradeHistory, setTradeHistory] = useState([]);
  const [backtestReport, setBacktestReport] = useState(null);
  const [wallet, setWallet] = useState(() => createInitialWallet(DEFAULT_SETTINGS.initialCapital));
  const [guardState, setGuardState] = useState(() => createInitialGuardState(DEFAULT_SETTINGS.initialCapital));
  const [liveStatus, setLiveStatus] = useState({
    loading: false,
    configured: false,
    liveOrdersEnabled: false,
    apiConnected: false,
    baseUrl: '-',
    error: null,
  });
  const [liveBalances, setLiveBalances] = useState({
    usdtFree: 0,
    baseFree: 0,
    updatedAt: null,
  });
  const [isLiveBusy, setIsLiveBusy] = useState(false);

  const walletRef = useRef(wallet);
  const guardStateRef = useRef(guardState);
  const totalEquityRef = useRef(settings.initialCapital);
  const lastTradeAtRef = useRef(0);
  const lastSymbolRef = useRef(symbol);
  const liveOrderLockRef = useRef(false);

  const baseAsset = useMemo(() => (symbol || 'BTC').toUpperCase(), [symbol]);
  const pairSymbol = useMemo(() => `${baseAsset}USDT`, [baseAsset]);

  const chartIndicators = useMemo(() => {
    const aligned = indicatorSnapshot?.pairSymbol === pairSymbol;
    const toFinite = (value) => {
      const numeric = Number(value);
      return Number.isFinite(numeric) ? numeric : null;
    };

    return {
      aligned,
      timeframe: aligned ? (indicatorSnapshot?.timeframe || '-') : '-',
      updatedAt: aligned && Number.isFinite(indicatorSnapshot?.updatedAt)
        ? new Date(indicatorSnapshot.updatedAt)
        : null,
      rsi: aligned ? toFinite(indicatorSnapshot?.rsi) : null,
      macd: aligned ? toFinite(indicatorSnapshot?.macd) : null,
      signal: aligned ? toFinite(indicatorSnapshot?.signal) : null,
      atr: aligned ? toFinite(indicatorSnapshot?.atr) : null,
    };
  }, [indicatorSnapshot, pairSymbol]);

  useEffect(() => {
    walletRef.current = wallet;
  }, [wallet]);

  useEffect(() => {
    guardStateRef.current = guardState;
  }, [guardState]);

  useEffect(() => {
    if (!Number.isFinite(currentPrice)) return;
    setPriceHistory((prev) => {
      const last = prev[prev.length - 1];
      if (last === currentPrice) return prev;
      const next = [...prev, currentPrice];
      return next.length > 1200 ? next.slice(-1200) : next;
    });
  }, [currentPrice]);

  const sendAlert = useCallback((entry) => {
    if (!settings.alertsEnabled) return;

    const title = `${entry.side} ${pairSymbol}`;
    const body = `${entry.reason} | Price ${formatUsd(entry.price)} | Qty ${formatQty(entry.qty)}`;

    if (typeof window !== 'undefined' && 'Notification' in window && Notification.permission === 'granted') {
      try {
        // Browser-level alert when app tab is not focused.
        // eslint-disable-next-line no-new
        new Notification(title, { body });
      } catch (error) {
        console.debug('Notification error:', error);
      }
    }

    const webhook = settings.webhookUrl.trim();
    if (!webhook) return;

    void fetch(webhook, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pair: pairSymbol,
        side: entry.side,
        reason: entry.reason,
        price: entry.price,
        qty: entry.qty,
        value: entry.value,
        pnl: entry.pnl ?? null,
        at: entry.at.toISOString(),
      }),
    }).catch((error) => {
      console.debug('Webhook notify failed:', error);
    });
  }, [pairSymbol, settings.alertsEnabled, settings.webhookUrl]);

  const addLog = useCallback((entry) => {
    const fullEntry = {
      id: `${Date.now()}-${Math.random()}`,
      at: new Date(),
      ...entry,
    };

    setLogs((prev) => [fullEntry, ...prev].slice(0, 120));

    if (['BUY', 'SELL', 'ERROR'].includes(fullEntry.side)) {
      sendAlert(fullEntry);
    }
  }, [sendAlert]);

  const updateGuardState = useCallback((updater) => {
    setGuardState((prev) => {
      const next = updater(prev);
      guardStateRef.current = next;
      return next;
    });
  }, []);

  const updateNumberSetting = useCallback((key, rawValue, min, max, integer = false) => {
    const numeric = Number(rawValue);
    if (!Number.isFinite(numeric)) return;

    setSettings((prev) => {
      let value = integer ? Math.round(numeric) : numeric;
      if (Number.isFinite(min)) value = Math.max(min, value);
      if (Number.isFinite(max)) value = Math.min(max, value);

      const next = { ...prev, [key]: value };

      if (key === 'fastMa' && next.fastMa >= next.slowMa) {
        next.slowMa = next.fastMa + 1;
      }
      if (key === 'slowMa' && next.slowMa <= next.fastMa) {
        next.fastMa = Math.max(2, next.slowMa - 1);
      }

      return next;
    });
  }, []);

  const updateBooleanSetting = useCallback((key, value) => {
    setSettings((prev) => ({ ...prev, [key]: Boolean(value) }));
  }, []);

  const updateTextSetting = useCallback((key, value) => {
    setSettings((prev) => ({ ...prev, [key]: value }));
  }, []);

  const applyResetState = useCallback((capital, price) => {
    setIsAutoEnabled(false);
    setWallet(createInitialWallet(capital));
    setGuardState(createInitialGuardState(capital));
    setLogs([]);
    setTradeHistory([]);
    setBacktestReport(null);
    setPriceHistory(Number.isFinite(price) ? [price] : []);
    lastTradeAtRef.current = 0;
  }, []);

  const resetBot = useCallback(() => {
    applyResetState(settings.initialCapital, currentPrice);
  }, [applyResetState, currentPrice, settings.initialCapital]);

  useEffect(() => {
    if (lastSymbolRef.current === symbol) return;
    lastSymbolRef.current = symbol;
    applyResetState(settings.initialCapital, currentPrice);
  }, [symbol, applyResetState, settings.initialCapital, currentPrice]);

  const fastSma = useMemo(() => calcSma(priceHistory, settings.fastMa), [priceHistory, settings.fastMa]);
  const slowSma = useMemo(() => calcSma(priceHistory, settings.slowMa), [priceHistory, settings.slowMa]);
  const hasEnoughData = priceHistory.length >= settings.slowMa;

  const positionValue = Number.isFinite(currentPrice) ? wallet.positionQty * currentPrice : 0;
  const unrealizedPnl = wallet.positionQty > 0 && Number.isFinite(currentPrice)
    ? (currentPrice - wallet.avgEntry) * wallet.positionQty
    : 0;
  const totalEquity = wallet.cash + positionValue;
  const totalPnl = wallet.realizedPnl + unrealizedPnl;

  useEffect(() => {
    totalEquityRef.current = totalEquity;
  }, [totalEquity]);

  useEffect(() => {
    if (!Number.isFinite(totalEquity) || totalEquity <= 0) return;

    updateGuardState((prev) => {
      const today = getDayKey();
      let next = prev;
      let changed = false;

      if (prev.dayKey !== today) {
        next = {
          ...prev,
          dayKey: today,
          dayStartEquity: totalEquity,
          tradesToday: 0,
          consecutiveLosses: 0,
          peakEquity: totalEquity,
          currentDrawdownPct: 0,
          maxDrawdownPct: 0,
          haltedReason: null,
        };
        changed = true;
      }

      const peakEquity = Math.max(next.peakEquity, totalEquity);
      const currentDrawdownPct = peakEquity > 0 ? ((peakEquity - totalEquity) / peakEquity) * 100 : 0;
      const maxDrawdownPct = Math.max(next.maxDrawdownPct, currentDrawdownPct);

      if (
        !changed &&
        peakEquity === next.peakEquity &&
        Math.abs(currentDrawdownPct - next.currentDrawdownPct) < 1e-9 &&
        Math.abs(maxDrawdownPct - next.maxDrawdownPct) < 1e-9
      ) {
        return prev;
      }

      return {
        ...next,
        peakEquity,
        currentDrawdownPct,
        maxDrawdownPct,
      };
    });
  }, [totalEquity, updateGuardState]);

  const registerTradeForGuards = useCallback((realizedPnl = null) => {
    updateGuardState((prev) => ({
      ...prev,
      tradesToday: prev.tradesToday + 1,
      consecutiveLosses: Number.isFinite(realizedPnl)
        ? (realizedPnl < 0 ? prev.consecutiveLosses + 1 : 0)
        : prev.consecutiveLosses,
    }));
  }, [updateGuardState]);

  const evaluateRiskGuards = useCallback(() => {
    const snapshot = guardStateRef.current;
    const equity = totalEquityRef.current;

    if (!Number.isFinite(equity) || snapshot.dayStartEquity <= 0) return null;

    const dayPnl = equity - snapshot.dayStartEquity;
    const dailyLossLimit = snapshot.dayStartEquity * (settings.maxDailyLossPct / 100);

    if (dailyLossLimit > 0 && dayPnl <= -dailyLossLimit) {
      return `Daily loss limit hit (${formatUsd(dayPnl)})`;
    }
    if (snapshot.currentDrawdownPct >= settings.maxDrawdownPct) {
      return `Drawdown exceeded ${settings.maxDrawdownPct}%`;
    }
    if (snapshot.tradesToday >= settings.maxTradesPerDay) {
      return `Max trades/day ${settings.maxTradesPerDay} reached`;
    }
    if (snapshot.consecutiveLosses >= settings.maxConsecutiveLosses) {
      return `Consecutive loss limit ${settings.maxConsecutiveLosses} reached`;
    }

    return null;
  }, [
    settings.maxConsecutiveLosses,
    settings.maxDailyLossPct,
    settings.maxDrawdownPct,
    settings.maxTradesPerDay,
  ]);

  const haltAuto = useCallback((reason) => {
    const shouldLog = guardStateRef.current.haltedReason !== reason;
    updateGuardState((prev) => (
      prev.haltedReason === reason
        ? prev
        : { ...prev, haltedReason: reason }
    ));

    setIsAutoEnabled(false);

    if (shouldLog) {
      addLog({
        side: 'INFO',
        reason: `Auto paused: ${reason}`,
        price: currentPrice,
        qty: 0,
        value: 0,
      });
    }
  }, [addLog, currentPrice, updateGuardState]);

  const isSessionOpenNow = useCallback(() => (
    isWithinSession(
      settings.sessionFilterEnabled,
      settings.sessionStartHour,
      settings.sessionEndHour,
      new Date(),
    )
  ), [settings.sessionEndHour, settings.sessionFilterEnabled, settings.sessionStartHour]);

  const resolveStopDistancePct = useCallback((entryPrice, atrOverride = null) => {
    if (settings.slMode === 'atr') {
      const atrValue = Number.isFinite(atrOverride) ? atrOverride : chartIndicators.atr;
      if (Number.isFinite(atrValue) && atrValue > 0 && Number.isFinite(entryPrice) && entryPrice > 0) {
        return clamp(((atrValue * settings.atrSlMultiplier) / entryPrice) * 100, 0.2, 80);
      }
    }
    return clamp(settings.stopLossPct, 0.2, 80);
  }, [chartIndicators.atr, settings.atrSlMultiplier, settings.slMode, settings.stopLossPct]);

  const resolveTakeProfitPrice = useCallback((entryPrice, atrOverride = null) => {
    if (!Number.isFinite(entryPrice) || entryPrice <= 0) return null;

    if (settings.tpMode === 'atr') {
      const atrValue = Number.isFinite(atrOverride) ? atrOverride : chartIndicators.atr;
      if (Number.isFinite(atrValue) && atrValue > 0) {
        return entryPrice + (atrValue * settings.atrTpMultiplier);
      }
    }

    return entryPrice * (1 + (settings.takeProfitPct / 100));
  }, [chartIndicators.atr, settings.atrTpMultiplier, settings.takeProfitPct, settings.tpMode]);

  const resolveOrderValue = useCallback((cashAvailable, entryPrice, atrOverride = null) => {
    if (!Number.isFinite(cashAvailable) || cashAvailable <= 0) return 0;

    if (settings.positionSizing === 'risk') {
      const riskCapital = cashAvailable * (settings.riskPerTradePct / 100);
      const stopDistancePct = resolveStopDistancePct(entryPrice, atrOverride);
      if (!Number.isFinite(stopDistancePct) || stopDistancePct <= 0) return 0;
      return Math.min(riskCapital / (stopDistancePct / 100), cashAvailable);
    }

    return cashAvailable * (settings.orderSizePct / 100);
  }, [
    resolveStopDistancePct,
    settings.orderSizePct,
    settings.positionSizing,
    settings.riskPerTradePct,
  ]);

  const getSignalSnapshot = useCallback(() => ({
    fastMa: Number.isFinite(fastSma) ? Number(fastSma.toFixed(6)) : null,
    slowMa: Number.isFinite(slowSma) ? Number(slowSma.toFixed(6)) : null,
    rsi: chartIndicators.rsi,
    macd: chartIndicators.macd,
    signal: chartIndicators.signal,
    atr: chartIndicators.atr,
    timeframe: chartIndicators.timeframe,
  }), [
    chartIndicators.atr,
    chartIndicators.macd,
    chartIndicators.rsi,
    chartIndicators.signal,
    chartIndicators.timeframe,
    fastSma,
    slowSma,
  ]);

  const passesBuyFilters = useCallback((rsiValue = chartIndicators.rsi, macdValue = chartIndicators.macd, signalValue = chartIndicators.signal) => {
    if (settings.useRsiFilter) {
      if (!Number.isFinite(rsiValue)) return false;
      if (rsiValue > settings.rsiBuyBelow) return false;
    }

    if (settings.useMacdFilter) {
      if (!Number.isFinite(macdValue) || !Number.isFinite(signalValue)) return false;
      if (macdValue <= signalValue) return false;
    }

    return true;
  }, [
    chartIndicators.macd,
    chartIndicators.rsi,
    chartIndicators.signal,
    settings.rsiBuyBelow,
    settings.useMacdFilter,
    settings.useRsiFilter,
  ]);

  const passesSellFilters = useCallback((rsiValue = chartIndicators.rsi, macdValue = chartIndicators.macd, signalValue = chartIndicators.signal) => {
    if (settings.useRsiFilter) {
      if (!Number.isFinite(rsiValue)) return false;
      if (rsiValue < settings.rsiSellAbove) return false;
    }

    if (settings.useMacdFilter) {
      if (!Number.isFinite(macdValue) || !Number.isFinite(signalValue)) return false;
      if (macdValue >= signalValue) return false;
    }

    return true;
  }, [
    chartIndicators.macd,
    chartIndicators.rsi,
    chartIndicators.signal,
    settings.rsiSellAbove,
    settings.useMacdFilter,
    settings.useRsiFilter,
  ]);

  const executePaperBuy = useCallback((reason = 'Manual buy', orderValueOverride = null) => {
    if (!Number.isFinite(currentPrice) || currentPrice <= 0) return false;

    const snapshot = walletRef.current;
    if (snapshot.cash <= 0) return false;

    const feeRate = settings.feePct / 100;
    const slippageRate = settings.slippagePct / 100;

    const desiredOrderValue = Number.isFinite(orderValueOverride) && orderValueOverride > 0
      ? orderValueOverride
      : resolveOrderValue(snapshot.cash, currentPrice);

    let orderValue = Math.min(desiredOrderValue, snapshot.cash / (1 + feeRate));

    if (!Number.isFinite(orderValue) || orderValue < 10) {
      addLog({
        side: 'INFO',
        reason: 'Skip buy (cash too small)',
        price: currentPrice,
        qty: 0,
        value: 0,
      });
      return false;
    }

    const executionPrice = currentPrice * (1 + slippageRate);
    const qty = orderValue / executionPrice;
    const fee = orderValue * feeRate;
    const stopDistancePct = resolveStopDistancePct(executionPrice);
    const initialStop = executionPrice * (1 - (stopDistancePct / 100));
    const takeProfitPrice = resolveTakeProfitPrice(executionPrice);

    setWallet((prev) => {
      const nextQty = prev.positionQty + qty;
      const nextAvgEntry = prev.positionQty > 0
        ? ((prev.avgEntry * prev.positionQty) + (executionPrice * qty)) / nextQty
        : executionPrice;

      return {
        ...prev,
        cash: prev.cash - (orderValue + fee),
        positionQty: nextQty,
        avgEntry: nextAvgEntry,
        trades: prev.trades + 1,
        highestPrice: prev.positionQty > 0 ? Math.max(prev.highestPrice, executionPrice) : executionPrice,
        dynamicStop: initialStop,
        breakEvenArmed: false,
        entryAt: prev.positionQty > 0 ? prev.entryAt : new Date(),
      };
    });

    registerTradeForGuards(null);
    lastTradeAtRef.current = Date.now();

    addLog({
      side: 'BUY',
      reason,
      price: executionPrice,
      marketPrice: currentPrice,
      qty,
      value: orderValue,
      fee,
      slippageValue: orderValue * slippageRate,
      stopPrice: initialStop,
      takeProfitPrice,
      indicators: getSignalSnapshot(),
    });

    return true;
  }, [
    addLog,
    currentPrice,
    getSignalSnapshot,
    registerTradeForGuards,
    resolveOrderValue,
    resolveStopDistancePct,
    resolveTakeProfitPrice,
    settings.feePct,
    settings.slippagePct,
  ]);

  const executePaperSell = useCallback((reason = 'Manual sell', sellPct = 100) => {
    if (!Number.isFinite(currentPrice) || currentPrice <= 0) return false;

    const snapshot = walletRef.current;
    if (snapshot.positionQty <= 0) return false;

    const normalizedSellPct = clamp(sellPct, 1, 100);
    const qty = snapshot.positionQty * (normalizedSellPct / 100);
    if (qty <= 0) return false;

    const feeRate = settings.feePct / 100;
    const slippageRate = settings.slippagePct / 100;
    const executionPrice = currentPrice * (1 - slippageRate);

    const grossProceeds = qty * executionPrice;
    const fee = grossProceeds * feeRate;
    const netProceeds = grossProceeds - fee;
    const realized = (executionPrice - snapshot.avgEntry) * qty - fee;

    setWallet((prev) => {
      const nextQty = prev.positionQty - qty;
      const closedPosition = nextQty <= 1e-12;

      return {
        ...prev,
        cash: prev.cash + netProceeds,
        positionQty: closedPosition ? 0 : nextQty,
        avgEntry: closedPosition ? 0 : prev.avgEntry,
        realizedPnl: prev.realizedPnl + realized,
        trades: prev.trades + 1,
        highestPrice: closedPosition ? 0 : prev.highestPrice,
        dynamicStop: closedPosition ? 0 : prev.dynamicStop,
        breakEvenArmed: closedPosition ? false : prev.breakEvenArmed,
        entryAt: closedPosition ? null : prev.entryAt,
      };
    });

    if (Number.isFinite(realized)) {
      const holdingSec = snapshot.entryAt
        ? (Date.now() - new Date(snapshot.entryAt).getTime()) / 1000
        : null;

      setTradeHistory((prev) => [
        {
          id: `${Date.now()}-${Math.random()}`,
          at: new Date(),
          entry: snapshot.avgEntry,
          exit: executionPrice,
          qty,
          pnl: realized,
          returnPct: snapshot.avgEntry > 0
            ? ((executionPrice - snapshot.avgEntry) / snapshot.avgEntry) * 100
            : 0,
          holdingSec,
        },
        ...prev,
      ].slice(0, 200));
    }

    registerTradeForGuards(realized);
    lastTradeAtRef.current = Date.now();

    addLog({
      side: 'SELL',
      reason,
      price: executionPrice,
      marketPrice: currentPrice,
      qty,
      value: netProceeds,
      fee,
      slippageValue: grossProceeds * slippageRate,
      pnl: realized,
      indicators: getSignalSnapshot(),
    });

    return true;
  }, [
    addLog,
    currentPrice,
    getSignalSnapshot,
    registerTradeForGuards,
    settings.feePct,
    settings.slippagePct,
  ]);

  const hydrateBalancesFromAccount = useCallback((accountPayload) => {
    const balances = Array.isArray(accountPayload?.balances) ? accountPayload.balances : [];
    setLiveBalances({
      usdtFree: getFreeBalance(balances, 'USDT'),
      baseFree: getFreeBalance(balances, baseAsset),
      updatedAt: new Date(),
    });
  }, [baseAsset]);

  const loadLiveStatus = useCallback(async () => {
    setLiveStatus((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const payload = await fetchTradingStatus();
      setLiveStatus({
        loading: false,
        configured: Boolean(payload?.configured),
        liveOrdersEnabled: Boolean(payload?.liveOrdersEnabled),
        apiConnected: Boolean(payload?.apiConnected),
        baseUrl: payload?.baseUrl || '-',
        error: null,
      });
      return payload;
    } catch (error) {
      setLiveStatus({
        loading: false,
        configured: false,
        liveOrdersEnabled: false,
        apiConnected: false,
        baseUrl: '-',
        error: error.message || 'Failed to read live status',
      });
      return null;
    }
  }, []);

  const syncLiveAccount = useCallback(async () => {
    try {
      const payload = await fetchBinanceAccount();
      hydrateBalancesFromAccount(payload);
      setLiveStatus((prev) => ({ ...prev, error: null }));
      return payload;
    } catch (error) {
      setLiveStatus((prev) => ({ ...prev, error: error.message || 'Failed to sync account' }));
      return null;
    }
  }, [hydrateBalancesFromAccount]);

  useEffect(() => {
    if (executionMode !== 'live') return undefined;
    let isMounted = true;
    let intervalId = null;

    const boot = async () => {
      const statusPayload = await loadLiveStatus();
      if (!isMounted || !statusPayload?.configured) return;
      await syncLiveAccount();
      intervalId = setInterval(() => {
        void syncLiveAccount();
      }, 20000);
    };

    void boot();

    return () => {
      isMounted = false;
      if (intervalId) clearInterval(intervalId);
    };
  }, [executionMode, loadLiveStatus, syncLiveAccount]);

  const executeLiveOrder = useCallback(async (side, reason) => {
    if (liveOrderLockRef.current) return false;
    if (!Number.isFinite(currentPrice) || currentPrice <= 0) return false;

    liveOrderLockRef.current = true;
    setIsLiveBusy(true);

    try {
      let statusSnapshot = liveStatus;
      if (!statusSnapshot.configured) {
        const statusPayload = await loadLiveStatus();
        statusSnapshot = {
          configured: Boolean(statusPayload?.configured),
          liveOrdersEnabled: Boolean(statusPayload?.liveOrdersEnabled),
        };
      }

      if (!statusSnapshot.configured) {
        throw new Error('Trading API belum diset. Isi BINANCE_API_KEY dan BINANCE_API_SECRET.');
      }

      if (!liveTestMode && !statusSnapshot.liveOrdersEnabled) {
        throw new Error('ENABLE_LIVE_ORDERS masih false pada server.');
      }

      const payload = {
        symbol: pairSymbol,
        side,
        type: 'MARKET',
        testMode: liveTestMode,
      };

      if (side === 'BUY') {
        const estimatedBuyValue = settings.positionSizing === 'risk'
          ? resolveOrderValue(walletRef.current.cash, currentPrice)
          : settings.liveBuyUsdt;

        if (!Number.isFinite(estimatedBuyValue) || estimatedBuyValue < 10) {
          throw new Error('Live buy minimum 10 USDT.');
        }

        payload.quoteOrderQty = estimatedBuyValue;
      } else {
        let quantity = walletRef.current.positionQty * (settings.liveSellPct / 100);
        if (!liveTestMode) {
          const accountPayload = await syncLiveAccount();
          const availableBase = getFreeBalance(accountPayload?.balances, baseAsset);
          if (availableBase > 0) {
            quantity = availableBase * (settings.liveSellPct / 100);
          }
        }

        if (!Number.isFinite(quantity) || quantity <= 0) {
          throw new Error(`Tiada kuantiti ${baseAsset} untuk sell.`);
        }

        payload.quantity = quantity;
      }

      const response = await placeBinanceOrder(payload);
      const modeLabel = liveTestMode ? 'TEST' : 'LIVE';

      if (side === 'BUY') {
        executePaperBuy(`${reason} [${modeLabel}]`, Number(payload.quoteOrderQty));
      } else {
        executePaperSell(`${reason} [${modeLabel}]`, settings.liveSellPct);
      }

      if (!liveTestMode) {
        await syncLiveAccount();
      }

      if (!liveTestMode && response?.order?.orderId) {
        addLog({
          side: 'INFO',
          reason: `Exchange orderId: ${response.order.orderId}`,
          price: currentPrice,
          qty: 0,
          value: 0,
        });
      }

      return true;
    } catch (error) {
      addLog({
        side: 'ERROR',
        reason: `${side} failed: ${error.message || 'Unknown error'}`,
        price: currentPrice,
        qty: 0,
        value: 0,
      });
      return false;
    } finally {
      liveOrderLockRef.current = false;
      setIsLiveBusy(false);
      lastTradeAtRef.current = Date.now();
    }
  }, [
    addLog,
    baseAsset,
    currentPrice,
    executePaperBuy,
    executePaperSell,
    liveStatus,
    liveTestMode,
    loadLiveStatus,
    pairSymbol,
    resolveOrderValue,
    settings.liveBuyUsdt,
    settings.liveSellPct,
    settings.positionSizing,
    syncLiveAccount,
  ]);

  const executeBuy = useCallback(async (reason = 'Manual buy') => {
    if (executionMode === 'live') {
      return executeLiveOrder('BUY', reason);
    }
    return executePaperBuy(reason);
  }, [executionMode, executeLiveOrder, executePaperBuy]);

  const executeSell = useCallback(async (reason = 'Manual sell') => {
    if (executionMode === 'live') {
      return executeLiveOrder('SELL', reason);
    }
    return executePaperSell(reason);
  }, [executionMode, executeLiveOrder, executePaperSell]);

  const runBacktest = useCallback(() => {
    if (priceHistory.length < Math.max(settings.slowMa + 10, 60)) {
      setBacktestReport({
        error: 'Not enough history for backtest. Tunggu lebih banyak ticks.',
      });
      return;
    }

    const prices = [...priceHistory];
    const rsiSeries = calcRsiSeries(prices, 14);
    const { macd, signal } = calcMacdSeries(prices);
    const atrProxy = calcAtrProxySeries(prices, 14);

    let cash = settings.initialCapital;
    let qty = 0;
    let avgEntry = 0;
    let highestPrice = 0;
    let dynamicStop = 0;

    let closedTrades = 0;
    let wins = 0;
    let grossProfit = 0;
    let grossLoss = 0;
    let closedPnlTotal = 0;

    const feeRate = settings.feePct / 100;
    const slippageRate = settings.slippagePct / 100;

    let peakEquity = cash;
    let maxDrawdownPct = 0;

    const backtestStopPct = (entryPrice, atrValue) => {
      if (settings.slMode === 'atr' && Number.isFinite(atrValue) && atrValue > 0 && entryPrice > 0) {
        return clamp(((atrValue * settings.atrSlMultiplier) / entryPrice) * 100, 0.2, 80);
      }
      return clamp(settings.stopLossPct, 0.2, 80);
    };

    const backtestTpPrice = (entryPrice, atrValue) => {
      if (settings.tpMode === 'atr' && Number.isFinite(atrValue) && atrValue > 0) {
        return entryPrice + (atrValue * settings.atrTpMultiplier);
      }
      return entryPrice * (1 + (settings.takeProfitPct / 100));
    };

    for (let i = 0; i < prices.length; i += 1) {
      const price = prices[i];
      const fast = calcSmaAt(prices, i, settings.fastMa);
      const slow = calcSmaAt(prices, i, settings.slowMa);
      const trendUp = Number.isFinite(fast) && Number.isFinite(slow) && fast > slow;
      const trendDown = Number.isFinite(fast) && Number.isFinite(slow) && fast < slow;

      const buyFilterPass = (() => {
        if (settings.useRsiFilter) {
          if (!Number.isFinite(rsiSeries[i]) || rsiSeries[i] > settings.rsiBuyBelow) return false;
        }
        if (settings.useMacdFilter) {
          if (!Number.isFinite(macd[i]) || !Number.isFinite(signal[i]) || macd[i] <= signal[i]) return false;
        }
        return true;
      })();

      const sellFilterPass = (() => {
        if (settings.useRsiFilter) {
          if (!Number.isFinite(rsiSeries[i]) || rsiSeries[i] < settings.rsiSellAbove) return false;
        }
        if (settings.useMacdFilter) {
          if (!Number.isFinite(macd[i]) || !Number.isFinite(signal[i]) || macd[i] >= signal[i]) return false;
        }
        return true;
      })();

      if (qty <= 0) {
        if (trendUp && buyFilterPass) {
          const stopPct = backtestStopPct(price, atrProxy[i]);
          const riskOrderValue = settings.positionSizing === 'risk'
            ? (cash * (settings.riskPerTradePct / 100)) / (stopPct / 100)
            : cash * (settings.orderSizePct / 100);

          let orderValue = Math.min(riskOrderValue, cash / (1 + feeRate));
          if (Number.isFinite(orderValue) && orderValue >= 10) {
            const executionPrice = price * (1 + slippageRate);
            const boughtQty = orderValue / executionPrice;
            const fee = orderValue * feeRate;

            cash -= orderValue + fee;
            qty = boughtQty;
            avgEntry = executionPrice;
            highestPrice = executionPrice;
            dynamicStop = executionPrice * (1 - (stopPct / 100));
          }
        }
      } else {
        highestPrice = Math.max(highestPrice, price);

        const stopPct = backtestStopPct(avgEntry, atrProxy[i]);
        let stopPrice = avgEntry * (1 - (stopPct / 100));

        if (settings.useTrailingStop) {
          stopPrice = Math.max(stopPrice, highestPrice * (1 - (settings.trailingStopPct / 100)));
        }

        if (settings.useBreakEven && price >= avgEntry * (1 + (settings.breakEvenTriggerPct / 100))) {
          stopPrice = Math.max(stopPrice, avgEntry * (1 + (settings.breakEvenOffsetPct / 100)));
        }

        dynamicStop = Math.max(dynamicStop, stopPrice);
        const takeProfitPrice = backtestTpPrice(avgEntry, atrProxy[i]);

        const stopHit = price <= dynamicStop;
        const tpHit = Number.isFinite(takeProfitPrice) && price >= takeProfitPrice;

        if (stopHit || tpHit || (trendDown && sellFilterPass)) {
          const executionPrice = price * (1 - slippageRate);
          const grossProceeds = qty * executionPrice;
          const fee = grossProceeds * feeRate;
          const netProceeds = grossProceeds - fee;
          const realized = (executionPrice - avgEntry) * qty - fee;

          cash += netProceeds;
          qty = 0;
          avgEntry = 0;
          highestPrice = 0;
          dynamicStop = 0;

          closedTrades += 1;
          closedPnlTotal += realized;
          if (realized >= 0) {
            wins += 1;
            grossProfit += realized;
          } else {
            grossLoss += Math.abs(realized);
          }
        }
      }

      const equity = cash + (qty > 0 ? qty * price : 0);
      peakEquity = Math.max(peakEquity, equity);
      const ddPct = peakEquity > 0 ? ((peakEquity - equity) / peakEquity) * 100 : 0;
      maxDrawdownPct = Math.max(maxDrawdownPct, ddPct);
    }

    const lastPrice = prices[prices.length - 1];
    const finalEquity = cash + (qty > 0 ? qty * lastPrice : 0);
    const netPnl = finalEquity - settings.initialCapital;
    const winRate = closedTrades > 0 ? (wins / closedTrades) * 100 : 0;
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : (grossProfit > 0 ? Number.POSITIVE_INFINITY : 0);
    const expectancy = closedTrades > 0 ? closedPnlTotal / closedTrades : 0;

    const report = {
      generatedAt: new Date(),
      sampleSize: prices.length,
      closedTrades,
      winRate,
      profitFactor,
      maxDrawdownPct,
      expectancy,
      netPnl,
      finalEquity,
    };

    setBacktestReport(report);
    addLog({
      side: 'INFO',
      reason: `Backtest completed · Trades ${closedTrades} · Win ${winRate.toFixed(1)}% · Net ${formatUsd(netPnl)}`,
      price: currentPrice,
      qty: 0,
      value: 0,
    });
  }, [
    addLog,
    currentPrice,
    priceHistory,
    settings.atrSlMultiplier,
    settings.atrTpMultiplier,
    settings.breakEvenOffsetPct,
    settings.breakEvenTriggerPct,
    settings.fastMa,
    settings.feePct,
    settings.initialCapital,
    settings.orderSizePct,
    settings.positionSizing,
    settings.riskPerTradePct,
    settings.rsiBuyBelow,
    settings.rsiSellAbove,
    settings.slowMa,
    settings.slMode,
    settings.slippagePct,
    settings.stopLossPct,
    settings.takeProfitPct,
    settings.tpMode,
    settings.trailingStopPct,
    settings.useBreakEven,
    settings.useMacdFilter,
    settings.useRsiFilter,
    settings.useTrailingStop,
  ]);

  useEffect(() => {
    if (!isAutoEnabled || !hasEnoughData) return;
    if (!Number.isFinite(currentPrice) || !Number.isFinite(fastSma) || !Number.isFinite(slowSma)) return;

    const cooldownMs = settings.cooldownSec * 1000;
    if ((Date.now() - lastTradeAtRef.current) < cooldownMs) return;

    const guardReason = evaluateRiskGuards();
    if (guardReason) {
      haltAuto(guardReason);
      return;
    }

    const snapshot = walletRef.current;
    const hasPosition = snapshot.positionQty > 0;
    const trendUp = fastSma > slowSma;
    const trendDown = fastSma < slowSma;

    const run = async () => {
      if (!hasPosition) {
        if (!trendUp) return;
        if (!isSessionOpenNow()) return;
        if (!passesBuyFilters()) return;

        const reasons = ['MA bullish'];
        if (settings.useRsiFilter) reasons.push(`RSI <= ${settings.rsiBuyBelow}`);
        if (settings.useMacdFilter) reasons.push('MACD > Signal');
        await executeBuy(`Entry: ${reasons.join(', ')}`);
        return;
      }

      const entry = snapshot.avgEntry;
      if (!Number.isFinite(entry) || entry <= 0) return;

      const highestPrice = Math.max(snapshot.highestPrice || entry, currentPrice);
      const baseStopPrice = entry * (1 - (resolveStopDistancePct(entry) / 100));
      let dynamicStop = Math.max(snapshot.dynamicStop || 0, baseStopPrice);

      if (settings.useTrailingStop) {
        dynamicStop = Math.max(dynamicStop, highestPrice * (1 - (settings.trailingStopPct / 100)));
      }

      let breakEvenArmed = snapshot.breakEvenArmed;
      if (settings.useBreakEven && currentPrice >= entry * (1 + (settings.breakEvenTriggerPct / 100))) {
        breakEvenArmed = true;
        dynamicStop = Math.max(dynamicStop, entry * (1 + (settings.breakEvenOffsetPct / 100)));
      }

      const takeProfitPrice = resolveTakeProfitPrice(entry);

      if (
        Math.abs(highestPrice - (snapshot.highestPrice || 0)) > 1e-9 ||
        Math.abs(dynamicStop - (snapshot.dynamicStop || 0)) > 1e-9 ||
        breakEvenArmed !== snapshot.breakEvenArmed
      ) {
        setWallet((prev) => {
          if (prev.positionQty <= 0) return prev;
          return {
            ...prev,
            highestPrice: Math.max(prev.highestPrice || entry, currentPrice),
            dynamicStop,
            breakEvenArmed,
          };
        });
      }

      if (currentPrice <= dynamicStop) {
        await executeSell(`Stop hit @ ${formatUsd(dynamicStop)}`);
        return;
      }

      if (Number.isFinite(takeProfitPrice) && currentPrice >= takeProfitPrice) {
        await executeSell(`Take profit hit @ ${formatUsd(takeProfitPrice)}`);
        return;
      }

      if (trendDown && passesSellFilters()) {
        await executeSell('Exit: MA bearish confirmation');
      }
    };

    void run();
  }, [
    currentPrice,
    evaluateRiskGuards,
    executeBuy,
    executeSell,
    fastSma,
    haltAuto,
    hasEnoughData,
    isAutoEnabled,
    isSessionOpenNow,
    passesBuyFilters,
    passesSellFilters,
    resolveStopDistancePct,
    resolveTakeProfitPrice,
    settings.breakEvenOffsetPct,
    settings.breakEvenTriggerPct,
    settings.cooldownSec,
    settings.rsiBuyBelow,
    settings.trailingStopPct,
    settings.useBreakEven,
    settings.useMacdFilter,
    settings.useRsiFilter,
    settings.useTrailingStop,
    slowSma,
  ]);

  const modeBadge = executionMode === 'live'
    ? `Live ${liveTestMode ? 'TEST' : 'LIVE'}`
    : 'Paper';

  const sessionOpen = isSessionOpenNow();
  const guardDayPnl = totalEquity - guardState.dayStartEquity;

  const signalStatus = useMemo(() => {
    if (!hasEnoughData) return 'WAITING_DATA';
    if (guardState.haltedReason) return 'HALTED';

    const hasPosition = wallet.positionQty > 0;
    const trendUp = Number.isFinite(fastSma) && Number.isFinite(slowSma) && fastSma > slowSma;
    const trendDown = Number.isFinite(fastSma) && Number.isFinite(slowSma) && fastSma < slowSma;

    if (!hasPosition) {
      if (!sessionOpen) return 'OUT_OF_SESSION';
      if (trendUp && passesBuyFilters()) return 'BUY_READY';
      return 'NO_ENTRY';
    }

    if (trendDown && passesSellFilters()) return 'SELL_READY';
    return 'HOLD';
  }, [
    fastSma,
    guardState.haltedReason,
    hasEnoughData,
    passesBuyFilters,
    passesSellFilters,
    sessionOpen,
    slowSma,
    wallet.positionQty,
  ]);

  const requestNotificationPermission = () => {
    if (typeof window === 'undefined' || !('Notification' in window)) return;
    if (Notification.permission === 'default') {
      void Notification.requestPermission();
    }
  };

  const clearGuardHalt = () => {
    updateGuardState((prev) => (
      prev.haltedReason ? { ...prev, haltedReason: null } : prev
    ));
  };

  return (
    <section className="bg-gray-800 rounded-lg shadow-lg p-5 text-white">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between mb-5">
        <div>
          <h2 className="text-xl font-bold">Auto Trading Bot ({modeBadge})</h2>
          <p className="text-sm text-gray-300">
            Pair: <span className="font-semibold">{pairSymbol}</span>
            {' · '}
            Price: <span className="font-semibold">{formatUsd(currentPrice)}</span>
            {' · '}
            24h: <span className={Number.isFinite(marketChange24h) && marketChange24h >= 0 ? 'text-green-400' : 'text-red-400'}>{formatPercent(marketChange24h)}</span>
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setExecutionMode('paper')}
            className={`px-3 py-2 rounded-md text-sm font-semibold ${executionMode === 'paper' ? 'bg-indigo-600 hover:bg-indigo-500' : 'bg-gray-700 hover:bg-gray-600'}`}
          >
            Paper
          </button>
          <button
            type="button"
            onClick={() => setExecutionMode('live')}
            className={`px-3 py-2 rounded-md text-sm font-semibold ${executionMode === 'live' ? 'bg-indigo-600 hover:bg-indigo-500' : 'bg-gray-700 hover:bg-gray-600'}`}
          >
            Live
          </button>
          <button
            type="button"
            onClick={() => {
              if (isAutoEnabled) {
                setIsAutoEnabled(false);
                return;
              }
              clearGuardHalt();
              setIsAutoEnabled(true);
            }}
            className={`px-4 py-2 rounded-md font-semibold ${isAutoEnabled ? 'bg-green-600 hover:bg-green-500' : 'bg-gray-700 hover:bg-gray-600'}`}
          >
            {isAutoEnabled ? 'Auto ON' : 'Auto OFF'}
          </button>
        </div>
      </div>

      {executionMode === 'live' && (
        <div className="mb-5 rounded-md border border-gray-700 bg-gray-900/50 p-3 text-sm">
          <p className="text-gray-200">
            API: {liveStatus.loading ? 'Checking...' : liveStatus.configured ? 'Configured' : 'Not configured'}
            {' · '}
            Connection: {liveStatus.apiConnected ? 'Online' : 'Offline'}
            {' · '}
            Live Permission: {liveStatus.liveOrdersEnabled ? 'Enabled' : 'Disabled'}
          </p>
          <p className="text-gray-400">
            Base URL: {liveStatus.baseUrl}
            {' · '}
            Exchange Balance: {formatUsd(liveBalances.usdtFree)} USDT / {formatQty(liveBalances.baseFree)} {baseAsset}
            {liveBalances.updatedAt && ` · Sync ${liveBalances.updatedAt.toLocaleTimeString()}`}
          </p>
          {liveStatus.error && <p className="text-red-300 mt-1">{liveStatus.error}</p>}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 mb-5">
        <div className="bg-gray-700/60 rounded-md p-3">
          <p className="text-xs text-gray-300">Strategy Cash</p>
          <p className="text-lg font-semibold">{formatUsd(wallet.cash)}</p>
        </div>
        <div className="bg-gray-700/60 rounded-md p-3">
          <p className="text-xs text-gray-300">Position</p>
          <p className="text-lg font-semibold">{formatQty(wallet.positionQty)} {symbol}</p>
          <p className="text-xs text-gray-400">Avg: {wallet.avgEntry > 0 ? formatUsd(wallet.avgEntry) : '-'}</p>
        </div>
        <div className="bg-gray-700/60 rounded-md p-3">
          <p className="text-xs text-gray-300">Equity</p>
          <p className="text-lg font-semibold">{formatUsd(totalEquity)}</p>
          <p className={`text-xs ${totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>PnL: {formatUsd(totalPnl)}</p>
        </div>
        <div className="bg-gray-700/60 rounded-md p-3">
          <p className="text-xs text-gray-300">Signals</p>
          <p className="text-xs text-gray-100">MA: {Number.isFinite(fastSma) ? fastSma.toFixed(2) : '-'} / {Number.isFinite(slowSma) ? slowSma.toFixed(2) : '-'}</p>
          <p className="text-xs text-gray-100">RSI: {chartIndicators.rsi !== null ? chartIndicators.rsi.toFixed(2) : '-'}</p>
          <p className="text-xs text-gray-400">MACD: {chartIndicators.macd !== null ? chartIndicators.macd.toFixed(4) : '-'} / {chartIndicators.signal !== null ? chartIndicators.signal.toFixed(4) : '-'}</p>
        </div>
        <div className="bg-gray-700/60 rounded-md p-3">
          <p className="text-xs text-gray-300">Guard / Session</p>
          <p className="text-xs text-gray-100">Status: {signalStatus}</p>
          <p className="text-xs text-gray-100">Session: {sessionOpen ? 'Open' : 'Closed'}</p>
          <p className={`text-xs ${guardDayPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>DayPnL: {formatUsd(guardDayPnl)}</p>
        </div>
      </div>

      {guardState.haltedReason && (
        <div className="mb-4 rounded-md border border-red-500/40 bg-red-900/20 px-3 py-2 text-sm text-red-200 flex items-center justify-between gap-3">
          <span>Risk Guard Halted Auto: {guardState.haltedReason}</span>
          <button
            type="button"
            onClick={clearGuardHalt}
            className="px-2 py-1 rounded bg-red-700 hover:bg-red-600 text-xs font-semibold"
          >
            Clear Halt
          </button>
        </div>
      )}

      <div className="rounded-md border border-gray-700 bg-gray-900/40 p-3 mb-4">
        <p className="text-sm font-semibold mb-3">Strategy & Entry Confirmation</p>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <label className="text-xs text-gray-300">
            Capital (USDT)
            <input
              type="number"
              min="100"
              step="50"
              value={settings.initialCapital}
              onChange={(event) => updateNumberSetting('initialCapital', event.target.value, 100, 1000000, true)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300">
            Position Sizing
            <select
              value={settings.positionSizing}
              onChange={(event) => updateTextSetting('positionSizing', event.target.value)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            >
              <option value="percent">Fixed % of cash</option>
              <option value="risk">Risk % per trade</option>
            </select>
          </label>

          {settings.positionSizing === 'percent' ? (
            <label className="text-xs text-gray-300">
              Order Size %
              <input
                type="number"
                min="1"
                max="100"
                step="1"
                value={settings.orderSizePct}
                onChange={(event) => updateNumberSetting('orderSizePct', event.target.value, 1, 100)}
                className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
              />
            </label>
          ) : (
            <label className="text-xs text-gray-300">
              Risk per Trade %
              <input
                type="number"
                min="0.1"
                max="10"
                step="0.1"
                value={settings.riskPerTradePct}
                onChange={(event) => updateNumberSetting('riskPerTradePct', event.target.value, 0.1, 10)}
                className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
              />
            </label>
          )}

          <label className="text-xs text-gray-300">
            Fast MA
            <input
              type="number"
              min="2"
              max="100"
              step="1"
              value={settings.fastMa}
              onChange={(event) => updateNumberSetting('fastMa', event.target.value, 2, 100, true)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300">
            Slow MA
            <input
              type="number"
              min="3"
              max="200"
              step="1"
              value={settings.slowMa}
              onChange={(event) => updateNumberSetting('slowMa', event.target.value, 3, 200, true)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300">
            Cooldown (sec)
            <input
              type="number"
              min="5"
              max="300"
              step="5"
              value={settings.cooldownSec}
              onChange={(event) => updateNumberSetting('cooldownSec', event.target.value, 5, 300, true)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300 flex items-end">
            <span className="w-full bg-gray-700 rounded-md px-3 py-2 text-sm text-white flex items-center justify-between">
              Use RSI filter
              <input
                type="checkbox"
                checked={settings.useRsiFilter}
                onChange={(event) => updateBooleanSetting('useRsiFilter', event.target.checked)}
                className="ml-2"
              />
            </span>
          </label>

          <label className="text-xs text-gray-300">
            RSI Buy Below
            <input
              type="number"
              min="1"
              max="99"
              step="1"
              value={settings.rsiBuyBelow}
              onChange={(event) => updateNumberSetting('rsiBuyBelow', event.target.value, 1, 99)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300">
            RSI Sell Above
            <input
              type="number"
              min="1"
              max="99"
              step="1"
              value={settings.rsiSellAbove}
              onChange={(event) => updateNumberSetting('rsiSellAbove', event.target.value, 1, 99)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300 flex items-end">
            <span className="w-full bg-gray-700 rounded-md px-3 py-2 text-sm text-white flex items-center justify-between">
              Use MACD filter
              <input
                type="checkbox"
                checked={settings.useMacdFilter}
                onChange={(event) => updateBooleanSetting('useMacdFilter', event.target.checked)}
                className="ml-2"
              />
            </span>
          </label>

          <div className="col-span-2 md:col-span-2 lg:col-span-2 text-xs text-gray-400 bg-gray-700/40 rounded-md px-3 py-2">
            Indicator feed: {chartIndicators.aligned ? `TF ${chartIndicators.timeframe.toUpperCase()}${chartIndicators.updatedAt ? ` · ${chartIndicators.updatedAt.toLocaleTimeString()}` : ''}` : 'Waiting chart indicators'}
            <br />
            ATR: {chartIndicators.atr !== null ? chartIndicators.atr.toFixed(2) : '-'}
          </div>
        </div>
      </div>

      <div className="rounded-md border border-gray-700 bg-gray-900/40 p-3 mb-4">
        <p className="text-sm font-semibold mb-3">Smart Exit (SL/TP)</p>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <label className="text-xs text-gray-300">
            Stop Loss Mode
            <select
              value={settings.slMode}
              onChange={(event) => updateTextSetting('slMode', event.target.value)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            >
              <option value="fixed">Fixed %</option>
              <option value="atr">ATR Multiplier</option>
            </select>
          </label>

          {settings.slMode === 'fixed' ? (
            <label className="text-xs text-gray-300">
              Stop Loss %
              <input
                type="number"
                min="0.2"
                max="20"
                step="0.1"
                value={settings.stopLossPct}
                onChange={(event) => updateNumberSetting('stopLossPct', event.target.value, 0.2, 20)}
                className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
              />
            </label>
          ) : (
            <label className="text-xs text-gray-300">
              ATR SL Multiplier
              <input
                type="number"
                min="0.2"
                max="10"
                step="0.1"
                value={settings.atrSlMultiplier}
                onChange={(event) => updateNumberSetting('atrSlMultiplier', event.target.value, 0.2, 10)}
                className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
              />
            </label>
          )}

          <label className="text-xs text-gray-300">
            Take Profit Mode
            <select
              value={settings.tpMode}
              onChange={(event) => updateTextSetting('tpMode', event.target.value)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            >
              <option value="fixed">Fixed %</option>
              <option value="atr">ATR Multiplier</option>
            </select>
          </label>

          {settings.tpMode === 'fixed' ? (
            <label className="text-xs text-gray-300">
              Take Profit %
              <input
                type="number"
                min="0.2"
                max="50"
                step="0.1"
                value={settings.takeProfitPct}
                onChange={(event) => updateNumberSetting('takeProfitPct', event.target.value, 0.2, 50)}
                className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
              />
            </label>
          ) : (
            <label className="text-xs text-gray-300">
              ATR TP Multiplier
              <input
                type="number"
                min="0.2"
                max="15"
                step="0.1"
                value={settings.atrTpMultiplier}
                onChange={(event) => updateNumberSetting('atrTpMultiplier', event.target.value, 0.2, 15)}
                className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
              />
            </label>
          )}

          <label className="text-xs text-gray-300 flex items-end">
            <span className="w-full bg-gray-700 rounded-md px-3 py-2 text-sm text-white flex items-center justify-between">
              Trailing Stop
              <input
                type="checkbox"
                checked={settings.useTrailingStop}
                onChange={(event) => updateBooleanSetting('useTrailingStop', event.target.checked)}
                className="ml-2"
              />
            </span>
          </label>

          <label className="text-xs text-gray-300">
            Trailing %
            <input
              type="number"
              min="0.1"
              max="20"
              step="0.1"
              value={settings.trailingStopPct}
              onChange={(event) => updateNumberSetting('trailingStopPct', event.target.value, 0.1, 20)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300 flex items-end">
            <span className="w-full bg-gray-700 rounded-md px-3 py-2 text-sm text-white flex items-center justify-between">
              Break-even
              <input
                type="checkbox"
                checked={settings.useBreakEven}
                onChange={(event) => updateBooleanSetting('useBreakEven', event.target.checked)}
                className="ml-2"
              />
            </span>
          </label>

          <label className="text-xs text-gray-300">
            BE Trigger %
            <input
              type="number"
              min="0.1"
              max="30"
              step="0.1"
              value={settings.breakEvenTriggerPct}
              onChange={(event) => updateNumberSetting('breakEvenTriggerPct', event.target.value, 0.1, 30)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300">
            BE Offset %
            <input
              type="number"
              min="0"
              max="5"
              step="0.1"
              value={settings.breakEvenOffsetPct}
              onChange={(event) => updateNumberSetting('breakEvenOffsetPct', event.target.value, 0, 5)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <div className="col-span-2 md:col-span-2 lg:col-span-2 text-xs text-gray-400 bg-gray-700/40 rounded-md px-3 py-2">
            Active stop: {wallet.positionQty > 0 ? formatUsd(wallet.dynamicStop) : '-'}
            <br />
            Break-even armed: {wallet.breakEvenArmed ? 'Yes' : 'No'}
          </div>
        </div>
      </div>

      <div className="rounded-md border border-gray-700 bg-gray-900/40 p-3 mb-4">
        <p className="text-sm font-semibold mb-3">Risk Guard + Session + Alerts</p>
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <label className="text-xs text-gray-300">
            Max Daily Loss %
            <input
              type="number"
              min="0.5"
              max="50"
              step="0.5"
              value={settings.maxDailyLossPct}
              onChange={(event) => updateNumberSetting('maxDailyLossPct', event.target.value, 0.5, 50)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300">
            Max Drawdown %
            <input
              type="number"
              min="0.5"
              max="80"
              step="0.5"
              value={settings.maxDrawdownPct}
              onChange={(event) => updateNumberSetting('maxDrawdownPct', event.target.value, 0.5, 80)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300">
            Max Trades/Day
            <input
              type="number"
              min="1"
              max="500"
              step="1"
              value={settings.maxTradesPerDay}
              onChange={(event) => updateNumberSetting('maxTradesPerDay', event.target.value, 1, 500, true)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300">
            Max Consecutive Losses
            <input
              type="number"
              min="1"
              max="20"
              step="1"
              value={settings.maxConsecutiveLosses}
              onChange={(event) => updateNumberSetting('maxConsecutiveLosses', event.target.value, 1, 20, true)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300 flex items-end">
            <span className="w-full bg-gray-700 rounded-md px-3 py-2 text-sm text-white flex items-center justify-between">
              Session Filter
              <input
                type="checkbox"
                checked={settings.sessionFilterEnabled}
                onChange={(event) => updateBooleanSetting('sessionFilterEnabled', event.target.checked)}
                className="ml-2"
              />
            </span>
          </label>

          <label className="text-xs text-gray-300">
            Session Start (hour)
            <input
              type="number"
              min="0"
              max="23"
              step="1"
              value={settings.sessionStartHour}
              onChange={(event) => updateNumberSetting('sessionStartHour', event.target.value, 0, 23, true)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300">
            Session End (hour)
            <input
              type="number"
              min="0"
              max="23"
              step="1"
              value={settings.sessionEndHour}
              onChange={(event) => updateNumberSetting('sessionEndHour', event.target.value, 0, 23, true)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300">
            Fee %
            <input
              type="number"
              min="0"
              max="2"
              step="0.01"
              value={settings.feePct}
              onChange={(event) => updateNumberSetting('feePct', event.target.value, 0, 2)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300">
            Slippage %
            <input
              type="number"
              min="0"
              max="2"
              step="0.01"
              value={settings.slippagePct}
              onChange={(event) => updateNumberSetting('slippagePct', event.target.value, 0, 2)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <label className="text-xs text-gray-300 flex items-end">
            <span className="w-full bg-gray-700 rounded-md px-3 py-2 text-sm text-white flex items-center justify-between">
              Enable Alerts
              <input
                type="checkbox"
                checked={settings.alertsEnabled}
                onChange={(event) => {
                  updateBooleanSetting('alertsEnabled', event.target.checked);
                  if (event.target.checked) requestNotificationPermission();
                }}
                className="ml-2"
              />
            </span>
          </label>

          <label className="text-xs text-gray-300 col-span-2 md:col-span-2 lg:col-span-2">
            Webhook URL (optional)
            <input
              type="text"
              value={settings.webhookUrl}
              onChange={(event) => updateTextSetting('webhookUrl', event.target.value)}
              placeholder="https://your-webhook-endpoint"
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>

          <div className="col-span-2 md:col-span-2 lg:col-span-2 text-xs text-gray-400 bg-gray-700/40 rounded-md px-3 py-2">
            Day trades: {guardState.tradesToday} / {settings.maxTradesPerDay}
            <br />
            Drawdown: {guardState.currentDrawdownPct.toFixed(2)}% (max {guardState.maxDrawdownPct.toFixed(2)}%)
            <br />
            Consecutive losses: {guardState.consecutiveLosses}
          </div>
        </div>
      </div>

      {executionMode === 'live' && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5">
          <label className="text-xs text-gray-300">
            Live Buy Amount (USDT)
            <input
              type="number"
              min="10"
              step="1"
              value={settings.liveBuyUsdt}
              onChange={(event) => updateNumberSetting('liveBuyUsdt', event.target.value, 10, 100000)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>
          <label className="text-xs text-gray-300">
            Live Sell %
            <input
              type="number"
              min="1"
              max="100"
              step="1"
              value={settings.liveSellPct}
              onChange={(event) => updateNumberSetting('liveSellPct', event.target.value, 1, 100)}
              className="mt-1 w-full bg-gray-700 rounded-md px-2 py-2 text-sm text-white"
            />
          </label>
          <label className="text-xs text-gray-300 flex items-end">
            <span className="w-full bg-gray-700 rounded-md px-3 py-2 text-sm text-white flex items-center justify-between">
              Send as TEST order
              <input
                type="checkbox"
                checked={liveTestMode}
                onChange={(event) => setLiveTestMode(event.target.checked)}
                className="ml-2"
              />
            </span>
          </label>
        </div>
      )}

      <div className="flex flex-wrap gap-2 mb-4">
        <button
          type="button"
          onClick={() => { void executeBuy('Manual buy click'); }}
          disabled={isLiveBusy}
          className="px-3 py-2 rounded-md bg-blue-600 hover:bg-blue-500 text-sm font-semibold disabled:opacity-60"
        >
          Manual Buy
        </button>
        <button
          type="button"
          onClick={() => { void executeSell('Manual sell click'); }}
          disabled={isLiveBusy}
          className="px-3 py-2 rounded-md bg-amber-600 hover:bg-amber-500 text-sm font-semibold disabled:opacity-60"
        >
          Manual Sell
        </button>
        <button
          type="button"
          onClick={runBacktest}
          className="px-3 py-2 rounded-md bg-emerald-600 hover:bg-emerald-500 text-sm font-semibold"
        >
          Run Backtest
        </button>
        <button
          type="button"
          onClick={resetBot}
          className="px-3 py-2 rounded-md bg-gray-600 hover:bg-gray-500 text-sm font-semibold"
        >
          Reset Bot
        </button>
        {executionMode === 'live' && (
          <button
            type="button"
            onClick={() => { void syncLiveAccount(); }}
            className="px-3 py-2 rounded-md bg-purple-600 hover:bg-purple-500 text-sm font-semibold"
          >
            Sync Account
          </button>
        )}
        <span className="text-xs text-gray-400 self-center">
          Trades: {wallet.trades} | Closed Trades: {tradeHistory.length} | History ticks: {priceHistory.length}
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <div className="bg-gray-900/70 rounded-md p-3">
          <p className="text-sm font-semibold mb-2">Backtest Report</p>
          {!backtestReport ? (
            <p className="text-xs text-gray-400">Run backtest to see win rate, PF, max drawdown, expectancy.</p>
          ) : backtestReport.error ? (
            <p className="text-xs text-red-300">{backtestReport.error}</p>
          ) : (
            <div className="text-xs space-y-1 text-gray-200">
              <p>Generated: {backtestReport.generatedAt.toLocaleTimeString()} · Samples: {backtestReport.sampleSize}</p>
              <p>Closed trades: {backtestReport.closedTrades} · Win rate: {backtestReport.winRate.toFixed(2)}%</p>
              <p>Profit factor: {formatFactor(backtestReport.profitFactor)} · Max DD: {backtestReport.maxDrawdownPct.toFixed(2)}%</p>
              <p>Expectancy: {formatUsd(backtestReport.expectancy)} / trade</p>
              <p className={backtestReport.netPnl >= 0 ? 'text-green-400' : 'text-red-400'}>
                Net PnL: {formatUsd(backtestReport.netPnl)} · Final Equity: {formatUsd(backtestReport.finalEquity)}
              </p>
            </div>
          )}
        </div>

        <div className="bg-gray-900/70 rounded-md p-3">
          <p className="text-sm font-semibold mb-2">Recent Closed Trades</p>
          {tradeHistory.length === 0 ? (
            <p className="text-xs text-gray-400">No closed trades yet.</p>
          ) : (
            <div className="max-h-40 overflow-y-auto space-y-2">
              {tradeHistory.slice(0, 8).map((trade) => (
                <div key={trade.id} className="text-xs border border-gray-700 rounded-md px-2 py-1">
                  <p className="text-gray-300">{trade.at.toLocaleTimeString()} · Qty {formatQty(trade.qty)} · Hold {Number.isFinite(trade.holdingSec) ? `${trade.holdingSec.toFixed(0)}s` : '-'}</p>
                  <p className="text-gray-400">Entry {formatUsd(trade.entry)} → Exit {formatUsd(trade.exit)} · Return {formatPercent(trade.returnPct)}</p>
                  <p className={trade.pnl >= 0 ? 'text-green-400' : 'text-red-400'}>PnL {formatUsd(trade.pnl)}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="bg-gray-900/70 rounded-md p-3">
        <p className="text-sm font-semibold mb-2">Trade Logs</p>
        {logs.length === 0 ? (
          <p className="text-xs text-gray-400">No trades yet.</p>
        ) : (
          <div className="max-h-60 overflow-y-auto space-y-2">
            {logs.map((log) => (
              <div key={log.id} className="text-xs border border-gray-700 rounded-md px-2 py-2">
                <p className="text-gray-300">
                  {log.at.toLocaleTimeString()} · {log.side}
                  {' · '}
                  {log.reason}
                </p>
                <p className="text-gray-400">
                  Exec: {formatUsd(log.price)}
                  {Number.isFinite(log.marketPrice) && ` (Mkt ${formatUsd(log.marketPrice)})`}
                  {' | '}Qty: {formatQty(log.qty)}
                  {' | '}Value: {formatUsd(log.value)}
                  {Number.isFinite(log.fee) && ` | Fee: ${formatUsd(log.fee)}`}
                  {Number.isFinite(log.slippageValue) && ` | Slip: ${formatUsd(log.slippageValue)}`}
                  {Number.isFinite(log.pnl) && ` | PnL: ${formatUsd(log.pnl)}`}
                </p>
                {(Number.isFinite(log.stopPrice) || Number.isFinite(log.takeProfitPrice)) && (
                  <p className="text-gray-500">
                    Stop: {Number.isFinite(log.stopPrice) ? formatUsd(log.stopPrice) : '-'}
                    {' | '}TP: {Number.isFinite(log.takeProfitPrice) ? formatUsd(log.takeProfitPrice) : '-'}
                  </p>
                )}
                {log.indicators && (
                  <p className="text-gray-500">
                    Sig MA {Number.isFinite(log.indicators.fastMa) ? log.indicators.fastMa.toFixed(2) : '-'} /
                    {' '}{Number.isFinite(log.indicators.slowMa) ? log.indicators.slowMa.toFixed(2) : '-'}
                    {' · '}RSI {Number.isFinite(log.indicators.rsi) ? log.indicators.rsi.toFixed(2) : '-'}
                    {' · '}MACD {Number.isFinite(log.indicators.macd) ? log.indicators.macd.toFixed(4) : '-'} /
                    {' '}{Number.isFinite(log.indicators.signal) ? log.indicators.signal.toFixed(4) : '-'}
                    {' · '}ATR {Number.isFinite(log.indicators.atr) ? log.indicators.atr.toFixed(2) : '-'}
                    {' · '}TF {log.indicators.timeframe || '-'}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
