from __future__ import annotations

import logging
import threading
import time
from typing import Any

import pandas as pd

from services.alerts import AlertService
from services.config import Settings
from services.exchange import ExchangeGateway
from services.indicators import (
    add_indicators,
    build_ai_filter_state,
    classify_strength,
    get_signal,
    safe_float,
    score_indicator_state,
)
from services.market_state import MarketStateManager
from services.state import RuntimeState
from services.trading import TradingService


class MarketService:
    def __init__(
        self,
        settings: Settings,
        state: RuntimeState,
        exchange: ExchangeGateway,
        market_state: MarketStateManager,
        alerts: AlertService,
        trading: TradingService,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.state = state
        self.exchange = exchange
        self.market_state = market_state
        self.alerts = alerts
        self.trading = trading
        self.logger = logger
        self._indicator_backoff_until: dict[str, float] = {}
        self._indicator_last_error: dict[str, str] = {}
        self._row_error_logged_at: dict[str, float] = {}
        self._global_rest_error_logged_at = 0.0
        self._runtime_symbols = list(settings.symbols)
        self._symbol_validation_done = False
        self._engine_cycle_lock = threading.Lock()
        self._profile_ema_spans: dict[str, tuple[int, int]] = {
            "WEAK": (
                int(settings.auto_trade_profile_weak_ema_fast),
                int(settings.auto_trade_profile_weak_ema_slow),
            ),
            "MIDDLE": (
                int(settings.auto_trade_profile_middle_ema_fast),
                int(settings.auto_trade_profile_middle_ema_slow),
            ),
            "STRONG": (
                int(settings.auto_trade_profile_strong_ema_fast),
                int(settings.auto_trade_profile_strong_ema_slow),
            ),
        }
        self._extra_ema_spans: tuple[int, ...] = tuple(
            sorted(
                {
                    span
                    for fast, slow in self._profile_ema_spans.values()
                    for span in (fast, slow)
                    if span not in {20, 50}
                }
            )
        )

    @staticmethod
    def utc_day_key() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def get_symbol_indicators(self, symbol: str) -> dict[str, float]:
        return self.get_symbol_indicators_by_timeframe(symbol, self.settings.signal_timeframe)

    def apply_live_price_to_latest_candle(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        live_price = self.market_state.get_last_price(symbol)
        if live_price is None or len(df.index) == 0:
            return df

        latest_index = df.index[-1]

        df.at[latest_index, "close"] = live_price

        high_value = safe_float(df.at[latest_index, "high"])
        low_value = safe_float(df.at[latest_index, "low"])
        if high_value is not None:
            df.at[latest_index, "high"] = max(high_value, live_price)
        if low_value is not None:
            df.at[latest_index, "low"] = min(low_value, live_price)

        return df

    def get_symbol_indicators_by_timeframe(
        self,
        symbol: str,
        timeframe: str,
    ) -> dict[str, float]:
        now = time.time()
        cache_key = f"{symbol}:{timeframe}"
        weak_fast_span, weak_slow_span = self._profile_ema_spans["WEAK"]
        middle_fast_span, middle_slow_span = self._profile_ema_spans["MIDDLE"]
        strong_fast_span, strong_slow_span = self._profile_ema_spans["STRONG"]

        def cached_float(source: dict[str, Any], key: str, fallback: float) -> float:
            value = safe_float(source.get(key))
            return float(value) if value is not None else float(fallback)

        def map_cached(source: dict[str, Any]) -> dict[str, float]:
            ema20 = float(source["ema20"])
            ema50 = float(source["ema50"])
            return {
                "rsi": float(source["rsi"]),
                "ema20": ema20,
                "ema50": ema50,
                "macd": float(source["macd"]),
                "macd_signal": float(source["macd_signal"]),
                "last_close": float(source["last_close"]),
                "atr_pct": float(source.get("atr_pct") or 0.0),
                "volume_ratio": float(source.get("volume_ratio") or 1.0),
                "ema_weak_fast": cached_float(source, "ema_weak_fast", ema20),
                "ema_weak_slow": cached_float(source, "ema_weak_slow", ema50),
                "ema_middle_fast": cached_float(source, "ema_middle_fast", ema20),
                "ema_middle_slow": cached_float(source, "ema_middle_slow", ema50),
                "ema_strong_fast": cached_float(source, "ema_strong_fast", ema20),
                "ema_strong_slow": cached_float(source, "ema_strong_slow", ema50),
                "ema_weak_fast_span": float(weak_fast_span),
                "ema_weak_slow_span": float(weak_slow_span),
                "ema_middle_fast_span": float(middle_fast_span),
                "ema_middle_slow_span": float(middle_slow_span),
                "ema_strong_fast_span": float(strong_fast_span),
                "ema_strong_slow_span": float(strong_slow_span),
            }

        cached = self.state.indicator_cache.get(cache_key)
        if cached and now - cached.get("updated_at", 0.0) < self.settings.indicator_cache_seconds:
            return map_cached(cached)

        backoff_until = self._indicator_backoff_until.get(cache_key, 0.0)
        if backoff_until > now:
            if cached:
                return map_cached(cached)
            raise ValueError(
                self._indicator_last_error.get(cache_key, "Indicator fetch in cooldown")
            )

        try:
            candles = self.exchange.call(
                "fetch_ohlcv",
                symbol,
                timeframe=timeframe,
                limit=self.settings.signal_candle_limit,
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc).strip() or "fetch_ohlcv failed"
            self._indicator_last_error[cache_key] = message[:220]
            self._indicator_backoff_until[cache_key] = now + 60.0

            if cached:
                self.logger.warning(
                    "Using stale indicator cache for %s after fetch failure: %s",
                    cache_key,
                    message[:140],
                )
                return map_cached(cached)
            raise

        if not candles:
            self._indicator_last_error[cache_key] = "No OHLCV candles returned"
            self._indicator_backoff_until[cache_key] = now + 60.0
            raise ValueError("No OHLCV candles returned")

        df = pd.DataFrame(
            candles,
            columns=["time", "open", "high", "low", "close", "volume"],
        )
        df = self.apply_live_price_to_latest_candle(df, symbol)
        df = add_indicators(df, extra_ema_spans=self._extra_ema_spans)

        last = df.iloc[-1]
        if (
            pd.isna(last["rsi"])
            or pd.isna(last["ema20"])
            or pd.isna(last["ema50"])
            or pd.isna(last["macd"])
            or pd.isna(last["macd_signal"])
        ):
            raise ValueError("Indicators not ready")

        payload = {
            "updated_at": now,
            "rsi": float(last["rsi"]),
            "ema20": float(last["ema20"]),
            "ema50": float(last["ema50"]),
            "macd": float(last["macd"]),
            "macd_signal": float(last["macd_signal"]),
            "last_close": float(last["close"]),
            "atr_pct": float(last["atr_pct"]) if pd.notna(last.get("atr_pct")) else 0.0,
            "volume_ratio": (
                float(last["volume_ratio"]) if pd.notna(last.get("volume_ratio")) else 1.0
            ),
            "ema_weak_fast": float(last[self._ema_column(weak_fast_span)]),
            "ema_weak_slow": float(last[self._ema_column(weak_slow_span)]),
            "ema_middle_fast": float(last[self._ema_column(middle_fast_span)]),
            "ema_middle_slow": float(last[self._ema_column(middle_slow_span)]),
            "ema_strong_fast": float(last[self._ema_column(strong_fast_span)]),
            "ema_strong_slow": float(last[self._ema_column(strong_slow_span)]),
        }
        self.state.indicator_cache[cache_key] = payload
        self._indicator_backoff_until.pop(cache_key, None)
        self._indicator_last_error.pop(cache_key, None)

        return map_cached(payload)

    def _effective_symbols(self) -> list[str]:
        if self._symbol_validation_done:
            return self._runtime_symbols

        self._symbol_validation_done = True
        try:
            self.exchange.call("load_markets")
            valid: list[str] = []
            invalid: list[str] = []
            for symbol in self.settings.symbols:
                try:
                    self.exchange.call("market", symbol)
                    valid.append(symbol)
                except Exception:  # noqa: BLE001
                    invalid.append(symbol)

            if valid:
                self._runtime_symbols = valid
            else:
                self._runtime_symbols = [self.settings.default_symbol]

            if invalid:
                self.logger.warning(
                    "Filtered unsupported symbols for %s: %s",
                    self.settings.exchange_name,
                    ", ".join(invalid[:8]),
                )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Could not validate symbols, using configured list: %s", exc)

        return self._runtime_symbols

    @staticmethod
    def _bias_from_score(score: float) -> str:
        if score >= 1.0:
            return "BUY"
        if score <= -1.0:
            return "SELL"
        return "HOLD"

    @staticmethod
    def _ema_column(span: int) -> str:
        return f"ema{max(2, int(span))}"

    def build_mtf_bias_payload(
        self,
        symbol: str,
        timeframes: tuple[str, ...] = ("1m", "5m", "15m"),
    ) -> dict[str, Any]:
        frames: list[dict[str, Any]] = []

        for timeframe in timeframes:
            try:
                indicators = self.get_symbol_indicators_by_timeframe(symbol, timeframe)
                score = float(
                    score_indicator_state(
                        price=indicators["last_close"],
                        rsi=indicators["rsi"],
                        ema20=indicators["ema20"],
                        ema50=indicators["ema50"],
                        macd=indicators["macd"],
                        macd_signal=indicators["macd_signal"],
                    )
                )
                bias = self._bias_from_score(score)
                confidence = min(100, int(round(abs(score) / 5.0 * 100)))
                frames.append(
                    {
                        "timeframe": timeframe,
                        "bias": bias,
                        "score": round(score, 2),
                        "confidence": confidence,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.debug(
                    "MTF bias unavailable for %s timeframe=%s: %s",
                    symbol,
                    timeframe,
                    exc,
                )

        buy_frames = sum(1 for frame in frames if frame.get("bias") == "BUY")
        sell_frames = sum(1 for frame in frames if frame.get("bias") == "SELL")
        hold_frames = sum(1 for frame in frames if frame.get("bias") == "HOLD")

        if not frames:
            alignment = "NO_DATA"
            dominant_bias = "HOLD"
        elif buy_frames == len(frames):
            alignment = "BULLISH"
            dominant_bias = "BUY"
        elif sell_frames == len(frames):
            alignment = "BEARISH"
            dominant_bias = "SELL"
        elif hold_frames == len(frames):
            alignment = "NEUTRAL"
            dominant_bias = "HOLD"
        else:
            alignment = "MIXED"
            if buy_frames > sell_frames and buy_frames > hold_frames:
                dominant_bias = "BUY"
            elif sell_frames > buy_frames and sell_frames > hold_frames:
                dominant_bias = "SELL"
            else:
                dominant_bias = "HOLD"

        return {
            "frames": frames,
            "alignment": alignment,
            "dominant_bias": dominant_bias,
            "buy_frames": buy_frames,
            "sell_frames": sell_frames,
            "hold_frames": hold_frames,
            "total_frames": len(frames),
        }

    @staticmethod
    def add_session_vwap_bands(df: pd.DataFrame) -> pd.DataFrame:
        if len(df.index) == 0:
            return df

        frame = df.copy()
        time_ms = pd.to_numeric(frame["time"], errors="coerce")
        session_time = pd.to_datetime(time_ms, unit="ms", utc=True, errors="coerce")
        session_key = session_time.dt.strftime("%Y-%m-%d").fillna("unknown")

        high = pd.to_numeric(frame["high"], errors="coerce")
        low = pd.to_numeric(frame["low"], errors="coerce")
        close = pd.to_numeric(frame["close"], errors="coerce")
        volume = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0)

        typical_price = (high + low + close) / 3.0
        price_volume = typical_price * volume

        cumulative_pv = price_volume.groupby(session_key).cumsum()
        cumulative_volume = volume.groupby(session_key).cumsum()
        vwap = cumulative_pv / cumulative_volume.replace(0.0, pd.NA)

        deviation = close - vwap
        sample_count = close.groupby(session_key).cumcount().add(1)
        cumulative_var = (
            deviation.pow(2).groupby(session_key).cumsum()
            / sample_count.replace(0, pd.NA)
        )
        std_dev = cumulative_var.pow(0.5)

        frame["vwap_session"] = vwap
        frame["vwap_upper_1"] = vwap + std_dev
        frame["vwap_lower_1"] = vwap - std_dev
        frame["vwap_upper_2"] = vwap + (std_dev * 2.0)
        frame["vwap_lower_2"] = vwap - (std_dev * 2.0)
        return frame

    def build_signal_strength(self, symbol: str) -> dict[str, Any]:
        score_total = 0.0
        frame_count = 0

        for timeframe in self.settings.strength_timeframes:
            try:
                indicators = self.get_symbol_indicators_by_timeframe(symbol, timeframe)
                score_total += score_indicator_state(
                    price=indicators["last_close"],
                    rsi=indicators["rsi"],
                    ema20=indicators["ema20"],
                    ema50=indicators["ema50"],
                    macd=indicators["macd"],
                    macd_signal=indicators["macd_signal"],
                )
                frame_count += 1
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(
                    "Signal strength indicators failed for %s timeframe=%s: %s",
                    symbol,
                    timeframe,
                    exc,
                )

        if frame_count == 0:
            return {
                "label": "HOLD",
                "score": 0.0,
                "confidence": 0,
                "timeframes": 0,
            }

        average_score = score_total / frame_count
        confidence = min(100, int(round(abs(average_score) / 5 * 100)))

        return {
            "label": classify_strength(average_score),
            "score": round(average_score, 2),
            "confidence": confidence,
            "timeframes": frame_count,
        }

    def build_market_rows(self) -> list[dict[str, Any]]:
        effective_symbols = self._effective_symbols()
        tickers = self.market_state.get_tickers(effective_symbols)
        rows: list[dict[str, Any]] = []

        for symbol in effective_symbols:
            try:
                ticker = tickers.get(symbol, {})
                indicators = self.get_symbol_indicators(symbol)

                ticker_price = safe_float(ticker.get("last"))
                price = ticker_price if ticker_price is not None else indicators["last_close"]

                change_24h = safe_float(ticker.get("percentage"))

                volume_24h = safe_float(ticker.get("quoteVolume"))
                if volume_24h is None:
                    base_volume = safe_float(ticker.get("baseVolume")) or 0.0
                    volume_24h = base_volume * price
                orderflow_payload = self.market_state.get_orderflow_payload(symbol)
                orderbook = (
                    (orderflow_payload or {}).get("orderbook", {})
                    if isinstance(orderflow_payload, dict)
                    else {}
                )
                spread = safe_float(orderbook.get("spread"))
                spread_pct = safe_float(orderbook.get("spread_pct"))

                signal = get_signal(
                    price=price,
                    rsi=indicators["rsi"],
                    ema20=indicators["ema20"],
                    ema50=indicators["ema50"],
                )
                strength = self.build_signal_strength(symbol)
                ai_filter = build_ai_filter_state(
                    price=price,
                    rsi=indicators["rsi"],
                    ema20=indicators["ema20"],
                    ema50=indicators["ema50"],
                    macd=indicators["macd"],
                    macd_signal=indicators["macd_signal"],
                    strength_score=float(strength["score"]),
                    change_24h=change_24h,
                )

                rows.append(
                    {
                        "symbol": symbol,
                        "price": round(price, 6),
                        "change_24h": (
                            round(change_24h, 2) if change_24h is not None else None
                        ),
                        "volume_24h": (
                            round(volume_24h, 2) if volume_24h is not None else None
                        ),
                        "rsi": round(indicators["rsi"], 2),
                        "ema20": round(indicators["ema20"], 6),
                        "ema50": round(indicators["ema50"], 6),
                        "ema_weak_fast": round(indicators["ema_weak_fast"], 6),
                        "ema_weak_slow": round(indicators["ema_weak_slow"], 6),
                        "ema_middle_fast": round(indicators["ema_middle_fast"], 6),
                        "ema_middle_slow": round(indicators["ema_middle_slow"], 6),
                        "ema_strong_fast": round(indicators["ema_strong_fast"], 6),
                        "ema_strong_slow": round(indicators["ema_strong_slow"], 6),
                        "ema_weak_fast_span": int(indicators["ema_weak_fast_span"]),
                        "ema_weak_slow_span": int(indicators["ema_weak_slow_span"]),
                        "ema_middle_fast_span": int(indicators["ema_middle_fast_span"]),
                        "ema_middle_slow_span": int(indicators["ema_middle_slow_span"]),
                        "ema_strong_fast_span": int(indicators["ema_strong_fast_span"]),
                        "ema_strong_slow_span": int(indicators["ema_strong_slow_span"]),
                        "macd": round(indicators["macd"], 6),
                        "macd_signal": round(indicators["macd_signal"], 6),
                        "atr_pct": round(float(indicators.get("atr_pct") or 0.0), 3),
                        "spread": round(spread, 6) if spread is not None else None,
                        "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
                        "volume_ratio": round(float(indicators.get("volume_ratio") or 1.0), 3),
                        "signal": signal,
                        "strength": strength["label"],
                        "strength_score": strength["score"],
                        "strength_confidence": strength["confidence"],
                        "strength_timeframes": strength["timeframes"],
                        "ai_score": ai_filter["score"],
                        "ai_confidence": ai_filter["confidence"],
                        "ai_bias": ai_filter["bias"],
                    }
                )
            except Exception as exc:  # noqa: BLE001
                now_ts = time.time()
                error_text = str(exc)
                is_global_rest_issue = (
                    "GET https://" in error_text
                    and (
                        "api.gateio.ws" in error_text
                        or "api.binance.com" in error_text
                    )
                )
                if is_global_rest_issue:
                    if now_ts - self._global_rest_error_logged_at >= 60.0:
                        self._global_rest_error_logged_at = now_ts
                        self.logger.warning(
                            "Market REST unavailable, building degraded rows: %s",
                            error_text[:180],
                        )
                else:
                    logged_at = self._row_error_logged_at.get(symbol, 0.0)
                    if now_ts - logged_at >= 60.0:
                        self._row_error_logged_at[symbol] = now_ts
                        self.logger.warning("Failed to build row for %s: %s", symbol, exc)
                rows.append(
                    {
                        "symbol": symbol,
                        "price": None,
                        "change_24h": None,
                        "volume_24h": None,
                        "rsi": None,
                        "ema20": None,
                        "ema50": None,
                        "ema_weak_fast": None,
                        "ema_weak_slow": None,
                        "ema_middle_fast": None,
                        "ema_middle_slow": None,
                        "ema_strong_fast": None,
                        "ema_strong_slow": None,
                        "ema_weak_fast_span": None,
                        "ema_weak_slow_span": None,
                        "ema_middle_fast_span": None,
                        "ema_middle_slow_span": None,
                        "ema_strong_fast_span": None,
                        "ema_strong_slow_span": None,
                        "macd": None,
                        "macd_signal": None,
                        "atr_pct": None,
                        "spread": None,
                        "spread_pct": None,
                        "volume_ratio": None,
                        "signal": "HOLD",
                        "strength": "HOLD",
                        "strength_score": 0.0,
                        "strength_confidence": 0,
                        "strength_timeframes": 0,
                        "ai_score": 0.0,
                        "ai_confidence": 0,
                        "ai_bias": "HOLD",
                        "error": str(exc),
                    }
                )

        return rows

    def build_top_movers(self, market_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        clean_rows = [row for row in market_rows if not row.get("error")]

        with_change = [
            row for row in clean_rows if safe_float(row.get("change_24h")) is not None
        ]
        with_volume = [
            row for row in clean_rows if safe_float(row.get("volume_24h")) is not None
        ]

        gainers_raw = sorted(
            with_change,
            key=lambda row: safe_float(row.get("change_24h")) or -99999.0,
            reverse=True,
        )
        losers_raw = sorted(
            with_change,
            key=lambda row: safe_float(row.get("change_24h")) or 99999.0,
        )
        volume_raw = sorted(
            with_volume,
            key=lambda row: safe_float(row.get("volume_24h")) or 0.0,
            reverse=True,
        )

        def simplify(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [
                {
                    "symbol": row.get("symbol"),
                    "price": row.get("price"),
                    "change_24h": row.get("change_24h"),
                    "volume_24h": row.get("volume_24h"),
                    "signal": row.get("signal", "HOLD"),
                }
                for row in rows[: self.settings.top_mover_count]
            ]

        return {
            "gainers": simplify(gainers_raw),
            "losers": simplify(losers_raw),
            "volume": simplify(volume_raw),
        }

    def build_orderflow_payload(self, symbol: str) -> dict[str, Any]:
        return self.market_state.get_orderflow_payload(symbol)

    def _build_paper_wallet_payload_locked(
        self,
        now: float,
        *,
        price_lookup: dict[str, float],
        paper_positions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.state.paper_wallet_initialized:
            self.state.paper_wallet_initialized = True
            self.state.paper_wallet_free_usdt = float(self.settings.paper_wallet_start_usdt)
            self.state.paper_wallet_used_usdt = 0.0
            self.state.paper_wallet_realized_pnl_usdt = 0.0

        free_usdt = max(0.0, float(self.state.paper_wallet_free_usdt))
        tracked_assets: list[str] = []
        tracked_asset_set: set[str] = set()
        for symbol in self.settings.auto_trade_symbols:
            base_asset = str(symbol or "").split("/", 1)[0].upper()
            if not base_asset or base_asset == "USDT" or base_asset in tracked_asset_set:
                continue
            tracked_assets.append(base_asset)
            tracked_asset_set.add(base_asset)

        long_amount_by_asset: dict[str, float] = {asset: 0.0 for asset in tracked_assets}
        long_value_by_asset: dict[str, float] = {asset: 0.0 for asset in tracked_assets}
        short_reserved_total = 0.0
        short_equity_total = 0.0

        for raw_symbol, raw_position in paper_positions.items():
            if not isinstance(raw_position, dict):
                continue

            symbol = str(raw_symbol or "").strip()
            if not symbol:
                continue

            side = str(raw_position.get("side") or "LONG").strip().upper()
            amount = max(0.0, safe_float(raw_position.get("amount")) or 0.0)
            if amount <= 0:
                continue

            entry_price = max(0.0, safe_float(raw_position.get("entry_price")) or 0.0)
            market_price = safe_float(price_lookup.get(symbol))
            current_price = max(0.0, market_price if market_price is not None else entry_price)

            if side == "SHORT":
                reserved_notional = max(
                    0.0,
                    safe_float(raw_position.get("notional_usdt")) or (entry_price * amount),
                )
                pnl_usdt = (entry_price - current_price) * amount
                short_reserved_total += reserved_notional
                short_equity_total += max(0.0, reserved_notional + pnl_usdt)
                continue

            base_asset = symbol.split("/", 1)[0].upper()
            if not base_asset:
                continue
            if base_asset not in long_amount_by_asset:
                tracked_assets.append(base_asset)
                long_amount_by_asset[base_asset] = 0.0
                long_value_by_asset[base_asset] = 0.0

            long_amount_by_asset[base_asset] += amount
            long_value_by_asset[base_asset] += current_price * amount

        usdt_total = max(0.0, free_usdt + short_reserved_total)
        usdt_equity_value = max(0.0, free_usdt + short_equity_total)

        assets: list[dict[str, Any]] = [
            {
                "asset": "USDT",
                "free": round(free_usdt, 8),
                "used": round(short_reserved_total, 8),
                "total": round(usdt_total, 8),
                "price_usdt": 1.0,
                "usdt_value": round(usdt_equity_value, 6),
            }
        ]

        long_equity_total = 0.0
        for asset in tracked_assets:
            symbol = f"{asset}/USDT"
            amount = max(0.0, long_amount_by_asset.get(asset) or 0.0)
            market_price = safe_float(price_lookup.get(symbol))
            fallback_value = max(0.0, long_value_by_asset.get(asset) or 0.0)

            if amount > 0 and market_price is not None and market_price > 0:
                value_usdt = amount * market_price
                price_usdt = market_price
            elif amount > 0:
                value_usdt = fallback_value
                price_usdt = (fallback_value / amount) if fallback_value > 0 else None
            else:
                value_usdt = 0.0
                price_usdt = (
                    market_price
                    if market_price is not None and market_price > 0
                    else None
                )

            long_equity_total += value_usdt
            assets.append(
                {
                    "asset": asset,
                    "free": 0.0,
                    "used": round(amount, 8),
                    "total": round(amount, 8),
                    "price_usdt": (round(price_usdt, 8) if price_usdt is not None else None),
                    "usdt_value": round(value_usdt, 6),
                }
            )

        total_usdt = max(0.0, usdt_equity_value + long_equity_total)

        day_key = self.utc_day_key()
        if self.state.wallet_day_key != day_key:
            self.state.wallet_day_key = day_key
            self.state.wallet_day_start_total_usdt = (
                total_usdt if total_usdt > 0 else None
            )
        if self.state.wallet_day_start_total_usdt is None and total_usdt > 0:
            self.state.wallet_day_start_total_usdt = total_usdt

        day_start_total = self.state.wallet_day_start_total_usdt
        daily_pnl_estimate = (
            total_usdt - day_start_total
            if day_start_total is not None
            else None
        )
        daily_pnl_pct = (
            (daily_pnl_estimate / day_start_total * 100)
            if daily_pnl_estimate is not None and day_start_total and day_start_total > 0
            else None
        )

        payload = {
            "enabled": True,
            "connected": True,
            "exchange": f"{self.settings.exchange_name}-paper",
            "updated_at": int(now),
            "asset_count": len(assets),
            "total_usdt_estimate": round(total_usdt, 2),
            "usdt_free": round(free_usdt, 6),
            "usdt_total": round(usdt_total, 6),
            "assets": assets[:25],
            "error": None,
            "daily_pnl_estimate_usdt": (
                round(daily_pnl_estimate, 2)
                if daily_pnl_estimate is not None
                else None
            ),
            "daily_pnl_estimate_pct": (
                round(daily_pnl_pct, 2)
                if daily_pnl_pct is not None
                else None
            ),
            "day_start_total_usdt": (
                round(day_start_total, 2)
                if day_start_total is not None
                else None
            ),
            "pnl_day_key": day_key,
            "pnl_basis": "paper_sim",
        }
        return payload

    def build_wallet_payload(self, market_rows: list[dict[str, Any]]) -> dict[str, Any]:
        now = time.time()
        price_lookup: dict[str, float] = {}
        for row in market_rows:
            symbol = str(row.get("symbol") or "")
            price = safe_float(row.get("price"))
            if symbol and price is not None and price > 0:
                price_lookup[symbol] = price

        paper_positions: dict[str, dict[str, Any]] = {}
        if self.settings.paper_trading and self.settings.paper_wallet_enabled:
            with self.state.auto_trade_lock:
                paper_positions = {
                    str(symbol): dict(position)
                    for symbol, position in self.state.auto_trade_positions.items()
                    if isinstance(position, dict)
                }

        with self.state.wallet_lock:
            cached = self.state.wallet_cache.get("payload")
            cached_updated_at = safe_float(self.state.wallet_cache.get("updated_at")) or 0.0
            if cached and now - cached_updated_at < self.settings.wallet_cache_seconds:
                return cached

            if self.settings.paper_trading and self.settings.paper_wallet_enabled:
                payload = self._build_paper_wallet_payload_locked(
                    now,
                    price_lookup=price_lookup,
                    paper_positions=paper_positions,
                )
                self.state.wallet_cache["updated_at"] = now
                self.state.wallet_cache["payload"] = payload
                return payload

            if not self.settings.api_key or not self.settings.api_secret:
                payload = {
                    "enabled": False,
                    "connected": False,
                    "exchange": self.settings.exchange_name,
                    "updated_at": int(now),
                    "asset_count": 0,
                    "total_usdt_estimate": None,
                    "usdt_free": None,
                    "usdt_total": None,
                    "assets": [],
                    "error": "Wallet unavailable: API key/secret is missing in .env",
                    "daily_pnl_estimate_usdt": None,
                    "daily_pnl_estimate_pct": None,
                    "day_start_total_usdt": None,
                    "pnl_day_key": None,
                    "pnl_basis": "equity_delta",
                }
                self.state.wallet_cache["updated_at"] = now
                self.state.wallet_cache["payload"] = payload
                return payload

            try:
                balance = self.exchange.call("fetch_balance")
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("Failed to fetch wallet balance")
                message = str(exc).strip() or "Unable to fetch wallet balance"
                payload = {
                    "enabled": True,
                    "connected": False,
                    "exchange": self.settings.exchange_name,
                    "updated_at": int(now),
                    "asset_count": 0,
                    "total_usdt_estimate": None,
                    "usdt_free": None,
                    "usdt_total": None,
                    "assets": [],
                    "error": message[:220],
                    "daily_pnl_estimate_usdt": None,
                    "daily_pnl_estimate_pct": None,
                    "day_start_total_usdt": None,
                    "pnl_day_key": None,
                    "pnl_basis": "equity_delta",
                }
                self.state.wallet_cache["updated_at"] = now
                self.state.wallet_cache["payload"] = payload
                return payload

            free_map = balance.get("free", {})
            used_map = balance.get("used", {})
            total_map = balance.get("total", {})
            if not isinstance(free_map, dict):
                free_map = {}
            if not isinstance(used_map, dict):
                used_map = {}
            if not isinstance(total_map, dict):
                total_map = {}

            asset_codes = set(free_map.keys()) | set(used_map.keys()) | set(total_map.keys())
            assets: list[dict[str, Any]] = []
            total_usdt_estimate = 0.0

            for asset_code in asset_codes:
                asset = str(asset_code or "").upper()
                if not asset:
                    continue

                free = safe_float(free_map.get(asset)) or 0.0
                used = safe_float(used_map.get(asset)) or 0.0
                total = safe_float(total_map.get(asset))
                if total is None:
                    total = free + used

                if total <= 0 and free <= 0 and used <= 0:
                    continue

                price_usdt = (
                    1.0
                    if asset == "USDT"
                    else safe_float(price_lookup.get(f"{asset}/USDT"))
                )
                usdt_value = (total * price_usdt) if price_usdt is not None else None
                if usdt_value is not None:
                    total_usdt_estimate += usdt_value

                assets.append(
                    {
                        "asset": asset,
                        "free": round(free, 8),
                        "used": round(used, 8),
                        "total": round(total, 8),
                        "price_usdt": (
                            round(price_usdt, 8) if price_usdt is not None else None
                        ),
                        "usdt_value": (
                            round(usdt_value, 6) if usdt_value is not None else None
                        ),
                    }
                )

            assets.sort(
                key=lambda item: (
                    safe_float(item.get("usdt_value")) is not None,
                    safe_float(item.get("usdt_value")) or 0.0,
                    safe_float(item.get("total")) or 0.0,
                ),
                reverse=True,
            )

            usdt_entry = next((entry for entry in assets if entry.get("asset") == "USDT"), None)
            day_key = self.utc_day_key()
            if self.state.wallet_day_key != day_key:
                self.state.wallet_day_key = day_key
                self.state.wallet_day_start_total_usdt = (
                    total_usdt_estimate if total_usdt_estimate > 0 else None
                )

            if (
                self.state.wallet_day_start_total_usdt is None
                and total_usdt_estimate > 0
            ):
                self.state.wallet_day_start_total_usdt = total_usdt_estimate

            day_start_total = self.state.wallet_day_start_total_usdt
            daily_pnl_estimate = (
                total_usdt_estimate - day_start_total
                if day_start_total is not None
                else None
            )
            daily_pnl_pct = (
                (daily_pnl_estimate / day_start_total * 100)
                if daily_pnl_estimate is not None and day_start_total and day_start_total > 0
                else None
            )

            payload = {
                "enabled": True,
                "connected": True,
                "exchange": self.settings.exchange_name,
                "updated_at": int(now),
                "asset_count": len(assets),
                "total_usdt_estimate": round(total_usdt_estimate, 2),
                "usdt_free": (usdt_entry.get("free") if usdt_entry else 0.0),
                "usdt_total": (usdt_entry.get("total") if usdt_entry else 0.0),
                "assets": assets[:25],
                "error": None,
                "daily_pnl_estimate_usdt": (
                    round(daily_pnl_estimate, 2)
                    if daily_pnl_estimate is not None
                    else None
                ),
                "daily_pnl_estimate_pct": (
                    round(daily_pnl_pct, 2)
                    if daily_pnl_pct is not None
                    else None
                ),
                "day_start_total_usdt": (
                    round(day_start_total, 2)
                    if day_start_total is not None
                    else None
                ),
                "pnl_day_key": day_key,
                "pnl_basis": "equity_delta",
            }
            self.state.wallet_cache["updated_at"] = now
            self.state.wallet_cache["payload"] = payload
            return payload

    def build_chart_payload(self, symbol: str, timeframe: str) -> dict[str, Any]:
        key = f"{symbol}:{timeframe}"
        now = time.time()

        cached = self.state.chart_cache.get(key)
        if cached and now - cached.get("updated_at", 0.0) < self.settings.chart_cache_seconds:
            return cached["payload"]

        candles = self.exchange.call(
            "fetch_ohlcv",
            symbol,
            timeframe=timeframe,
            limit=self.settings.chart_candle_limit,
        )
        if not candles:
            raise ValueError("No chart candles available")

        df = pd.DataFrame(
            candles,
            columns=["time", "open", "high", "low", "close", "volume"],
        )
        df = self.apply_live_price_to_latest_candle(df, symbol)
        df = add_indicators(df, extra_ema_spans=self._extra_ema_spans)
        df = self.add_session_vwap_bands(df)
        mtf_bias = self.build_mtf_bias_payload(symbol)

        candle_rows: list[dict[str, Any]] = []
        ema20_rows: list[dict[str, Any]] = []
        ema50_rows: list[dict[str, Any]] = []
        vwap_session_rows: list[dict[str, Any]] = []
        vwap_upper_1_rows: list[dict[str, Any]] = []
        vwap_lower_1_rows: list[dict[str, Any]] = []
        vwap_upper_2_rows: list[dict[str, Any]] = []
        vwap_lower_2_rows: list[dict[str, Any]] = []
        volume_rows: list[dict[str, Any]] = []
        rsi_rows: list[dict[str, Any]] = []
        macd_rows: list[dict[str, Any]] = []
        macd_signal_rows: list[dict[str, Any]] = []
        macd_histogram_rows: list[dict[str, Any]] = []

        for _, row in df.iterrows():
            ts = int(float(row["time"]) / 1000)
            open_price = float(row["open"])
            close_price = float(row["close"])

            candle_rows.append(
                {
                    "time": ts,
                    "open": open_price,
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": close_price,
                }
            )

            volume_rows.append(
                {
                    "time": ts,
                    "value": float(row["volume"]),
                    "color": (
                        "rgba(16, 185, 129, 0.7)"
                        if close_price >= open_price
                        else "rgba(239, 68, 68, 0.7)"
                    ),
                }
            )

            if pd.notna(row["ema20"]):
                ema20_rows.append({"time": ts, "value": float(row["ema20"])})
            if pd.notna(row["ema50"]):
                ema50_rows.append({"time": ts, "value": float(row["ema50"])})
            if pd.notna(row.get("vwap_session")):
                vwap_session_rows.append({"time": ts, "value": float(row["vwap_session"])})
            if pd.notna(row.get("vwap_upper_1")):
                vwap_upper_1_rows.append({"time": ts, "value": float(row["vwap_upper_1"])})
            if pd.notna(row.get("vwap_lower_1")):
                vwap_lower_1_rows.append({"time": ts, "value": float(row["vwap_lower_1"])})
            if pd.notna(row.get("vwap_upper_2")):
                vwap_upper_2_rows.append({"time": ts, "value": float(row["vwap_upper_2"])})
            if pd.notna(row.get("vwap_lower_2")):
                vwap_lower_2_rows.append({"time": ts, "value": float(row["vwap_lower_2"])})
            if pd.notna(row["rsi"]):
                rsi_rows.append({"time": ts, "value": float(row["rsi"])})
            if pd.notna(row["macd"]):
                macd_rows.append({"time": ts, "value": float(row["macd"])})
            if pd.notna(row["macd_signal"]):
                macd_signal_rows.append({"time": ts, "value": float(row["macd_signal"])})
            if pd.notna(row["macd_histogram"]):
                macd_histogram_rows.append(
                    {
                        "time": ts,
                        "value": float(row["macd_histogram"]),
                        "color": (
                            "rgba(16, 185, 129, 0.9)"
                            if float(row["macd_histogram"]) >= 0
                            else "rgba(239, 68, 68, 0.9)"
                        ),
                    }
                )

        last = df.iloc[-1]
        summary = {
            "symbol": symbol,
            "timeframe": timeframe,
            "price": round(float(last["close"]), 6),
            "rsi": round(float(last["rsi"]), 2) if pd.notna(last["rsi"]) else None,
            "ema20": round(float(last["ema20"]), 6) if pd.notna(last["ema20"]) else None,
            "ema50": round(float(last["ema50"]), 6) if pd.notna(last["ema50"]) else None,
            "vwap_session": (
                round(float(last["vwap_session"]), 6)
                if pd.notna(last.get("vwap_session"))
                else None
            ),
            "macd": round(float(last["macd"]), 6) if pd.notna(last["macd"]) else None,
            "macd_signal": (
                round(float(last["macd_signal"]), 6)
                if pd.notna(last["macd_signal"])
                else None
            ),
        }

        payload = {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": candle_rows,
            "ema20": ema20_rows,
            "ema50": ema50_rows,
            "vwap_session": vwap_session_rows,
            "vwap_upper_1": vwap_upper_1_rows,
            "vwap_lower_1": vwap_lower_1_rows,
            "vwap_upper_2": vwap_upper_2_rows,
            "vwap_lower_2": vwap_lower_2_rows,
            "volume": volume_rows,
            "rsi": rsi_rows,
            "macd": macd_rows,
            "macd_signal": macd_signal_rows,
            "macd_histogram": macd_histogram_rows,
            "mtf_bias": mtf_bias,
            "summary": summary,
        }

        self.state.chart_cache[key] = {"updated_at": now, "payload": payload}
        return payload

    @staticmethod
    def empty_chart_payload(symbol: str, timeframe: str) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "candles": [],
            "ema20": [],
            "ema50": [],
            "vwap_session": [],
            "vwap_upper_1": [],
            "vwap_lower_1": [],
            "vwap_upper_2": [],
            "vwap_lower_2": [],
            "volume": [],
            "rsi": [],
            "macd": [],
            "macd_signal": [],
            "macd_histogram": [],
            "mtf_bias": {
                "frames": [],
                "alignment": "NO_DATA",
                "dominant_bias": "HOLD",
                "buy_frames": 0,
                "sell_frames": 0,
                "hold_frames": 0,
                "total_frames": 0,
            },
            "summary": {
                "symbol": symbol,
                "timeframe": timeframe,
                "price": None,
                "rsi": None,
                "ema20": None,
                "ema50": None,
                "vwap_session": None,
                "macd": None,
                "macd_signal": None,
            },
        }

    def build_snapshot(self, selected_symbol: str, timeframe: str) -> dict[str, Any]:
        with self._engine_cycle_lock:
            market_rows = self.build_market_rows()
            movers = self.build_top_movers(market_rows)
            wallet = self.build_wallet_payload(market_rows)
            self.alerts.update_alert_state(market_rows)
            recent_alerts = self.alerts.get_recent_alerts(self.settings.alert_recent_count)

        symbols_present = [row["symbol"] for row in market_rows if row.get("symbol")]
        if selected_symbol not in symbols_present:
            selected_symbol = symbols_present[0] if symbols_present else self.settings.default_symbol

        try:
            orderflow = self.build_orderflow_payload(selected_symbol)
        except Exception:  # noqa: BLE001
            self.logger.exception("Orderflow payload build failed for %s", selected_symbol)
            orderflow = {
                "symbol": selected_symbol,
                "orderbook": {
                    "bids": [],
                    "asks": [],
                    "spread": None,
                    "spread_pct": None,
                    "mid": None,
                },
                "trades": [],
                "error": "Order flow data unavailable. Retrying...",
            }

        snapshot_error: str | None = None
        try:
            chart = self.build_chart_payload(selected_symbol, timeframe)
        except Exception:  # noqa: BLE001
            self.logger.exception(
                "Chart payload build failed for symbol=%s timeframe=%s",
                selected_symbol,
                timeframe,
            )
            snapshot_error = f"Chart data unavailable for {selected_symbol} ({timeframe}). Retrying..."

            cached_key = f"{selected_symbol}:{timeframe}"
            cached_chart = self.state.chart_cache.get(cached_key, {}).get("payload")
            if isinstance(cached_chart, dict):
                chart = cached_chart
            else:
                chart = self.empty_chart_payload(selected_symbol, timeframe)

        stream_status = self.market_state.get_stream_status()
        if not stream_status.get("connected"):
            stream_error = str(stream_status.get("last_error") or "").strip()
            stream_message = (
                f"Exchange stream reconnecting: {stream_error}"
                if stream_error
                else "Exchange stream is connecting..."
            )
            snapshot_error = (
                f"{snapshot_error} • {stream_message}" if snapshot_error else stream_message
            )

        selected_row = next(
            (row for row in market_rows if row.get("symbol") == selected_symbol),
            None,
        )
        auto_trade = self.trading.build_auto_trade_status(selected_symbol)

        return {
            "type": "snapshot",
            "timestamp": int(time.time()),
            "symbols": self._effective_symbols(),
            "selected_symbol": selected_symbol,
            "timeframe": timeframe,
            "market": market_rows,
            "movers": movers,
            "orderflow": orderflow,
            "summary": selected_row,
            "chart": chart,
            "wallet": wallet,
            "alerts": recent_alerts,
            "auto_trade": auto_trade,
            "error": snapshot_error,
        }

    def run_background_engine_cycle(self) -> None:
        with self._engine_cycle_lock:
            market_rows = self.build_market_rows()
            wallet = self.build_wallet_payload(market_rows)
            self.alerts.update_alert_state(market_rows)
            self.trading.run_auto_trading(market_rows, wallet)
            self.trading.persist_runtime_state()
