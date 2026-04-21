from __future__ import annotations

import asyncio
import json
import logging
import random
import ssl
import time
from contextlib import suppress
from typing import Any

import certifi
import websockets

from services.config import Settings
from services.indicators import safe_float
from services.market_state import MarketStateManager


class ExchangeStreamService:
    def __init__(
        self,
        settings: Settings,
        market_state: MarketStateManager,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.market_state = market_state
        self.logger = logger

        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

        self._binance_symbol_map = {
            symbol.replace("/", "").upper(): symbol for symbol in settings.symbols
        }
        self._gate_symbol_map = {
            symbol.replace("/", "_").upper(): symbol for symbol in settings.symbols
        }
        self._ssl_context = self._build_ssl_context()

    def _build_ssl_context(self) -> ssl.SSLContext:
        if not self.settings.stream_ssl_verify:
            self.logger.warning(
                "STREAM_SSL_VERIFY=false, certificate verification disabled for exchange websocket",
            )
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return context

        if self.settings.stream_ssl_ca_bundle:
            return ssl.create_default_context(cafile=self.settings.stream_ssl_ca_bundle)

        # Use certifi CA bundle to avoid local trust store issues (common on macOS/venv setups).
        return ssl.create_default_context(cafile=certifi.where())

    @property
    def is_supported(self) -> bool:
        return self.settings.exchange_name in {"binance", "gateio"}

    async def start(self) -> None:
        if self._task and not self._task.done():
            return

        self.market_state.attach_event_loop()
        self._stopping.clear()
        self._task = asyncio.create_task(self._run_forever(), name="exchange-stream")

    async def stop(self) -> None:
        self._stopping.set()
        if not self._task:
            return

        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_forever(self) -> None:
        if not self.is_supported:
            message = (
                f"Exchange '{self.settings.exchange_name}' websocket streaming is not supported"
            )
            self.logger.error(message)
            self.market_state.set_stream_status(
                connected=False,
                exchange=self.settings.exchange_name,
                error=message,
                reconnect_attempt=0,
            )
            return

        reconnect_attempt = 0

        while not self._stopping.is_set():
            try:
                if self.settings.exchange_name == "binance":
                    await self._run_binance_stream()
                else:
                    await self._run_gateio_stream()

                reconnect_attempt = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                reconnect_attempt += 1
                backoff = min(30.0, 1.5 * (2 ** min(reconnect_attempt, 5)))
                delay = backoff + random.uniform(0.0, 0.5)

                message = str(exc).strip() or exc.__class__.__name__
                self.market_state.set_stream_status(
                    connected=False,
                    exchange=self.settings.exchange_name,
                    error=message[:220],
                    reconnect_attempt=reconnect_attempt,
                )
                self.logger.warning(
                    "Exchange websocket disconnected (%s). Reconnecting in %.1fs",
                    message,
                    delay,
                )
                await asyncio.sleep(delay)

    async def _run_binance_stream(self) -> None:
        streams: list[str] = []
        for raw_symbol in self._binance_symbol_map:
            symbol_key = raw_symbol.lower()
            streams.append(f"{symbol_key}@ticker")
            streams.append(f"{symbol_key}@trade")
            streams.append(f"{symbol_key}@depth20@100ms")

        url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"

        async with websockets.connect(
            url,
            ssl=self._ssl_context,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=5,
            max_size=4_000_000,
        ) as ws:
            self.logger.info("Connected to Binance market stream")
            self.market_state.set_stream_status(
                connected=True,
                exchange="binance",
                error=None,
                reconnect_attempt=0,
            )
            await self._consume_stream(ws, self._handle_binance_message)

    async def _run_gateio_stream(self) -> None:
        url = "wss://api.gateio.ws/ws/v4/"

        async with websockets.connect(
            url,
            ssl=self._ssl_context,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=5,
            max_size=4_000_000,
        ) as ws:
            await self._subscribe_gateio(ws)
            self.logger.info("Connected to Gate.io market stream")
            self.market_state.set_stream_status(
                connected=True,
                exchange="gateio",
                error=None,
                reconnect_attempt=0,
            )
            await self._consume_stream(ws, self._handle_gateio_message)

    async def _consume_stream(
        self,
        ws: Any,
        message_handler: Any,
    ) -> None:
        heartbeat_interval = 20.0
        heartbeat_timeout = 10.0
        idle_timeout = 45.0

        last_message_at = time.monotonic()
        next_ping_at = time.monotonic() + heartbeat_interval

        while not self._stopping.is_set():
            now = time.monotonic()
            timeout = min(idle_timeout, max(0.1, next_ping_at - now))

            try:
                raw_message = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                now = time.monotonic()
                if now - last_message_at >= idle_timeout:
                    raise RuntimeError("Exchange websocket idle timeout")

                if now >= next_ping_at:
                    pong_waiter = await ws.ping()
                    await asyncio.wait_for(pong_waiter, timeout=heartbeat_timeout)
                    next_ping_at = time.monotonic() + heartbeat_interval
                continue

            last_message_at = time.monotonic()
            if last_message_at >= next_ping_at:
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=heartbeat_timeout)
                next_ping_at = time.monotonic() + heartbeat_interval

            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8", errors="ignore")

            try:
                payload = json.loads(raw_message)
            except Exception:  # noqa: BLE001
                continue

            await message_handler(payload)

    async def _subscribe_gateio(self, ws: Any) -> None:
        now = int(time.time())
        depth = max(20, self.settings.orderbook_depth)

        for pair in self._gate_symbol_map:
            await ws.send(
                json.dumps(
                    {
                        "time": now,
                        "channel": "spot.tickers",
                        "event": "subscribe",
                        "payload": [pair],
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "time": now,
                        "channel": "spot.trades",
                        "event": "subscribe",
                        "payload": [pair],
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "time": now,
                        "channel": "spot.order_book_update",
                        "event": "subscribe",
                        "payload": [pair, "100ms", str(depth)],
                    }
                )
            )
            await ws.send(
                json.dumps(
                    {
                        "time": now,
                        "channel": "spot.order_book",
                        "event": "subscribe",
                        "payload": [pair, str(depth), "100ms"],
                    }
                )
            )

    @staticmethod
    def _parse_timestamp_ms(value: Any) -> int | None:
        ts = safe_float(value)
        if ts is None:
            return None
        if ts > 1_000_000_000_000:
            return int(ts)
        if ts > 10_000_000_000:
            return int(ts)
        return int(ts * 1000)

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _map_binance_symbol(self, raw_symbol: Any) -> str | None:
        if raw_symbol is None:
            return None
        return self._binance_symbol_map.get(str(raw_symbol).upper())

    def _map_gate_symbol(self, raw_symbol: Any) -> str | None:
        if raw_symbol is None:
            return None
        return self._gate_symbol_map.get(str(raw_symbol).upper())

    async def _handle_binance_message(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return

        data = payload.get("data", payload)
        if not isinstance(data, dict):
            return

        event = str(data.get("e") or "").lower()

        if event == "24hrticker":
            symbol = self._map_binance_symbol(data.get("s"))
            if not symbol:
                return

            self.market_state.update_ticker(
                symbol=symbol,
                last=safe_float(data.get("c")),
                percentage=safe_float(data.get("P")),
                quote_volume=safe_float(data.get("q")),
                base_volume=safe_float(data.get("v")),
                timestamp_ms=self._parse_timestamp_ms(data.get("E")),
            )
            return

        if event == "trade":
            symbol = self._map_binance_symbol(data.get("s"))
            if not symbol:
                return

            side = "sell" if bool(data.get("m")) else "buy"
            self.market_state.update_trade(
                symbol=symbol,
                price=safe_float(data.get("p")),
                amount=safe_float(data.get("q")),
                side=side,
                timestamp_ms=self._parse_timestamp_ms(data.get("T") or data.get("E")),
                cost=None,
            )
            return

        stream_name = str(payload.get("stream") or "")
        stream_symbol = stream_name.split("@", 1)[0] if "@" in stream_name else None

        if "bids" in data and "asks" in data:
            symbol = self._map_binance_symbol(data.get("s") or stream_symbol)
            if not symbol:
                return

            self.market_state.update_orderbook(
                symbol=symbol,
                bids=list(data.get("bids") or []),
                asks=list(data.get("asks") or []),
                timestamp_ms=self._parse_timestamp_ms(data.get("E") or data.get("T")),
            )

    async def _handle_gateio_message(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return

        channel = str(payload.get("channel") or "")
        event = str(payload.get("event") or "")

        if event in {"subscribe", "unsubscribe"}:
            return

        if event == "error" or payload.get("error"):
            self.logger.warning("Gate.io websocket error message: %s", payload)
            return

        result = payload.get("result")

        if channel == "spot.tickers":
            for item in self._as_list(result):
                if not isinstance(item, dict):
                    continue

                symbol = self._map_gate_symbol(item.get("currency_pair") or item.get("s"))
                if not symbol:
                    continue

                self.market_state.update_ticker(
                    symbol=symbol,
                    last=safe_float(item.get("last") or item.get("close")),
                    percentage=safe_float(
                        item.get("change_percentage")
                        or item.get("change_24h")
                        or item.get("percentChange")
                    ),
                    quote_volume=safe_float(item.get("quote_volume") or item.get("quoteVolume")),
                    base_volume=safe_float(item.get("base_volume") or item.get("baseVolume")),
                    timestamp_ms=self._parse_timestamp_ms(
                        item.get("update_time_ms")
                        or item.get("time_ms")
                        or payload.get("time_ms")
                        or payload.get("time")
                    ),
                )
            return

        if channel == "spot.trades":
            for trade in self._as_list(result):
                if not isinstance(trade, dict):
                    continue

                symbol = self._map_gate_symbol(
                    trade.get("currency_pair")
                    or trade.get("s")
                    or trade.get("symbol")
                )
                if not symbol:
                    continue

                self.market_state.update_trade(
                    symbol=symbol,
                    price=safe_float(trade.get("price") or trade.get("p")),
                    amount=safe_float(trade.get("amount") or trade.get("q")),
                    side=str(trade.get("side") or "neutral").lower(),
                    timestamp_ms=self._parse_timestamp_ms(
                        trade.get("create_time_ms")
                        or trade.get("time_ms")
                        or trade.get("t")
                        or trade.get("create_time")
                        or payload.get("time_ms")
                        or payload.get("time")
                    ),
                    cost=safe_float(trade.get("cost")),
                )
            return

        if channel in {"spot.order_book_update", "spot.order_book"}:
            for item in self._as_list(result):
                if not isinstance(item, dict):
                    continue

                symbol = self._map_gate_symbol(
                    item.get("currency_pair")
                    or item.get("s")
                    or item.get("symbol")
                )
                if not symbol:
                    continue

                self.market_state.update_orderbook(
                    symbol=symbol,
                    bids=list(item.get("b") or item.get("bids") or []),
                    asks=list(item.get("a") or item.get("asks") or []),
                    timestamp_ms=self._parse_timestamp_ms(
                        item.get("t")
                        or item.get("timestamp")
                        or item.get("time_ms")
                        or payload.get("time_ms")
                        or payload.get("time")
                    ),
                )
