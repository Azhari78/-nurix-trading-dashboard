from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import certifi

from services.config import Settings, normalize_stock_symbol


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
logger = logging.getLogger(__name__)
TIMEFRAME_INTERVALS = {
    "1m": ("1m", "1d"),
    "5m": ("5m", "5d"),
    "15m": ("15m", "1mo"),
    "1h": ("60m", "3mo"),
    "4h": ("60m", "6mo"),
    "1d": ("1d", "2y"),
}
MOOMOO_MARKET_PREFIXES = {"US", "HK", "SH", "SZ", "SG", "JP", "AU", "MY", "CA"}
MOOMOO_KLTYPE_BY_TIMEFRAME = {
    "1m": "K_1M",
    "5m": "K_5M",
    "15m": "K_15M",
    "1h": "K_60M",
    "4h": "K_60M",
    "1d": "K_DAY",
}
MOOMOO_FAILURE_BACKOFF_SECONDS = 60.0


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class ExchangeGateway:
    """Stock market data gateway.

    The service keeps the historical `exchange.call(...)` interface used by the
    dashboard, but routes it to a stock quote/chart provider instead of a broker
    client.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._ohlcv_cache: dict[tuple[str, str, str, int], tuple[float, list[list[float]]]] = {}
        self._ticker_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._moomoo_module: Any | None = None
        self._moomoo_quote_ctx: Any | None = None
        self._moomoo_backoff_until = 0.0
        self._markets_loaded = False

    def call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            fn = getattr(self, method)
            return fn(*args, **kwargs)

    def close(self) -> None:
        with self._lock:
            self._reset_moomoo_context()
            self._ohlcv_cache.clear()
            self._ticker_cache.clear()

    def load_markets(self) -> dict[str, dict[str, Any]]:
        self._markets_loaded = True
        return {symbol: self.market(symbol) for symbol in self.settings.symbols}

    def market(self, symbol: str) -> dict[str, Any]:
        normalized = normalize_stock_symbol(symbol or "")
        if not normalized or normalized not in self.settings.symbols:
            raise ValueError(f"Unsupported stock symbol: {symbol}")

        return {
            "id": normalized,
            "symbol": normalized,
            "base": normalized,
            "quote": "USD",
            "type": "stock",
            "limits": {
                "amount": {"min": 0.0001},
                "cost": {"min": self.settings.auto_trade_min_notional_usd},
            },
            "precision": {"amount": 4, "price": 4},
        }

    @staticmethod
    def amount_to_precision(_: str, amount: float) -> str:
        return f"{max(0.0, float(amount)):.6f}"

    @staticmethod
    def price_to_precision(_: str, price: float) -> str:
        return f"{max(0.0, float(price)):.4f}"

    def create_order(self, *_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError(
            "Live stock order execution is not implemented. Keep PAPER_TRADING=true "
            "or add a broker adapter before enabling live orders."
        )

    def fetch_balance(self) -> dict[str, Any]:
        raise RuntimeError(
            "Live broker wallet fetch is not implemented. Use paper wallet mode "
            "until a broker adapter is configured."
        )

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        normalized = normalize_stock_symbol(symbol or "")
        if not normalized:
            raise ValueError(f"Invalid stock symbol: {symbol}")

        now = time.time()
        cache_key = f"{self.settings.exchange_name}:{normalized}"
        cached = self._ticker_cache.get(cache_key)
        if cached and now - cached[0] < 20.0:
            return dict(cached[1])

        if self._uses_moomoo():
            ticker = self._with_yahoo_fallback(
                "ticker",
                lambda: self._fetch_moomoo_ticker(normalized),
                lambda: self._fetch_yahoo_ticker(normalized),
            )
        else:
            ticker = self._fetch_yahoo_ticker(normalized)

        self._ticker_cache[cache_key] = (now, ticker)
        return dict(ticker)

    def _fetch_yahoo_ticker(self, normalized: str) -> dict[str, Any]:
        candles = self._fetch_yahoo_ohlcv(normalized, timeframe="1m", limit=390)
        if not candles:
            raise ValueError(f"No quote data available for {normalized}")

        last = candles[-1]
        previous = next(
            (
                row
                for row in reversed(candles[:-1])
                if _safe_float(row[4]) is not None and float(row[4]) > 0
            ),
            None,
        )
        last_price = float(last[4])
        previous_close = float(previous[4]) if previous else last_price
        percentage = (
            ((last_price - previous_close) / previous_close) * 100.0
            if previous_close > 0
            else None
        )
        base_volume = float(last[5]) if len(last) > 5 else 0.0
        quote_volume = base_volume * last_price
        ticker = {
            "symbol": normalized,
            "last": last_price,
            "percentage": percentage,
            "baseVolume": base_volume,
            "quoteVolume": quote_volume,
            "timestamp": int(last[0]),
        }
        return ticker

    def fetch_tickers(self, symbols: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
        selected_symbols = list(symbols or self.settings.symbols)
        if self._uses_moomoo():
            return self._with_yahoo_fallback(
                "tickers",
                lambda: self._fetch_moomoo_tickers(selected_symbols),
                lambda: {
                    symbol: self._fetch_yahoo_ticker(symbol)
                    for symbol in selected_symbols
                },
            )
        return {
            symbol: self.fetch_ticker(symbol)
            for symbol in selected_symbols
        }

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1m",
        limit: int = 120,
    ) -> list[list[float]]:
        normalized = normalize_stock_symbol(symbol or "")
        if not normalized:
            raise ValueError(f"Invalid stock symbol: {symbol}")

        safe_limit = max(1, min(int(limit or 120), 1000))
        cache_key = (self.settings.exchange_name, normalized, timeframe, safe_limit)
        now = time.time()
        cached = self._ohlcv_cache.get(cache_key)
        if cached and now - cached[0] < self._cache_ttl_seconds(timeframe):
            return [row[:] for row in cached[1]]

        if self._uses_moomoo():
            rows = self._with_yahoo_fallback(
                "ohlcv",
                lambda: self._fetch_moomoo_ohlcv(normalized, timeframe, safe_limit),
                lambda: self._fetch_yahoo_ohlcv(normalized, timeframe, safe_limit),
            )
        else:
            rows = self._fetch_yahoo_ohlcv(normalized, timeframe, safe_limit)

        rows = rows[-safe_limit:]
        if not rows:
            raise ValueError(f"No OHLCV data returned for {normalized}")

        self._ohlcv_cache[cache_key] = (now, rows)
        return [row[:] for row in rows]

    def _fetch_yahoo_ohlcv(self, normalized: str, timeframe: str, limit: int) -> list[list[float]]:
        interval, range_value = TIMEFRAME_INTERVALS.get(timeframe, TIMEFRAME_INTERVALS["1m"])
        raw_rows = self._fetch_yahoo_chart(normalized, interval=interval, range_value=range_value)
        rows = self._aggregate_four_hour(raw_rows) if timeframe == "4h" else raw_rows
        return rows[-limit:]

    def _uses_moomoo(self) -> bool:
        return self.settings.exchange_name == "moomoo"

    def _with_yahoo_fallback(
        self,
        operation: str,
        primary: Any,
        fallback: Any,
    ) -> Any:
        if (
            self.settings.moomoo_fallback_to_yahoo
            and self._moomoo_backoff_until > time.time()
        ):
            return fallback()

        try:
            result = primary()
            self._moomoo_backoff_until = 0.0
            return result
        except Exception as exc:  # noqa: BLE001
            self._reset_moomoo_context()
            if not self.settings.moomoo_fallback_to_yahoo:
                raise
            self._moomoo_backoff_until = time.time() + MOOMOO_FAILURE_BACKOFF_SECONDS
            logger.warning(
                "Moomoo %s failed; falling back to Yahoo for this request: %s",
                operation,
                str(exc)[:220],
            )
            return fallback()

    def _reset_moomoo_context(self) -> None:
        ctx = self._moomoo_quote_ctx
        self._moomoo_quote_ctx = None
        if ctx is None:
            return

        try:
            stop_fn = getattr(ctx, "stop", None)
            if callable(stop_fn):
                stop_fn()
        except Exception as exc:  # noqa: BLE001 - best-effort SDK cleanup
            logger.debug("Moomoo quote context stop failed: %s", exc)

        try:
            ctx.close()
        except Exception as exc:  # noqa: BLE001 - best-effort SDK cleanup
            logger.debug("Moomoo quote context close failed: %s", exc)

    def _load_moomoo_module(self) -> Any:
        if self._moomoo_module is not None:
            return self._moomoo_module
        original_home = os.environ.get("HOME")
        log_home = self.settings.moomoo_log_home
        try:
            if log_home:
                os.makedirs(log_home, exist_ok=True)
                os.environ["HOME"] = os.path.abspath(log_home)
            import moomoo  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "moomoo-api is not installed. Run `pip install moomoo-api` "
                "or keep MOOMOO_FALLBACK_TO_YAHOO=true."
            ) from exc
        finally:
            if log_home:
                if original_home is None:
                    os.environ.pop("HOME", None)
                else:
                    os.environ["HOME"] = original_home
        self._moomoo_module = moomoo
        return moomoo

    def _moomoo_quote_context(self) -> Any:
        if self._moomoo_quote_ctx is None:
            self._ensure_moomoo_opend_reachable()
            module = self._load_moomoo_module()
            self._moomoo_quote_ctx = module.OpenQuoteContext(
                host=self.settings.moomoo_opend_host,
                port=self.settings.moomoo_opend_port,
            )
        return self._moomoo_quote_ctx

    def _ensure_moomoo_opend_reachable(self) -> None:
        try:
            with socket.create_connection(
                (self.settings.moomoo_opend_host, self.settings.moomoo_opend_port),
                timeout=0.5,
            ):
                return
        except OSError as exc:
            raise ConnectionError(
                "Moomoo OpenD is not reachable at "
                f"{self.settings.moomoo_opend_host}:{self.settings.moomoo_opend_port}"
            ) from exc

    def _moomoo_ret_ok(self) -> Any:
        module = self._load_moomoo_module()
        return getattr(module, "RET_OK", 0)

    def _moomoo_code(self, symbol: str) -> str:
        normalized = normalize_stock_symbol(symbol or "")
        if not normalized:
            raise ValueError(f"Invalid stock symbol: {symbol}")

        prefix, separator, _ = normalized.partition(".")
        if separator and prefix in MOOMOO_MARKET_PREFIXES:
            return normalized
        return f"{self.settings.moomoo_market_prefix}.{normalized}"

    def _display_symbol_from_moomoo_code(self, code: str, fallback: str) -> str:
        prefix = f"{self.settings.moomoo_market_prefix}."
        if code.startswith(prefix):
            return code[len(prefix):]
        return fallback

    def _moomoo_timezone(self, code: str) -> ZoneInfo:
        prefix = code.split(".", 1)[0].upper()
        if prefix == "US":
            return ZoneInfo("America/New_York")
        if prefix == "JP":
            return ZoneInfo("Asia/Tokyo")
        if prefix == "AU":
            return ZoneInfo("Australia/Sydney")
        if prefix == "CA":
            return ZoneInfo("America/Toronto")
        if prefix == "SG":
            return ZoneInfo("Asia/Singapore")
        if prefix == "MY":
            return ZoneInfo("Asia/Kuala_Lumpur")
        return ZoneInfo("Asia/Shanghai")

    def _parse_moomoo_time_ms(self, value: Any, code: str) -> int:
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                return int(timestamp)
            return int(timestamp * 1000)

        raw = str(value or "").strip()
        if not raw:
            return int(time.time() * 1000)

        parsed: datetime | None = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(raw)
            except ValueError:
                return int(time.time() * 1000)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=self._moomoo_timezone(code))
        return int(parsed.timestamp() * 1000)

    def _moomoo_dataframe_records(self, data: Any) -> list[dict[str, Any]]:
        if hasattr(data, "to_dict"):
            records = data.to_dict("records")
            return [dict(row) for row in records]
        if isinstance(data, list):
            return [dict(row) for row in data if isinstance(row, dict)]
        return []

    def _fetch_moomoo_snapshots(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        if not symbols:
            return {}
        ctx = self._moomoo_quote_context()
        code_by_symbol = {symbol: self._moomoo_code(symbol) for symbol in symbols}
        ret, data = ctx.get_market_snapshot(list(code_by_symbol.values()))
        if ret != self._moomoo_ret_ok():
            raise RuntimeError(f"Moomoo snapshot failed: {data}")

        snapshots: dict[str, dict[str, Any]] = {}
        for row in self._moomoo_dataframe_records(data):
            code = str(row.get("code") or "").upper()
            if code:
                snapshots[code] = row
        return snapshots

    def _fetch_moomoo_ticker(self, normalized: str) -> dict[str, Any]:
        return self._fetch_moomoo_tickers([normalized])[normalized]

    def _fetch_moomoo_tickers(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        snapshots = self._fetch_moomoo_snapshots(symbols)
        result: dict[str, dict[str, Any]] = {}

        for symbol in symbols:
            code = self._moomoo_code(symbol)
            row = snapshots.get(code)
            if not row:
                raise ValueError(f"No Moomoo snapshot returned for {code}")

            last_price = _safe_float(row.get("last_price"))
            if last_price is None or last_price <= 0:
                raise ValueError(f"Invalid Moomoo last price for {code}")

            prev_close = _safe_float(row.get("prev_close_price"))
            percentage = _safe_float(row.get("change_rate"))
            if percentage is None and prev_close and prev_close > 0:
                percentage = ((last_price - prev_close) / prev_close) * 100.0

            volume = _safe_float(row.get("volume")) or 0.0
            turnover = _safe_float(row.get("turnover"))
            timestamp_ms = self._parse_moomoo_time_ms(
                row.get("update_timestamp") or row.get("update_time"),
                code,
            )

            result[symbol] = {
                "symbol": self._display_symbol_from_moomoo_code(code, symbol),
                "last": last_price,
                "percentage": percentage,
                "baseVolume": volume,
                "quoteVolume": turnover if turnover is not None else volume * last_price,
                "timestamp": timestamp_ms,
            }

        return result

    def _moomoo_ktype(self, timeframe: str) -> Any:
        module = self._load_moomoo_module()
        ktype_name = MOOMOO_KLTYPE_BY_TIMEFRAME.get(timeframe, "K_1M")
        return getattr(module.KLType, ktype_name)

    def _moomoo_autype(self) -> Any:
        module = self._load_moomoo_module()
        return getattr(module.AuType, "QFQ")

    def _moomoo_history_window(self, timeframe: str) -> tuple[str, str]:
        end_date = date.today()
        if timeframe == "1d":
            days = self.settings.moomoo_history_days_daily
        else:
            days = self.settings.moomoo_history_days_intraday
        start_date = end_date - timedelta(days=days)
        return start_date.isoformat(), end_date.isoformat()

    def _fetch_moomoo_ohlcv(
        self,
        normalized: str,
        timeframe: str,
        limit: int,
    ) -> list[list[float]]:
        code = self._moomoo_code(normalized)
        request_limit = min(1000, limit * 4 if timeframe == "4h" else limit)
        start, end = self._moomoo_history_window(timeframe)
        ctx = self._moomoo_quote_context()
        ret, data, _page_req_key = ctx.request_history_kline(
            code,
            start=start,
            end=end,
            ktype=self._moomoo_ktype(timeframe),
            autype=self._moomoo_autype(),
            max_count=request_limit,
            extended_time=self.settings.moomoo_extended_time,
        )
        if ret != self._moomoo_ret_ok():
            raise RuntimeError(f"Moomoo history failed for {code}: {data}")

        rows: list[list[float]] = []
        for item in self._moomoo_dataframe_records(data):
            open_price = _safe_float(item.get("open") or item.get("open_price"))
            high_price = _safe_float(item.get("high") or item.get("high_price"))
            low_price = _safe_float(item.get("low") or item.get("low_price"))
            close_price = _safe_float(item.get("close") or item.get("close_price"))
            volume = _safe_float(item.get("volume")) or 0.0
            if (
                open_price is None
                or high_price is None
                or low_price is None
                or close_price is None
            ):
                continue

            rows.append(
                [
                    self._parse_moomoo_time_ms(
                        item.get("timestamp") or item.get("time_key") or item.get("time"),
                        code,
                    ),
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    volume,
                ]
            )

        rows.sort(key=lambda row: row[0])
        if timeframe == "4h":
            rows = self._aggregate_four_hour(rows)
        return rows[-limit:]

    @staticmethod
    def _cache_ttl_seconds(timeframe: str) -> float:
        if timeframe == "1m":
            return 20.0
        if timeframe in {"5m", "15m"}:
            return 60.0
        if timeframe in {"1h", "4h"}:
            return 180.0
        return 900.0

    def _fetch_yahoo_chart(
        self,
        symbol: str,
        *,
        interval: str,
        range_value: str,
    ) -> list[list[float]]:
        encoded_symbol = urllib.parse.quote(symbol, safe="")
        query = urllib.parse.urlencode(
            {
                "interval": interval,
                "range": range_value,
                "includePrePost": "false",
                "events": "history",
            }
        )
        url = f"{YAHOO_CHART_URL.format(symbol=encoded_symbol)}?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) StockBot/1.0"
                ),
                "Accept": "application/json",
            },
        )

        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=12, context=context) as response:
            payload = json.loads(response.read().decode("utf-8"))

        chart = payload.get("chart") if isinstance(payload, dict) else None
        error = (chart or {}).get("error") if isinstance(chart, dict) else None
        if error:
            raise RuntimeError(str(error.get("description") or error))

        result = (chart or {}).get("result") if isinstance(chart, dict) else None
        if not result:
            raise ValueError(f"Yahoo returned no chart result for {symbol}")

        data = result[0]
        timestamps = data.get("timestamp") or []
        quotes = ((data.get("indicators") or {}).get("quote") or [{}])[0]

        opens = quotes.get("open") or []
        highs = quotes.get("high") or []
        lows = quotes.get("low") or []
        closes = quotes.get("close") or []
        volumes = quotes.get("volume") or []

        rows: list[list[float]] = []
        for index, timestamp in enumerate(timestamps):
            open_price = _safe_float(opens[index] if index < len(opens) else None)
            high_price = _safe_float(highs[index] if index < len(highs) else None)
            low_price = _safe_float(lows[index] if index < len(lows) else None)
            close_price = _safe_float(closes[index] if index < len(closes) else None)
            volume = _safe_float(volumes[index] if index < len(volumes) else None) or 0.0
            if (
                open_price is None
                or high_price is None
                or low_price is None
                or close_price is None
            ):
                continue

            rows.append(
                [
                    float(timestamp) * 1000.0,
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    volume,
                ]
            )

        return rows

    @staticmethod
    def _aggregate_four_hour(rows: list[list[float]]) -> list[list[float]]:
        aggregated: list[list[float]] = []
        for index in range(0, len(rows), 4):
            chunk = rows[index:index + 4]
            if not chunk:
                continue
            aggregated.append(
                [
                    chunk[0][0],
                    chunk[0][1],
                    max(row[2] for row in chunk),
                    min(row[3] for row in chunk),
                    chunk[-1][4],
                    sum(row[5] for row in chunk),
                ]
            )
        return aggregated
