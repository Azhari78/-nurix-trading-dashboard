from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
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
alerts = AlertService(state)
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
    await exchange_stream.start()
    await ws_manager.start()
    try:
        yield
    finally:
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


@app.websocket("/ws")
async def market_ws(websocket: WebSocket) -> None:
    await ws_manager.handle_connection(websocket)
