import { useEffect, useMemo, useRef, useState } from 'react';
import {
  CandlestickSeries,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
} from 'lightweight-charts';

const BINANCE_KLINES_ENDPOINTS = [
  'https://data-api.binance.vision/api/v3/klines',
  'https://api.binance.com/api/v3/klines',
];

const REQUEST_TIMEOUT_MS = 5000;

const TIMEFRAMES = [
  { key: '15m', label: '15m', interval: '15m', limit: '120', refreshMs: 15000 },
  { key: '1h', label: '1H', interval: '1h', limit: '120', refreshMs: 20000 },
  { key: '4h', label: '4H', interval: '4h', limit: '120', refreshMs: 30000 },
  { key: '1d', label: '1D', interval: '1d', limit: '120', refreshMs: 60000 },
];

const formatPrice = (value) => {
  if (!Number.isFinite(value)) return '-';
  if (value >= 1000) return `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  if (value >= 1) return `$${value.toLocaleString(undefined, { maximumFractionDigits: 4 })}`;
  return `$${value.toLocaleString(undefined, { maximumFractionDigits: 6 })}`;
};

const formatPercent = (value) => {
  if (!Number.isFinite(value)) return '-';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
};

const calculateEMA = (values, period) => {
  const ema = Array(values.length).fill(null);
  if (values.length < period) return ema;

  const multiplier = 2 / (period + 1);
  let seedSum = 0;
  for (let i = 0; i < period; i += 1) {
    seedSum += values[i];
  }
  ema[period - 1] = seedSum / period;

  for (let i = period; i < values.length; i += 1) {
    const prev = ema[i - 1];
    ema[i] = ((values[i] - prev) * multiplier) + prev;
  }

  return ema;
};

const calculateEMAFromNullable = (values, period) => {
  const ema = Array(values.length).fill(null);
  const multiplier = 2 / (period + 1);
  const seedBuffer = [];
  let prevEma = null;

  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    if (!Number.isFinite(value)) {
      continue;
    }

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

const calculateRSIData = (candles, period = 14) => {
  if (!Array.isArray(candles) || candles.length <= period) return [];

  const closes = candles.map((item) => item.close);
  const rsi = Array(candles.length).fill(null);
  let gains = 0;
  let losses = 0;

  for (let i = 1; i <= period; i += 1) {
    const change = closes[i] - closes[i - 1];
    gains += Math.max(change, 0);
    losses += Math.max(-change, 0);
  }

  let avgGain = gains / period;
  let avgLoss = losses / period;

  const firstRs = avgLoss === 0 ? Number.POSITIVE_INFINITY : avgGain / avgLoss;
  rsi[period] = 100 - (100 / (1 + firstRs));

  for (let i = period + 1; i < closes.length; i += 1) {
    const change = closes[i] - closes[i - 1];
    const gain = Math.max(change, 0);
    const loss = Math.max(-change, 0);

    avgGain = ((avgGain * (period - 1)) + gain) / period;
    avgLoss = ((avgLoss * (period - 1)) + loss) / period;

    const rs = avgLoss === 0 ? Number.POSITIVE_INFINITY : avgGain / avgLoss;
    rsi[i] = 100 - (100 / (1 + rs));
  }

  return rsi
    .map((value, index) => {
      if (!Number.isFinite(value)) return null;
      return { time: candles[index].time, value: Number(value.toFixed(2)) };
    })
    .filter(Boolean);
};

const calculateMACDData = (candles) => {
  if (!Array.isArray(candles) || candles.length < 35) {
    return {
      macdLine: [],
      signalLine: [],
      histogram: [],
      lastMacd: null,
      lastSignal: null,
    };
  }

  const closes = candles.map((item) => item.close);
  const ema12 = calculateEMA(closes, 12);
  const ema26 = calculateEMA(closes, 26);

  const macdRaw = closes.map((_, index) => {
    if (!Number.isFinite(ema12[index]) || !Number.isFinite(ema26[index])) return null;
    return ema12[index] - ema26[index];
  });

  const signalRaw = calculateEMAFromNullable(macdRaw, 9);
  let prevHistogram = null;

  const macdLine = [];
  const signalLine = [];
  const histogram = [];

  for (let i = 0; i < candles.length; i += 1) {
    const macd = macdRaw[i];
    const signal = signalRaw[i];
    if (!Number.isFinite(macd) || !Number.isFinite(signal)) continue;

    const hist = macd - signal;
    const color =
      hist >= 0
        ? (prevHistogram !== null && hist < prevHistogram ? '#86efac' : '#22c55e')
        : (prevHistogram !== null && hist > prevHistogram ? '#fca5a5' : '#ef4444');

    macdLine.push({
      time: candles[i].time,
      value: Number(macd.toFixed(6)),
    });
    signalLine.push({
      time: candles[i].time,
      value: Number(signal.toFixed(6)),
    });
    histogram.push({
      time: candles[i].time,
      value: Number(hist.toFixed(6)),
      color,
    });

    prevHistogram = hist;
  }

  const lastMacd = macdLine.length > 0 ? macdLine[macdLine.length - 1].value : null;
  const lastSignal = signalLine.length > 0 ? signalLine[signalLine.length - 1].value : null;

  return {
    macdLine,
    signalLine,
    histogram,
    lastMacd,
    lastSignal,
  };
};

const calculateATRData = (candles, period = 14) => {
  if (!Array.isArray(candles) || candles.length <= period) return [];

  const atr = Array(candles.length).fill(null);
  let trSum = 0;

  for (let i = 1; i <= period; i += 1) {
    const high = candles[i].high;
    const low = candles[i].low;
    const prevClose = candles[i - 1].close;
    const tr = Math.max(
      high - low,
      Math.abs(high - prevClose),
      Math.abs(low - prevClose),
    );
    trSum += tr;
  }

  atr[period] = trSum / period;

  for (let i = period + 1; i < candles.length; i += 1) {
    const high = candles[i].high;
    const low = candles[i].low;
    const prevClose = candles[i - 1].close;
    const tr = Math.max(
      high - low,
      Math.abs(high - prevClose),
      Math.abs(low - prevClose),
    );
    atr[i] = ((atr[i - 1] * (period - 1)) + tr) / period;
  }

  return atr;
};

const PriceChart = ({ symbol, onIndicatorUpdate }) => {
  const mainContainerRef = useRef(null);
  const rsiContainerRef = useRef(null);
  const macdContainerRef = useRef(null);

  const chartRefs = useRef({
    main: null,
    rsi: null,
    macd: null,
  });
  const seriesRefs = useRef({
    main: null,
    rsi: null,
    macd: null,
    signal: null,
    histogram: null,
  });

  const [chartState, setChartState] = useState('loading');
  const [timeframe, setTimeframe] = useState('1h');
  const [chartMode, setChartMode] = useState('candles');
  const [activePair, setActivePair] = useState(symbol || 'BTC');
  const [lastUpdated, setLastUpdated] = useState(null);
  const [indicatorSummary, setIndicatorSummary] = useState({
    rsi: null,
    macd: null,
    signal: null,
    atr: null,
    timeframe: '1h',
  });
  const [summary, setSummary] = useState({
    last: null,
    changePct: null,
    high: null,
    low: null,
  });

  const selectedFrame = useMemo(
    () => TIMEFRAMES.find((frame) => frame.key === timeframe) || TIMEFRAMES[1],
    [timeframe]
  );

  useEffect(() => {
    if (!mainContainerRef.current || !rsiContainerRef.current || !macdContainerRef.current) return undefined;

    const mainChart = createChart(mainContainerRef.current, {
      width: mainContainerRef.current.clientWidth || 600,
      height: 420,
      layout: {
        background: { color: '#111827' },
        textColor: '#cbd5e1',
      },
      grid: {
        vertLines: { color: '#243244' },
        horzLines: { color: '#243244' },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: '#334155' },
      timeScale: {
        borderColor: '#334155',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const mainSeries = chartMode === 'candles'
      ? mainChart.addSeries(CandlestickSeries, {
          upColor: '#22c55e',
          downColor: '#ef4444',
          borderVisible: false,
          wickUpColor: '#22c55e',
          wickDownColor: '#ef4444',
        })
      : mainChart.addSeries(LineSeries, {
          color: '#22d3ee',
          lineWidth: 2,
          priceLineVisible: false,
        });

    const rsiChart = createChart(rsiContainerRef.current, {
      width: rsiContainerRef.current.clientWidth || 600,
      height: 130,
      layout: {
        background: { color: '#0f172a' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      rightPriceScale: {
        borderColor: '#334155',
        scaleMargins: {
          top: 0.15,
          bottom: 0.15,
        },
      },
      timeScale: {
        borderColor: '#334155',
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { mode: CrosshairMode.Normal },
    });

    const rsiSeries = rsiChart.addSeries(LineSeries, {
      color: '#a78bfa',
      lineWidth: 2,
      priceLineVisible: false,
    });

    rsiSeries.createPriceLine({
      price: 70,
      color: '#f97316',
      lineStyle: LineStyle.Dashed,
      lineWidth: 1,
      axisLabelVisible: false,
      title: '70',
    });
    rsiSeries.createPriceLine({
      price: 30,
      color: '#38bdf8',
      lineStyle: LineStyle.Dashed,
      lineWidth: 1,
      axisLabelVisible: false,
      title: '30',
    });

    const macdChart = createChart(macdContainerRef.current, {
      width: macdContainerRef.current.clientWidth || 600,
      height: 150,
      layout: {
        background: { color: '#0f172a' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      rightPriceScale: {
        borderColor: '#334155',
        scaleMargins: {
          top: 0.15,
          bottom: 0.15,
        },
      },
      timeScale: {
        borderColor: '#334155',
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: { mode: CrosshairMode.Normal },
    });

    const histogramSeries = macdChart.addSeries(HistogramSeries, {
      priceLineVisible: false,
      base: 0,
    });
    const macdSeries = macdChart.addSeries(LineSeries, {
      color: '#f59e0b',
      lineWidth: 2,
      priceLineVisible: false,
    });
    const signalSeries = macdChart.addSeries(LineSeries, {
      color: '#38bdf8',
      lineWidth: 2,
      priceLineVisible: false,
    });

    chartRefs.current = {
      main: mainChart,
      rsi: rsiChart,
      macd: macdChart,
    };
    seriesRefs.current = {
      main: mainSeries,
      rsi: rsiSeries,
      macd: macdSeries,
      signal: signalSeries,
      histogram: histogramSeries,
    };

    const syncingGuard = { value: false };
    const chartsForSync = [mainChart, rsiChart, macdChart];
    const unsubscribeFns = chartsForSync.map((sourceChart, sourceIndex) => {
      const sourceTimeScale = sourceChart.timeScale();
      const handler = (range) => {
        if (!range || syncingGuard.value) return;

        syncingGuard.value = true;
        chartsForSync.forEach((targetChart, targetIndex) => {
          if (targetIndex !== sourceIndex) {
            targetChart.timeScale().setVisibleLogicalRange(range);
          }
        });
        syncingGuard.value = false;
      };

      sourceTimeScale.subscribeVisibleLogicalRangeChange(handler);
      return () => sourceTimeScale.unsubscribeVisibleLogicalRangeChange(handler);
    });

    const handleResize = () => {
      const { main, rsi, macd } = chartRefs.current;
      if (main && mainContainerRef.current?.clientWidth > 0) {
        main.applyOptions({ width: mainContainerRef.current.clientWidth });
      }
      if (rsi && rsiContainerRef.current?.clientWidth > 0) {
        rsi.applyOptions({ width: rsiContainerRef.current.clientWidth });
      }
      if (macd && macdContainerRef.current?.clientWidth > 0) {
        macd.applyOptions({ width: macdContainerRef.current.clientWidth });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      unsubscribeFns.forEach((fn) => fn());
      chartRefs.current.main?.remove();
      chartRefs.current.rsi?.remove();
      chartRefs.current.macd?.remove();
      chartRefs.current = { main: null, rsi: null, macd: null };
      seriesRefs.current = { main: null, rsi: null, macd: null, signal: null, histogram: null };
    };
  }, [chartMode]);

  useEffect(() => {
    if (!seriesRefs.current.main || !chartRefs.current.main || !symbol) return undefined;

    const rootAbortController = new AbortController();
    let isMounted = true;
    let isFetching = false;

    const fetchWithTimeout = async (url, signal) => {
      const requestController = new AbortController();
      const onAbort = () => requestController.abort();
      signal.addEventListener('abort', onAbort, { once: true });
      const timeoutId = setTimeout(() => requestController.abort(), REQUEST_TIMEOUT_MS);

      try {
        const response = await fetch(url, { signal: requestController.signal });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return await response.json();
      } finally {
        clearTimeout(timeoutId);
        signal.removeEventListener('abort', onAbort);
      }
    };

    const fetchCandles = async (pairSymbol) => {
      let lastError = null;

      for (const endpoint of BINANCE_KLINES_ENDPOINTS) {
        try {
          const params = new URLSearchParams({
            symbol: pairSymbol,
            interval: selectedFrame.interval,
            limit: selectedFrame.limit,
          });

          const data = await fetchWithTimeout(`${endpoint}?${params.toString()}`, rootAbortController.signal);
          if (!Array.isArray(data)) {
            throw new Error('Unexpected klines response format');
          }

          const candles = data
            .map((item) => ({
              time: item[0] / 1000,
              open: Number.parseFloat(item[1]),
              high: Number.parseFloat(item[2]),
              low: Number.parseFloat(item[3]),
              close: Number.parseFloat(item[4]),
            }))
            .filter(
              (item) =>
                Number.isFinite(item.time) &&
                Number.isFinite(item.open) &&
                Number.isFinite(item.high) &&
                Number.isFinite(item.low) &&
                Number.isFinite(item.close)
            );

          if (candles.length === 0) {
            throw new Error('No valid candle data');
          }

          return candles;
        } catch (error) {
          if (error?.name === 'AbortError') return null;
          lastError = error;
        }
      }

      throw lastError || new Error('Unable to load klines data');
    };

    const applySeriesData = (candles, resolvedPair, shouldFitContent = false) => {
      const {
        main,
        rsi,
        macd,
        signal,
        histogram,
      } = seriesRefs.current;
      const {
        main: mainChart,
        rsi: rsiChart,
        macd: macdChart,
      } = chartRefs.current;

      if (!main || !rsi || !macd || !signal || !histogram) return;
      if (!mainChart || !rsiChart || !macdChart) return;

      if (chartMode === 'candles') {
        main.setData(candles);
      } else {
        main.setData(candles.map((item) => ({ time: item.time, value: item.close })));
      }

      const rsiData = calculateRSIData(candles);
      rsi.setData(rsiData);

      const macdData = calculateMACDData(candles);
      macd.setData(macdData.macdLine);
      signal.setData(macdData.signalLine);
      histogram.setData(macdData.histogram);
      const atrSeries = calculateATRData(candles);
      const latestAtr = atrSeries.length > 0 ? atrSeries[atrSeries.length - 1] : null;

      const first = candles[0];
      const last = candles[candles.length - 1];
      const high = Math.max(...candles.map((item) => item.high));
      const low = Math.min(...candles.map((item) => item.low));
      const changePct = ((last.close - first.open) / first.open) * 100;

      setSummary({
        last: last.close,
        changePct,
        high,
        low,
      });
      setIndicatorSummary({
        rsi: rsiData.length > 0 ? rsiData[rsiData.length - 1].value : null,
        macd: macdData.lastMacd,
        signal: macdData.lastSignal,
        atr: Number.isFinite(latestAtr) ? Number(latestAtr.toFixed(6)) : null,
        timeframe: selectedFrame.key,
      });
      setLastUpdated(new Date());

      if (typeof onIndicatorUpdate === 'function') {
        onIndicatorUpdate({
          symbol: resolvedPair.replace('USDT', ''),
          pairSymbol: resolvedPair,
          timeframe: selectedFrame.key,
          updatedAt: Date.now(),
          rsi: rsiData.length > 0 ? rsiData[rsiData.length - 1].value : null,
          macd: macdData.lastMacd,
          signal: macdData.lastSignal,
          atr: Number.isFinite(latestAtr) ? Number(latestAtr.toFixed(6)) : null,
        });
      }

      if (shouldFitContent) {
        mainChart.timeScale().fitContent();
        rsiChart.timeScale().fitContent();
        macdChart.timeScale().fitContent();
      }
    };

    const refreshData = async (showLoadingOverlay = false) => {
      if (isFetching) return;
      isFetching = true;

      try {
        if (showLoadingOverlay) {
          setChartState('loading');
        }

        const requestedPair = `${symbol.toUpperCase()}USDT`;
        let candles = await fetchCandles(requestedPair);
        let resolvedPair = requestedPair;

        if ((!candles || candles.length === 0) && requestedPair !== 'BTCUSDT') {
          candles = await fetchCandles('BTCUSDT');
          resolvedPair = 'BTCUSDT';
        }

        if (!isMounted || !candles || candles.length === 0) {
          if (isMounted) setChartState('error');
          return;
        }

        applySeriesData(candles, resolvedPair, showLoadingOverlay);
        if (isMounted) {
          setActivePair(resolvedPair.replace('USDT', ''));
          setChartState('ready');
        }
      } catch (error) {
        console.error('Error refreshing chart data:', error);
        if (isMounted) {
          setChartState('error');
        }
      } finally {
        isFetching = false;
      }
    };

    refreshData(true);
    const intervalId = setInterval(() => {
      refreshData(false);
    }, selectedFrame.refreshMs);

    return () => {
      isMounted = false;
      rootAbortController.abort();
      clearInterval(intervalId);
    };
  }, [symbol, selectedFrame, chartMode, onIndicatorUpdate]);

  const handleResetView = () => {
    chartRefs.current.main?.timeScale().fitContent();
    chartRefs.current.rsi?.timeScale().fitContent();
    chartRefs.current.macd?.timeScale().fitContent();
  };

  return (
    <div className="bg-gradient-to-b from-gray-900 to-gray-800 border border-slate-700/70 rounded-lg p-3">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
        <div>
          <p className="text-xs text-cyan-300/80">
            {activePair} • {selectedFrame.label}
          </p>
          <div className="flex items-baseline gap-2">
            <span className="text-white font-semibold text-lg">{formatPrice(summary.last)}</span>
            <span className={`text-xs font-semibold ${summary.changePct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {formatPercent(summary.changePct)}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {TIMEFRAMES.map((frame) => (
            <button
              key={frame.key}
              type="button"
              onClick={() => setTimeframe(frame.key)}
              className={`px-2.5 py-1 text-xs rounded-md border transition-colors ${
                timeframe === frame.key
                  ? 'bg-cyan-400/20 text-cyan-200 border-cyan-300/70'
                  : 'bg-slate-800 text-gray-300 border-slate-600 hover:bg-slate-700'
              }`}
            >
              {frame.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setChartMode('candles')}
            className={`px-2.5 py-1 text-xs rounded-md border transition-colors ${
              chartMode === 'candles'
                ? 'bg-cyan-400/20 text-cyan-200 border-cyan-300/70'
                : 'bg-slate-800 text-gray-300 border-slate-600 hover:bg-slate-700'
            }`}
          >
            Candles
          </button>
          <button
            type="button"
            onClick={() => setChartMode('line')}
            className={`px-2.5 py-1 text-xs rounded-md border transition-colors ${
              chartMode === 'line'
                ? 'bg-cyan-400/20 text-cyan-200 border-cyan-300/70'
                : 'bg-slate-800 text-gray-300 border-slate-600 hover:bg-slate-700'
            }`}
          >
            Line
          </button>
          <button
            type="button"
            onClick={handleResetView}
            className="px-2.5 py-1 text-xs rounded-md border bg-slate-800 text-gray-300 border-slate-600 hover:bg-slate-700 transition-colors"
          >
            Reset
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-gray-300 mb-2 px-1">
        <span>
          H: {formatPrice(summary.high)} • L: {formatPrice(summary.low)}
        </span>
        <span>
          RSI(14): {indicatorSummary.rsi !== null ? indicatorSummary.rsi.toFixed(2) : '-'} • MACD:{' '}
          {indicatorSummary.macd !== null ? indicatorSummary.macd.toFixed(4) : '-'} / Signal:{' '}
          {indicatorSummary.signal !== null ? indicatorSummary.signal.toFixed(4) : '-'} • ATR:{' '}
          {indicatorSummary.atr !== null ? indicatorSummary.atr.toFixed(2) : '-'} • TF:{' '}
          {indicatorSummary.timeframe.toUpperCase()}
        </span>
        <span>{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : 'Updating...'}</span>
      </div>

      <div className="relative">
        {chartState === 'loading' && (
          <div className="absolute inset-0 z-10 flex items-center justify-center text-cyan-200 text-sm bg-slate-900/70 rounded-md">
            Loading chart...
          </div>
        )}

        {chartState === 'error' && (
          <div className="absolute inset-0 z-10 flex items-center justify-center text-red-300 text-sm bg-slate-900/70 rounded-md px-4 text-center">
            Chart data unavailable right now.
          </div>
        )}

        <div ref={mainContainerRef} className="w-full h-[420px] rounded-md overflow-hidden" />
      </div>

      <div className="mt-3 space-y-2">
        <div className="text-[11px] font-medium text-purple-300 px-1">RSI (14)</div>
        <div ref={rsiContainerRef} className="w-full h-[130px] rounded-md overflow-hidden border border-slate-700/70" />
      </div>

      <div className="mt-2 space-y-2">
        <div className="text-[11px] font-medium text-amber-300 px-1">MACD (12, 26, 9)</div>
        <div ref={macdContainerRef} className="w-full h-[150px] rounded-md overflow-hidden border border-slate-700/70" />
      </div>
    </div>
  );
};

export default PriceChart;
