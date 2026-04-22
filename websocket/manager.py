from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fastapi import WebSocket, WebSocketDisconnect

SnapshotBuilder = Callable[[str, str], dict[str, Any]]
MarketUpdateWaiter = Callable[[int, float], Awaitable[int | None]]


@dataclass
class ClientSession:
    websocket: WebSocket
    symbol: str
    timeframe: str
    disconnected: asyncio.Event = field(default_factory=asyncio.Event)


class WebSocketManager:
    def __init__(
        self,
        *,
        push_interval_seconds: float,
        symbols: list[str],
        allowed_timeframes: set[str],
        default_symbol: str,
        default_timeframe: str,
        snapshot_builder: SnapshotBuilder,
        market_update_waiter: MarketUpdateWaiter,
        logger: logging.Logger,
    ) -> None:
        self.push_interval_seconds = max(0.2, push_interval_seconds)
        self.symbols = symbols
        self.allowed_timeframes = allowed_timeframes
        self.default_symbol = default_symbol
        self.default_timeframe = default_timeframe
        self.snapshot_builder = snapshot_builder
        self.market_update_waiter = market_update_waiter
        self.logger = logger

        self._clients: dict[int, ClientSession] = {}
        self._clients_lock = asyncio.Lock()
        self._broadcast_task: asyncio.Task[None] | None = None

    async def _build_snapshot_for_view(
        self,
        symbol: str,
        timeframe: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            snapshot = await asyncio.to_thread(
                self.snapshot_builder,
                symbol,
                timeframe,
            )
            return snapshot, None
        except Exception:  # noqa: BLE001
            self.logger.exception(
                "Snapshot generation failed for symbol=%s timeframe=%s",
                symbol,
                timeframe,
            )
            return None, "Live market stream is temporarily unavailable. Retrying..."

    async def start(self) -> None:
        if self._broadcast_task and not self._broadcast_task.done():
            return
        self._broadcast_task = asyncio.create_task(
            self._broadcast_loop(),
            name="frontend-broadcaster",
        )

    async def stop(self) -> None:
        if self._broadcast_task:
            self._broadcast_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._broadcast_task
            self._broadcast_task = None

        async with self._clients_lock:
            sessions = list(self._clients.values())
            self._clients.clear()

        for session in sessions:
            await self._close_session(session)

    async def _safe_send_json(
        self,
        session: ClientSession,
        payload: dict[str, Any],
    ) -> bool:
        if session.disconnected.is_set():
            return False

        try:
            await session.websocket.send_json(payload)
            return True
        except WebSocketDisconnect:
            session.disconnected.set()
            return False
        except RuntimeError as exc:
            self.logger.info("Skip send on closed websocket: %s", exc)
            session.disconnected.set()
            return False
        except Exception:
            session.disconnected.set()
            self.logger.exception("Unexpected websocket send failure")
            return False

    async def _close_session(self, session: ClientSession) -> None:
        session.disconnected.set()
        with suppress(Exception):
            await session.websocket.close()

    async def _remove_session(self, session: ClientSession) -> None:
        session.disconnected.set()
        async with self._clients_lock:
            self._clients.pop(id(session.websocket), None)
        await self._close_session(session)

    async def _receive_client_updates(self, session: ClientSession) -> None:
        websocket = session.websocket

        while not session.disconnected.is_set():
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                session.disconnected.set()
                return
            except Exception:
                if session.disconnected.is_set():
                    return
                continue

            if not isinstance(message, dict):
                continue

            msg_type = str(message.get("type", ""))
            symbol = message.get("symbol")
            timeframe = message.get("timeframe")

            if msg_type in {"set_view", "set_symbol"} and isinstance(symbol, str):
                if symbol in self.symbols:
                    session.symbol = symbol

            if msg_type in {"set_view", "set_timeframe"} and isinstance(timeframe, str):
                if timeframe in self.allowed_timeframes:
                    session.timeframe = timeframe

    async def handle_connection(self, websocket: WebSocket) -> None:
        await websocket.accept()

        session = ClientSession(
            websocket=websocket,
            symbol=self.default_symbol,
            timeframe=self.default_timeframe,
        )

        async with self._clients_lock:
            self._clients[id(websocket)] = session
            client_count = len(self._clients)

        self.logger.info("WebSocket client connected (clients=%s)", client_count)

        try:
            await self._push_snapshot(session)
            await self._receive_client_updates(session)
        finally:
            await self._remove_session(session)
            async with self._clients_lock:
                remaining = len(self._clients)
            self.logger.info("WebSocket client disconnected (clients=%s)", remaining)

    async def _push_snapshot(self, session: ClientSession) -> None:
        try:
            snapshot = await asyncio.to_thread(
                self.snapshot_builder,
                session.symbol,
                session.timeframe,
            )
            session.symbol = str(snapshot.get("selected_symbol") or session.symbol)
        except Exception:  # noqa: BLE001
            self.logger.exception("Snapshot generation failed")
            sent = await self._safe_send_json(
                session,
                {
                    "type": "error",
                    "message": "Live market stream is temporarily unavailable. Retrying...",
                },
            )
            if not sent:
                await self._remove_session(session)
            return

        sent = await self._safe_send_json(session, snapshot)
        if not sent:
            await self._remove_session(session)

    async def _broadcast_loop(self) -> None:
        last_sequence = -1
        last_push_at = 0.0
        min_push_interval = min(0.4, self.push_interval_seconds)

        while True:
            try:
                next_sequence = await self.market_update_waiter(
                    last_sequence,
                    self.push_interval_seconds,
                )
                if next_sequence is not None:
                    last_sequence = next_sequence
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self.logger.exception("Market update wait failed")
                await asyncio.sleep(self.push_interval_seconds)
                continue

            elapsed = time.monotonic() - last_push_at
            if elapsed < min_push_interval:
                await asyncio.sleep(min_push_interval - elapsed)

            async with self._clients_lock:
                sessions = list(self._clients.values())

            if not sessions:
                continue

            sessions_by_view: dict[tuple[str, str], list[ClientSession]] = {}
            for session in sessions:
                if session.disconnected.is_set():
                    continue
                key = (session.symbol, session.timeframe)
                sessions_by_view.setdefault(key, []).append(session)

            for (symbol, timeframe), grouped_sessions in sessions_by_view.items():
                snapshot, error_message = await self._build_snapshot_for_view(symbol, timeframe)
                if snapshot is None:
                    payload = {"type": "error", "message": error_message or "Snapshot failed"}
                    for session in grouped_sessions:
                        sent = await self._safe_send_json(session, payload)
                        if not sent:
                            await self._remove_session(session)
                    continue

                selected_symbol = str(snapshot.get("selected_symbol") or symbol)
                for session in grouped_sessions:
                    session.symbol = selected_symbol
                    sent = await self._safe_send_json(session, snapshot)
                    if not sent:
                        await self._remove_session(session)

            last_push_at = time.monotonic()
