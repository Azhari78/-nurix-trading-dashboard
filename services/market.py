from __future__ import annotations

import logging
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
        cached = self.state.indicator_cache.get(cache_key)
        if cached and now - cached.get("updated_at", 0.0) < self.settings.indicator_cache_seconds:
            return {
                "rsi": float(cached["rsi"]),
                "ema20": float(cached["ema20"]),
                "ema50": float(cached["ema50"]),
                "macd": float(cached["macd"]),
                "macd_signal": float(cached["macd_signal"]),
                "last_close": float(cached["last_close"]),
                "atr_pct": float(cached.get("atr_pct") or 0.0),
                "volume_ratio": float(cached.get("volume_ratio") or 1.0),
            }

        backoff_until = self._indicator_backoff_until.get(cache_key, 0.0)
        if backoff_until > now:
            if cached:
                return {
                    "rsi": float(cached["rsi"]),
                    "ema20": float(cached["ema20"]),
                    "ema50": float(cached["ema50"]),
                    "macd": float(cached["macd"]),
                    "macd_signal": float(cached["macd_signal"]),
                    "last_close": float(cached["last_close"]),
                    "atr_pct": float(cached.get("atr_pct") or 0.0),
                    "volume_ratio": float(cached.get("volume_ratio") or 1.0),
                }
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
                return {
                    "rsi": float(cached["rsi"]),
                    "ema20": float(cached["ema20"]),
                    "ema50": float(cached["ema50"]),
                    "macd": float(cached["macd"]),
                    "macd_signal": float(cached["macd_signal"]),
                    "last_close": float(cached["last_close"]),
                    "atr_pct": float(cached.get("atr_pct") or 0.0),
                    "volume_ratio": float(cached.get("volume_ratio") or 1.0),
                }
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
        df = add_indicators(df)

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
        }
        self.state.indicator_cache[cache_key] = payload
        self._indicator_backoff_until.pop(cache_key, None)
        self._indicator_last_error.pop(cache_key, None)

        return {
            "rsi": payload["rsi"],
            "ema20": payload["ema20"],
            "ema50": payload["ema50"],
            "macd": payload["macd"],
            "macd_signal": payload["macd_signal"],
            "last_close": payload["last_close"],
            "atr_pct": payload["atr_pct"],
            "volume_ratio": payload["volume_ratio"],
        }

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
                        "macd": round(indicators["macd"], 6),
                        "macd_signal": round(indicators["macd_signal"], 6),
                        "atr_pct": round(float(indicators.get("atr_pct") or 0.0), 3),
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
                        "macd": None,
                        "macd_signal": None,
                        "atr_pct": None,
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

    def _build_paper_wallet_payload_locked(self, now: float) -> dict[str, Any]:
        if not self.state.paper_wallet_initialized:
            self.state.paper_wallet_initialized = True
            self.state.paper_wallet_free_usdt = float(self.settings.paper_wallet_start_usdt)
            self.state.paper_wallet_used_usdt = 0.0
            self.state.paper_wallet_realized_pnl_usdt = 0.0

        free_usdt = max(0.0, float(self.state.paper_wallet_free_usdt))
        used_usdt = max(0.0, float(self.state.paper_wallet_used_usdt))
        total_usdt = max(0.0, free_usdt + used_usdt)

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
            "asset_count": 1,
            "total_usdt_estimate": round(total_usdt, 2),
            "usdt_free": round(free_usdt, 6),
            "usdt_total": round(total_usdt, 6),
            "assets": [
                {
                    "asset": "USDT",
                    "free": round(free_usdt, 8),
                    "used": round(used_usdt, 8),
                    "total": round(total_usdt, 8),
                    "price_usdt": 1.0,
                    "usdt_value": round(total_usdt, 6),
                }
            ],
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

        with self.state.wallet_lock:
            cached = self.state.wallet_cache.get("payload")
            cached_updated_at = safe_float(self.state.wallet_cache.get("updated_at")) or 0.0
            if cached and now - cached_updated_at < self.settings.wallet_cache_seconds:
                return cached

            if self.settings.paper_trading and self.settings.paper_wallet_enabled:
                payload = self._build_paper_wallet_payload_locked(now)
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

            price_lookup: dict[str, float] = {}
            for row in market_rows:
                symbol = str(row.get("symbol") or "")
                price = safe_float(row.get("price"))
                if symbol and price is not None and price > 0:
                    price_lookup[symbol] = price

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
        df = add_indicators(df)

        candle_rows: list[dict[str, Any]] = []
        ema20_rows: list[dict[str, Any]] = []
        ema50_rows: list[dict[str, Any]] = []
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
            "volume": volume_rows,
            "rsi": rsi_rows,
            "macd": macd_rows,
            "macd_signal": macd_signal_rows,
            "macd_histogram": macd_histogram_rows,
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
            "volume": [],
            "rsi": [],
            "macd": [],
            "macd_signal": [],
            "macd_histogram": [],
            "summary": {
                "symbol": symbol,
                "timeframe": timeframe,
                "price": None,
                "rsi": None,
                "ema20": None,
                "ema50": None,
                "macd": None,
                "macd_signal": None,
            },
        }

    def build_snapshot(self, selected_symbol: str, timeframe: str) -> dict[str, Any]:
        market_rows = self.build_market_rows()
        movers = self.build_top_movers(market_rows)
        wallet = self.build_wallet_payload(market_rows)
        self.alerts.update_alert_state(market_rows)
        self.trading.run_auto_trading(market_rows, wallet)
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
