from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Any

from services.config import Settings
from services.exchange import ExchangeGateway
from services.indicators import safe_float
from services.market_state import MarketStateManager


class ExchangeStreamService:
    """Poll stock quotes into the existing realtime dashboard state."""

    def __init__(
        self,
        settings: Settings,
        market_state: MarketStateManager,
        logger: logging.Logger,
        exchange: ExchangeGateway,
    ) -> None:
        self.settings = settings
        self.market_state = market_state
        self.logger = logger
        self.exchange = exchange

        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._poll_interval_seconds = 30.0

    async def start(self) -> None:
        if self._task and not self._task.done():
            return

        self.market_state.attach_event_loop()
        self._stopping.clear()
        self._task = asyncio.create_task(self._run_forever(), name="stock-quote-poller")

    async def stop(self) -> None:
        self._stopping.set()
        if not self._task:
            return

        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_forever(self) -> None:
        while not self._stopping.is_set():
            try:
                success_count, last_error = await asyncio.to_thread(self._poll_quotes)
                if success_count > 0:
                    self.market_state.set_stream_status(
                        connected=True,
                        exchange=self.settings.exchange_name,
                        error=None,
                        reconnect_attempt=0,
                    )
                    self.logger.info(
                        "Stock quote feed refreshed (%s symbols)",
                        success_count,
                    )
                else:
                    self.market_state.set_stream_status(
                        connected=False,
                        exchange=self.settings.exchange_name,
                        error=last_error or "Stock quote feed returned no data",
                        reconnect_attempt=1,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                message = str(exc).strip() or exc.__class__.__name__
                self.market_state.set_stream_status(
                    connected=False,
                    exchange=self.settings.exchange_name,
                    error=message[:220],
                    reconnect_attempt=1,
                )
                self.logger.warning("Stock quote polling failed: %s", message)

            await self._sleep_until_next_poll()

    def _poll_quotes(self) -> tuple[int, str | None]:
        success_count = 0
        last_error: str | None = None

        try:
            tickers = self.exchange.call("fetch_tickers", self.settings.symbols)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc).strip() or exc.__class__.__name__
            tickers = {}

        for symbol in self.settings.symbols:
            ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
            if not isinstance(ticker, dict):
                continue
            last_price = safe_float(ticker.get("last"))
            if last_price is None or last_price <= 0:
                continue

            self.market_state.update_ticker(
                symbol=symbol,
                last=last_price,
                percentage=safe_float(ticker.get("percentage")),
                quote_volume=safe_float(ticker.get("quoteVolume")),
                base_volume=safe_float(ticker.get("baseVolume")),
                timestamp_ms=int(ticker.get("timestamp") or time.time() * 1000),
            )
            success_count += 1

        return success_count, last_error

    async def _sleep_until_next_poll(self) -> None:
        slept = 0.0
        step = 0.5
        while slept < self._poll_interval_seconds and not self._stopping.is_set():
            await asyncio.sleep(step)
            slept += step
