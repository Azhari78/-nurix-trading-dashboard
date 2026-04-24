from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from services.alerts import AlertService
from services.config import load_settings
from services.exchange import ExchangeGateway
from services.exchange_stream import ExchangeStreamService
from services.market import MarketService
from services.market_state import MarketStateManager
from services.state import RuntimeState
from services.trading import TradingService
from websocket.manager import WebSocketManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = load_settings()
state = RuntimeState(
    alert_max_events=settings.alert_max_events,
    auto_trade_max_events=settings.auto_trade_max_events,
)
exchange = ExchangeGateway(settings)
market_state = MarketStateManager(
    symbols=settings.symbols,
    trade_tape_limit=settings.trade_tape_limit,
    orderbook_depth=settings.orderbook_depth,
)
exchange_stream = ExchangeStreamService(settings, market_state, logger)
alerts = AlertService(state, settings=settings, logger=logger)
trading = TradingService(settings, state, exchange, alerts, logger)
market = MarketService(settings, state, exchange, market_state, alerts, trading, logger)
ws_manager = WebSocketManager(
    push_interval_seconds=settings.push_interval_seconds,
    symbols=settings.symbols,
    allowed_timeframes=settings.allowed_timeframes,
    default_symbol=settings.default_symbol,
    default_timeframe=settings.default_timeframe,
    snapshot_builder=market.build_snapshot,
    market_update_waiter=market_state.wait_for_update,
    logger=logger,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    engine_interval_seconds = max(1.0, settings.push_interval_seconds)

    async def background_engine_loop() -> None:
        while True:
            started_at = time.monotonic()
            try:
                await asyncio.to_thread(market.run_background_engine_cycle)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Background auto-trade engine cycle failed")

            elapsed = time.monotonic() - started_at
            await asyncio.sleep(max(0.2, engine_interval_seconds - elapsed))

    await exchange_stream.start()
    await ws_manager.start()
    engine_task = asyncio.create_task(
        background_engine_loop(),
        name="auto-trade-engine",
    )
    logger.info(
        "Background auto-trade engine started (interval=%.2fs)",
        engine_interval_seconds,
    )
    try:
        yield
    finally:
        engine_task.cancel()
        with suppress(asyncio.CancelledError):
            await engine_task
        try:
            await asyncio.to_thread(trading.persist_runtime_state, True)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to persist auto-trade runtime state on shutdown")
        logger.info("Background auto-trade engine stopped")
        await ws_manager.stop()
        await exchange_stream.stop()
        await asyncio.to_thread(exchange.close)


app = FastAPI(title=settings.title, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
def health() -> dict[str, Any]:
    now = int(time.time())
    return {
        "status": "ok",
        "service": "nurix-trading-dashboard",
        "timestamp": now,
        "uptime_seconds": now - settings.service_started_at,
        "exchange": settings.exchange_name,
        "symbol_count": len(settings.symbols),
        "default_timeframe": settings.default_timeframe,
        "auto_trade_enabled": settings.auto_trade_enabled,
        "paper_trading": settings.paper_trading,
        "auto_trade_symbol_count": len(settings.auto_trade_symbols),
        "stream_connected": bool(market_state.get_stream_status().get("connected")),
    }


@app.get("/api/trade-journal.csv")
def trade_journal_csv(limit: int = 1000) -> Response:
    safe_limit = max(1, min(int(limit), 5000))
    with state.auto_trade_lock:
        journal_rows = list(state.auto_trade_journal)[-safe_limit:]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "timestamp_utc",
            "symbol",
            "event_type",
            "side",
            "reason",
            "pnl_usdt",
            "pnl_pct",
            "notional_usdt",
            "price",
            "amount",
            "metadata",
        ]
    )

    for row in reversed(journal_rows):
        ts = int(row.get("timestamp") or 0)
        ts_text = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        metadata = row.get("metadata") or {}
        writer.writerow(
            [
                ts_text,
                str(row.get("symbol") or ""),
                str(row.get("event_type") or ""),
                str(row.get("side") or ""),
                str(row.get("reason") or ""),
                row.get("pnl_usdt"),
                row.get("pnl_pct"),
                row.get("notional_usdt"),
                row.get("price"),
                row.get("amount"),
                json.dumps(metadata, ensure_ascii=True),
            ]
        )

    timestamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    filename = f"trade_journal_{timestamp}.csv"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=output.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


@app.websocket("/ws")
async def market_ws(websocket: WebSocket) -> None:
    await ws_manager.handle_connection(websocket)
