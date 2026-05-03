from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from typing import Any

from services.indicators import safe_float


class MarketStateManager:
    def __init__(
        self,
        *,
        symbols: list[str],
        trade_tape_limit: int,
        orderbook_depth: int,
    ) -> None:
        self.symbols = symbols
        self.trade_tape_limit = max(1, trade_tape_limit)
        self.orderbook_depth = max(1, orderbook_depth)

        self._symbol_set = set(symbols)
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sequence = 0
        self._update_event = asyncio.Event()

        self._tickers: dict[str, dict[str, Any]] = {
            symbol: {
                "last": None,
                "percentage": None,
                "quoteVolume": None,
                "baseVolume": None,
                "updated_at": None,
            }
            for symbol in symbols
        }
        self._orderbooks: dict[str, dict[str, Any]] = {
            symbol: {"bids": [], "asks": [], "updated_at": None} for symbol in symbols
        }
        self._trades: dict[str, deque[dict[str, Any]]] = {
            symbol: deque(maxlen=self.trade_tape_limit) for symbol in symbols
        }

        self._stream_status: dict[str, Any] = {
            "connected": False,
            "exchange": None,
            "last_error": None,
            "reconnect_attempt": 0,
            "last_event_at": None,
        }

    def attach_event_loop(self) -> None:
        self._loop = asyncio.get_running_loop()

    def _mark_updated(self) -> None:
        with self._lock:
            self._sequence += 1
            event = self._update_event
            self._update_event = asyncio.Event()
            loop = self._loop

        if loop is not None:
            loop.call_soon_threadsafe(event.set)

    @staticmethod
    def _normalize_side(levels: list[Any], *, reverse: bool) -> list[tuple[float, float]]:
        parsed: list[tuple[float, float]] = []
        for level in levels:
            if not isinstance(level, (list, tuple)) or len(level) < 2:
                continue
            price = safe_float(level[0])
            amount = safe_float(level[1])
            if price is None or amount is None or price <= 0 or amount <= 0:
                continue
            parsed.append((price, amount))

        parsed.sort(key=lambda item: item[0], reverse=reverse)
        return parsed

    @staticmethod
    def _format_levels(levels: list[tuple[float, float]], depth: int) -> list[dict[str, float]]:
        formatted: list[dict[str, float]] = []
        cumulative = 0.0

        for price, amount in levels[:depth]:
            cumulative += amount
            formatted.append(
                {
                    "price": round(price, 6),
                    "amount": round(amount, 6),
                    "total": round(cumulative, 6),
                }
            )

        return formatted

    def set_stream_status(
        self,
        *,
        connected: bool,
        exchange: str,
        error: str | None = None,
        reconnect_attempt: int | None = None,
    ) -> None:
        with self._lock:
            changed = (
                self._stream_status.get("connected") != connected
                or self._stream_status.get("exchange") != exchange
                or self._stream_status.get("last_error") != error
                or (
                    reconnect_attempt is not None
                    and self._stream_status.get("reconnect_attempt") != reconnect_attempt
                )
            )

            self._stream_status["connected"] = connected
            self._stream_status["exchange"] = exchange
            self._stream_status["last_error"] = error
            if reconnect_attempt is not None:
                self._stream_status["reconnect_attempt"] = reconnect_attempt
            if connected:
                self._stream_status["last_event_at"] = int(time.time())

        if changed:
            self._mark_updated()

    def update_ticker(
        self,
        *,
        symbol: str,
        last: float | None,
        percentage: float | None,
        quote_volume: float | None,
        base_volume: float | None,
        timestamp_ms: int | None,
    ) -> None:
        if symbol not in self._symbol_set:
            return

        with self._lock:
            ticker = self._tickers[symbol]
            changed = False

            if last is not None and ticker.get("last") != last:
                ticker["last"] = last
                changed = True
            if percentage is not None and ticker.get("percentage") != percentage:
                ticker["percentage"] = percentage
                changed = True
            if quote_volume is not None and ticker.get("quoteVolume") != quote_volume:
                ticker["quoteVolume"] = quote_volume
                changed = True
            if base_volume is not None and ticker.get("baseVolume") != base_volume:
                ticker["baseVolume"] = base_volume
                changed = True

            ticker["updated_at"] = timestamp_ms or int(time.time() * 1000)
            self._stream_status["last_event_at"] = int(time.time())

        if changed:
            self._mark_updated()

    def update_trade(
        self,
        *,
        symbol: str,
        price: float | None,
        amount: float | None,
        side: str | None,
        timestamp_ms: int | None,
        cost: float | None,
    ) -> None:
        if symbol not in self._symbol_set:
            return

        if price is None or amount is None or price <= 0 or amount <= 0:
            return

        normalized_side = str(side or "neutral").lower()
        if normalized_side not in {"buy", "sell"}:
            normalized_side = "neutral"

        trade_cost = cost if cost is not None else price * amount

        with self._lock:
            self._trades[symbol].appendleft(
                {
                    "timestamp": timestamp_ms or int(time.time() * 1000),
                    "side": normalized_side,
                    "price": round(price, 6),
                    "amount": round(amount, 6),
                    "cost": round(trade_cost, 6),
                }
            )

            ticker = self._tickers[symbol]
            ticker["last"] = price
            ticker["updated_at"] = timestamp_ms or int(time.time() * 1000)
            self._stream_status["last_event_at"] = int(time.time())

        self._mark_updated()

    def update_orderbook(
        self,
        *,
        symbol: str,
        bids: list[Any],
        asks: list[Any],
        timestamp_ms: int | None,
    ) -> None:
        if symbol not in self._symbol_set:
            return

        parsed_bids = self._normalize_side(bids, reverse=True)
        parsed_asks = self._normalize_side(asks, reverse=False)

        with self._lock:
            self._orderbooks[symbol] = {
                "bids": parsed_bids,
                "asks": parsed_asks,
                "updated_at": timestamp_ms or int(time.time() * 1000),
            }
            self._stream_status["last_event_at"] = int(time.time())

        self._mark_updated()

    def get_tickers(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                symbol: dict(self._tickers.get(symbol, {}))
                for symbol in symbols
            }

    def get_last_price(self, symbol: str) -> float | None:
        with self._lock:
            return safe_float(self._tickers.get(symbol, {}).get("last"))

    def get_orderflow_payload(self, symbol: str) -> dict[str, Any]:
        with self._lock:
            orderbook = self._orderbooks.get(symbol, {"bids": [], "asks": []})
            raw_bids = orderbook.get("bids", [])
            raw_asks = orderbook.get("asks", [])
            bids = self._format_levels(raw_bids, self.orderbook_depth)
            asks = self._format_levels(raw_asks, self.orderbook_depth)

            best_bid = bids[0]["price"] if bids else None
            best_ask = asks[0]["price"] if asks else None
            spread = None
            spread_pct = None
            mid = None
            if best_bid is not None and best_ask is not None and best_ask >= best_bid:
                spread = best_ask - best_bid
                mid = (best_ask + best_bid) / 2
                spread_pct = (spread / mid * 100) if mid > 0 else None

            trades = list(self._trades.get(symbol, deque()))
            connected = bool(self._stream_status.get("connected"))
            last_error = self._stream_status.get("last_error")

        top_bids = raw_bids[: self.orderbook_depth]
        top_asks = raw_asks[: self.orderbook_depth]
        bid_depth_amount = sum(amount for _, amount in top_bids)
        ask_depth_amount = sum(amount for _, amount in top_asks)
        bid_depth_notional = sum(price * amount for price, amount in top_bids)
        ask_depth_notional = sum(price * amount for price, amount in top_asks)
        depth_notional_total = bid_depth_notional + ask_depth_notional
        depth_amount_total = bid_depth_amount + ask_depth_amount
        depth_imbalance_pct = (
            (bid_depth_notional - ask_depth_notional) / depth_notional_total * 100.0
            if depth_notional_total > 0
            else None
        )
        depth_amount_imbalance_pct = (
            (bid_depth_amount - ask_depth_amount) / depth_amount_total * 100.0
            if depth_amount_total > 0
            else None
        )

        buy_notional = sum(
            (safe_float(trade.get("cost")) or 0.0)
            for trade in trades
            if str(trade.get("side") or "").lower() == "buy"
        )
        sell_notional = sum(
            (safe_float(trade.get("cost")) or 0.0)
            for trade in trades
            if str(trade.get("side") or "").lower() == "sell"
        )
        trade_notional_total = buy_notional + sell_notional
        trade_imbalance_pct = (
            (buy_notional - sell_notional) / trade_notional_total * 100.0
            if trade_notional_total > 0
            else None
        )

        pressure_inputs = [
            value
            for value in (depth_imbalance_pct, trade_imbalance_pct)
            if value is not None
        ]
        pressure_score = sum(pressure_inputs) / len(pressure_inputs) if pressure_inputs else 0.0
        if pressure_score >= 10:
            pressure_bias = "BUY"
        elif pressure_score <= -10:
            pressure_bias = "SELL"
        else:
            pressure_bias = "NEUTRAL"

        spread_penalty = max(0.0, spread_pct or 0.0) * 10.0
        liquidity_score = max(
            0.0,
            min(100.0, (depth_notional_total / 10000.0) - spread_penalty),
        )

        if not bids and not asks and not trades:
            if connected:
                error = "Waiting for exchange stream data..."
            elif last_error:
                error = f"Exchange stream reconnecting: {last_error}"
            else:
                error = "Exchange stream is not connected yet. Retrying..."
        else:
            error = None

        return {
            "symbol": symbol,
            "orderbook": {
                "bids": bids,
                "asks": asks,
                "spread": round(spread, 6) if spread is not None else None,
                "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
                "mid": round(mid, 6) if mid is not None else None,
            },
            "microstructure": {
                "bid_depth_amount": round(bid_depth_amount, 6),
                "ask_depth_amount": round(ask_depth_amount, 6),
                "bid_depth_notional": round(bid_depth_notional, 4),
                "ask_depth_notional": round(ask_depth_notional, 4),
                "depth_imbalance_pct": (
                    round(depth_imbalance_pct, 2)
                    if depth_imbalance_pct is not None
                    else None
                ),
                "depth_amount_imbalance_pct": (
                    round(depth_amount_imbalance_pct, 2)
                    if depth_amount_imbalance_pct is not None
                    else None
                ),
                "buy_trade_notional": round(buy_notional, 4),
                "sell_trade_notional": round(sell_notional, 4),
                "trade_imbalance_pct": (
                    round(trade_imbalance_pct, 2)
                    if trade_imbalance_pct is not None
                    else None
                ),
                "pressure_score": round(pressure_score, 2),
                "pressure_bias": pressure_bias,
                "liquidity_score": round(liquidity_score, 2),
            },
            "trades": trades,
            "error": error,
        }

    def get_stream_status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._stream_status)

    def current_sequence(self) -> int:
        with self._lock:
            return self._sequence

    async def wait_for_update(self, last_sequence: int, timeout_seconds: float) -> int | None:
        with self._lock:
            if self._sequence > last_sequence:
                return self._sequence
            event = self._update_event

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return None

        with self._lock:
            return self._sequence
