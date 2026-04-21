from __future__ import annotations

import asyncio
import logging
import os
import time
import threading
from collections import deque
from contextlib import suppress
from typing import Any

import ccxt
import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv()

app = FastAPI(title="Nurix Trading Dashboard")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "DOGE/USDT",
    "TRX/USDT",
    "AVAX/USDT",
    "LINK/USDT",
]
LOCKED_AUTO_TRADE_SYMBOLS = [
    "BTC/USDT",
]
LOCKED_AUTO_TRADE_SYMBOLS_SET = set(LOCKED_AUTO_TRADE_SYMBOLS)
ALLOWED_TIMEFRAMES = {"1m", "5m", "15m", "1h", "4h"}
FALLBACK_DEFAULT_TIMEFRAME = "1m"


def parse_env_symbols(raw_value: str | None) -> list[str]:
    if not raw_value:
        return DEFAULT_SYMBOLS.copy()

    symbols: list[str] = []
    seen: set[str] = set()

    for part in raw_value.split(","):
        symbol = part.strip().upper()
        if not symbol or symbol in seen:
            continue
        symbols.append(symbol)
        seen.add(symbol)

    return symbols if symbols else DEFAULT_SYMBOLS.copy()


def parse_default_timeframe(raw_value: str | None) -> str:
    timeframe = (raw_value or FALLBACK_DEFAULT_TIMEFRAME).strip()
    if timeframe in ALLOWED_TIMEFRAMES:
        return timeframe

    logger.warning(
        "Invalid DEFAULT_TIMEFRAME=%s, using fallback=%s",
        timeframe,
        FALLBACK_DEFAULT_TIMEFRAME,
    )
    return FALLBACK_DEFAULT_TIMEFRAME


def parse_env_bool(raw_value: str | None, default: bool) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def parse_env_float(raw_value: str | None, default: float) -> float:
    try:
        if raw_value is None or raw_value.strip() == "":
            return default
        return float(raw_value)
    except (TypeError, ValueError):
        return default


def parse_env_int(raw_value: str | None, default: int) -> int:
    try:
        if raw_value is None or raw_value.strip() == "":
            return default
        return int(raw_value)
    except (TypeError, ValueError):
        return default


SIGNAL_TIMEFRAME = "1m"
SIGNAL_CANDLE_LIMIT = 120
CHART_CANDLE_LIMIT = 220

PUSH_INTERVAL_SECONDS = 2.0
INDICATOR_CACHE_SECONDS = 20
CHART_CACHE_SECONDS = 3
ALERT_MAX_EVENTS = 200
ALERT_RECENT_COUNT = 30
TOP_MOVER_COUNT = 4
ORDERBOOK_DEPTH = 12
TRADE_TAPE_LIMIT = 16
ORDERFLOW_CACHE_SECONDS = 1.5
WALLET_CACHE_SECONDS = 10
STRENGTH_TIMEFRAMES = ("1m", "5m", "15m")
AUTO_TRADE_MAX_EVENTS = 120

EXCHANGE_NAME = (os.getenv("EXCHANGE_NAME", "gateio") or "gateio").strip().lower()
SYMBOLS = parse_env_symbols(os.getenv("SYMBOLS"))
DEFAULT_SYMBOL = SYMBOLS[0]
DEFAULT_TIMEFRAME = parse_default_timeframe(os.getenv("DEFAULT_TIMEFRAME"))
API_KEY = (os.getenv("API_KEY") or "").strip()
API_SECRET = (os.getenv("API_SECRET") or "").strip()
USE_SANDBOX = parse_env_bool(os.getenv("USE_SANDBOX"), False)

AUTO_TRADE_ENABLED = parse_env_bool(os.getenv("AUTO_TRADE_ENABLED"), False)
PAPER_TRADING = parse_env_bool(os.getenv("PAPER_TRADING"), True)
TRADE_SIZE_USDT = max(parse_env_float(os.getenv("TRADE_SIZE_USDT"), 20.0), 5.0)
TRADE_SIZE_PERCENT = min(
    max(parse_env_float(os.getenv("TRADE_SIZE_PERCENT"), 0.0), 0.0),
    100.0,
)
AUTO_TRADE_MIN_NOTIONAL_USDT = max(
    parse_env_float(os.getenv("AUTO_TRADE_MIN_NOTIONAL_USDT"), 3.0),
    1.0,
)
AUTO_TRADE_MIN_BUFFER_PCT = max(
    parse_env_float(os.getenv("AUTO_TRADE_MIN_BUFFER_PCT"), 1.5),
    0.0,
)
STOP_LOSS_PCT = max(parse_env_float(os.getenv("STOP_LOSS_PCT"), 1.0), 0.1)
TAKE_PROFIT_PCT = max(parse_env_float(os.getenv("TAKE_PROFIT_PCT"), 2.0), 0.1)
COOLDOWN_SECONDS = max(parse_env_int(os.getenv("COOLDOWN_SECONDS"), 300), 1)
MAX_DAILY_LOSS_USDT = max(parse_env_float(os.getenv("MAX_DAILY_LOSS_USDT"), 50.0), 1.0)
AUTO_TRADE_MIN_CONFIDENCE = min(
    max(parse_env_int(os.getenv("AUTO_TRADE_MIN_CONFIDENCE"), 55), 0),
    100,
)
requested_auto_trade_symbols = parse_env_symbols(os.getenv("AUTO_TRADE_SYMBOLS")) if (
    os.getenv("AUTO_TRADE_SYMBOLS") or ""
).strip() else SYMBOLS[: min(3, len(SYMBOLS))]
AUTO_TRADE_SYMBOLS = [
    symbol
    for symbol in LOCKED_AUTO_TRADE_SYMBOLS
    if symbol in requested_auto_trade_symbols
]
if not AUTO_TRADE_SYMBOLS:
    AUTO_TRADE_SYMBOLS = LOCKED_AUTO_TRADE_SYMBOLS.copy()
AUTO_TRADE_SYMBOLS_SET = set(AUTO_TRADE_SYMBOLS)

SERVICE_STARTED_AT = int(time.time())

INDICATOR_CACHE: dict[str, dict[str, Any]] = {}
CHART_CACHE: dict[str, dict[str, Any]] = {}
ORDERFLOW_CACHE: dict[str, dict[str, Any]] = {}
WALLET_CACHE: dict[str, Any] = {}
SYMBOL_STATE: dict[str, dict[str, Any]] = {}
ALERT_EVENTS: deque[dict[str, Any]] = deque(maxlen=ALERT_MAX_EVENTS)
ALERT_COUNTER = 0
ALERT_LOCK = threading.Lock()
WALLET_LOCK = threading.Lock()
AUTO_TRADE_LOCK = threading.Lock()
AUTO_TRADE_EVENTS: deque[dict[str, Any]] = deque(maxlen=AUTO_TRADE_MAX_EVENTS)
AUTO_TRADE_COUNTER = 0
AUTO_TRADE_POSITIONS: dict[str, dict[str, Any]] = {}
AUTO_TRADE_LAST_ACTION_TS: dict[str, float] = {}
AUTO_TRADE_DAILY_PNL: dict[str, float] = {}
AUTO_TRADE_LAST_EVAL_AT = 0.0
AUTO_TRADE_HALT_DAY: str | None = None
MARKETS_LOADED = False


def create_exchange(
    name: str,
    api_key: str = "",
    api_secret: str = "",
    use_sandbox: bool = False,
) -> ccxt.Exchange:
    options = {"enableRateLimit": True, "timeout": 30000}
    if api_key and api_secret:
        options["apiKey"] = api_key
        options["secret"] = api_secret

    if name == "binance":
        client = ccxt.binance(options)
    else:
        client = ccxt.gateio(options)

    if use_sandbox:
        with suppress(Exception):
            client.set_sandbox_mode(True)

    return client


exchange = create_exchange(
    EXCHANGE_NAME,
    API_KEY,
    API_SECRET,
    USE_SANDBOX,
)

if AUTO_TRADE_ENABLED and not PAPER_TRADING and (not API_KEY or not API_SECRET):
    logger.warning(
        "AUTO_TRADE_ENABLED is true but API_KEY/API_SECRET are missing. Auto trading disabled.",
    )
    AUTO_TRADE_ENABLED = False


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close = pd.to_numeric(df["close"], errors="coerce")

    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    rs = avg_gain / avg_loss.replace(0, 1e-10)
    df["rsi"] = 100 - (100 / (1 + rs))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_histogram"] = df["macd"] - df["macd_signal"]

    return df


def get_signal(price: float, rsi: float, ema20: float, ema50: float) -> str:
    if price > ema20 > ema50 and rsi < 70:
        return "BUY"
    if price < ema20 < ema50 and rsi > 30:
        return "SELL"
    return "HOLD"


def get_rsi_zone(rsi: float | None) -> str:
    if rsi is None:
        return "unknown"
    if rsi >= 70:
        return "overbought"
    if rsi <= 30:
        return "oversold"
    return "neutral"


def create_alert(
    symbol: str,
    alert_type: str,
    title: str,
    message: str,
    severity: str,
) -> None:
    global ALERT_COUNTER
    ALERT_COUNTER += 1
    ALERT_EVENTS.append(
        {
            "id": ALERT_COUNTER,
            "timestamp": int(time.time()),
            "symbol": symbol,
            "type": alert_type,
            "title": title,
            "message": message,
            "severity": severity,
        }
    )


def update_alert_state(market_rows: list[dict[str, Any]]) -> None:
    with ALERT_LOCK:
        for row in market_rows:
            symbol = row.get("symbol")
            if not symbol:
                continue

            signal = row.get("signal")
            rsi = safe_float(row.get("rsi"))
            rsi_zone = get_rsi_zone(rsi)

            previous = SYMBOL_STATE.get(symbol, {})
            prev_signal = previous.get("signal")
            prev_zone = previous.get("rsi_zone")

            if prev_signal and signal and prev_signal != signal:
                create_alert(
                    symbol=symbol,
                    alert_type="signal_flip",
                    title=f"{symbol} signal changed",
                    message=f"{prev_signal} → {signal}",
                    severity=("high" if signal in {"BUY", "SELL"} else "medium"),
                )

            if prev_zone and rsi_zone != prev_zone:
                if rsi_zone == "overbought":
                    create_alert(
                        symbol=symbol,
                        alert_type="rsi_overbought",
                        title=f"{symbol} RSI overbought",
                        message=f"RSI reached {rsi:.2f}",
                        severity="medium",
                    )
                elif rsi_zone == "oversold":
                    create_alert(
                        symbol=symbol,
                        alert_type="rsi_oversold",
                        title=f"{symbol} RSI oversold",
                        message=f"RSI reached {rsi:.2f}",
                        severity="medium",
                    )

            SYMBOL_STATE[symbol] = {
                "signal": signal,
                "rsi_zone": rsi_zone,
            }


def get_recent_alerts(limit: int = ALERT_RECENT_COUNT) -> list[dict[str, Any]]:
    with ALERT_LOCK:
        if limit <= 0:
            return []
        return list(ALERT_EVENTS)[-limit:]


def fetch_tickers_for_symbols(symbols: list[str]) -> dict[str, dict[str, Any]]:
    try:
        return exchange.fetch_tickers(symbols)
    except Exception:  # noqa: BLE001
        logger.exception("Failed targeted tickers call; fallback to all tickers")

    try:
        all_tickers = exchange.fetch_tickers()
        return {symbol: all_tickers.get(symbol, {}) for symbol in symbols}
    except Exception:  # noqa: BLE001
        logger.exception("Failed fallback all-tickers call")
        return {symbol: {} for symbol in symbols}


def get_symbol_indicators(symbol: str) -> dict[str, float]:
    return get_symbol_indicators_by_timeframe(symbol, SIGNAL_TIMEFRAME)


def get_symbol_indicators_by_timeframe(symbol: str, timeframe: str) -> dict[str, float]:
    now = time.time()
    cache_key = f"{symbol}:{timeframe}"
    cached = INDICATOR_CACHE.get(cache_key)
    if cached and now - cached.get("updated_at", 0.0) < INDICATOR_CACHE_SECONDS:
        return {
            "rsi": float(cached["rsi"]),
            "ema20": float(cached["ema20"]),
            "ema50": float(cached["ema50"]),
            "macd": float(cached["macd"]),
            "macd_signal": float(cached["macd_signal"]),
            "last_close": float(cached["last_close"]),
        }

    candles = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=SIGNAL_CANDLE_LIMIT,
    )
    if not candles:
        raise ValueError("No OHLCV candles returned")

    df = pd.DataFrame(
        candles,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
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
    }
    INDICATOR_CACHE[cache_key] = payload

    return {
        "rsi": payload["rsi"],
        "ema20": payload["ema20"],
        "ema50": payload["ema50"],
        "macd": payload["macd"],
        "macd_signal": payload["macd_signal"],
        "last_close": payload["last_close"],
    }


def score_indicator_state(
    price: float,
    rsi: float,
    ema20: float,
    ema50: float,
    macd: float,
    macd_signal: float,
) -> int:
    score = 0

    score += 1 if price >= ema20 else -1
    score += 1 if ema20 >= ema50 else -1
    score += 1 if macd >= macd_signal else -1

    if rsi >= 55:
        score += 1
    elif rsi <= 45:
        score -= 1

    if rsi > 75:
        score -= 1
    elif rsi < 25:
        score += 1

    return score


def classify_strength(average_score: float) -> str:
    if average_score >= 2.5:
        return "STRONG BUY"
    if average_score >= 1.0:
        return "BUY"
    if average_score <= -2.5:
        return "STRONG SELL"
    if average_score <= -1.0:
        return "SELL"
    return "HOLD"


def build_signal_strength(symbol: str) -> dict[str, Any]:
    score_total = 0.0
    frame_count = 0

    for timeframe in STRENGTH_TIMEFRAMES:
        try:
            indicators = get_symbol_indicators_by_timeframe(symbol, timeframe)
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
            logger.warning(
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


def utc_day_key() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def emit_alert(
    symbol: str,
    alert_type: str,
    title: str,
    message: str,
    severity: str,
) -> None:
    with ALERT_LOCK:
        create_alert(symbol, alert_type, title, message, severity)


def push_auto_trade_event(
    symbol: str,
    event_type: str,
    action: str,
    message: str,
    *,
    price: float | None = None,
    amount: float | None = None,
    pnl_usdt: float | None = None,
    mode: str | None = None,
    success: bool = True,
) -> None:
    global AUTO_TRADE_COUNTER
    AUTO_TRADE_COUNTER += 1
    AUTO_TRADE_EVENTS.append(
        {
            "id": AUTO_TRADE_COUNTER,
            "timestamp": int(time.time()),
            "symbol": symbol,
            "event_type": event_type,
            "action": action,
            "message": message,
            "price": round(price, 6) if price is not None else None,
            "amount": round(amount, 8) if amount is not None else None,
            "pnl_usdt": round(pnl_usdt, 4) if pnl_usdt is not None else None,
            "mode": mode or ("paper" if PAPER_TRADING else "live"),
            "success": success,
        }
    )


def execute_auto_trade_order(
    symbol: str,
    side: str,
    amount: float,
    reference_price: float,
) -> dict[str, Any]:
    if PAPER_TRADING:
        return {
            "ok": True,
            "order_id": f"paper-{int(time.time() * 1000)}",
            "filled": amount,
            "average": reference_price,
            "mode": "paper",
        }

    try:
        trade_amount = amount
        with suppress(Exception):
            trade_amount = float(exchange.amount_to_precision(symbol, amount))
        if trade_amount <= 0:
            return {"ok": False, "error": "Computed amount is zero after precision rounding"}

        try:
            order = exchange.create_order(symbol, "market", side, trade_amount)
        except Exception as exc:  # noqa: BLE001
            if (
                side == "buy"
                and "createMarketBuyOrderRequiresPrice" in str(exc)
                and reference_price > 0
            ):
                order = exchange.create_order(
                    symbol,
                    "market",
                    side,
                    trade_amount,
                    reference_price,
                )
            else:
                raise

        filled = safe_float(order.get("filled")) or trade_amount
        average = safe_float(order.get("average")) or reference_price
        return {
            "ok": True,
            "order_id": str(order.get("id") or f"live-{int(time.time() * 1000)}"),
            "filled": float(filled),
            "average": float(average),
            "mode": "live",
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auto trade order failed for %s side=%s", symbol, side)
        return {"ok": False, "error": str(exc)}


def should_enter_auto_trade(row: dict[str, Any]) -> bool:
    signal = str(row.get("signal") or "HOLD").upper()
    strength = str(row.get("strength") or "HOLD").upper()
    confidence = int(safe_float(row.get("strength_confidence")) or 0)

    return (
        signal == "BUY"
        and strength in {"BUY", "STRONG BUY"}
        and confidence >= AUTO_TRADE_MIN_CONFIDENCE
    )


def get_position_exit_reason(
    row: dict[str, Any],
    entry_price: float,
) -> tuple[str | None, float]:
    current_price = safe_float(row.get("price"))
    if current_price is None or entry_price <= 0:
        return None, 0.0

    pnl_pct = ((current_price - entry_price) / entry_price) * 100
    signal = str(row.get("signal") or "HOLD").upper()
    strength = str(row.get("strength") or "HOLD").upper()

    if pnl_pct <= -STOP_LOSS_PCT:
        return "STOP LOSS", pnl_pct
    if pnl_pct >= TAKE_PROFIT_PCT:
        return "TAKE PROFIT", pnl_pct
    if signal == "SELL" or strength in {"SELL", "STRONG SELL"}:
        return "SIGNAL EXIT", pnl_pct
    return None, pnl_pct


def get_symbol_min_notional_usdt(symbol: str) -> float:
    global MARKETS_LOADED

    try:
        if not MARKETS_LOADED:
            exchange.load_markets()
            MARKETS_LOADED = True

        market = exchange.market(symbol)
        limits = market.get("limits", {}) if isinstance(market, dict) else {}
        cost_limits = limits.get("cost", {}) if isinstance(limits, dict) else {}
        min_cost = safe_float(cost_limits.get("min")) if isinstance(cost_limits, dict) else None
        if min_cost is not None and min_cost > 0:
            return min_cost
    except Exception:  # noqa: BLE001
        logger.warning(
            "Could not determine exchange min notional for %s, fallback to configured minimum",
            symbol,
        )

    return AUTO_TRADE_MIN_NOTIONAL_USDT


def run_auto_trading(
    market_rows: list[dict[str, Any]],
    wallet_payload: dict[str, Any] | None = None,
) -> None:
    global AUTO_TRADE_LAST_EVAL_AT, AUTO_TRADE_HALT_DAY

    if not AUTO_TRADE_ENABLED:
        return

    now = time.time()
    eval_interval = max(1.0, PUSH_INTERVAL_SECONDS * 0.8)

    with AUTO_TRADE_LOCK:
        if now - AUTO_TRADE_LAST_EVAL_AT < eval_interval:
            return
        AUTO_TRADE_LAST_EVAL_AT = now

        available_usdt = safe_float((wallet_payload or {}).get("usdt_free"))

        day_key = utc_day_key()
        if day_key not in AUTO_TRADE_DAILY_PNL:
            AUTO_TRADE_DAILY_PNL[day_key] = 0.0
        if AUTO_TRADE_HALT_DAY and AUTO_TRADE_HALT_DAY != day_key:
            AUTO_TRADE_HALT_DAY = None

        rows_by_symbol = {
            str(row.get("symbol")): row
            for row in market_rows
            if row.get("symbol")
        }

        for symbol, position in list(AUTO_TRADE_POSITIONS.items()):
            row = rows_by_symbol.get(symbol)
            if not row:
                continue

            entry_price = safe_float(position.get("entry_price")) or 0.0
            amount = safe_float(position.get("amount")) or 0.0
            current_price = safe_float(row.get("price"))
            if amount <= 0 or current_price is None:
                continue

            reason, _ = get_position_exit_reason(row, entry_price)
            if not reason:
                continue

            order_result = execute_auto_trade_order(symbol, "sell", amount, current_price)
            if not order_result.get("ok"):
                error_message = str(order_result.get("error") or "Unknown sell error")
                push_auto_trade_event(
                    symbol,
                    "EXIT_FAILED",
                    "SELL",
                    error_message,
                    price=current_price,
                    amount=amount,
                    success=False,
                )
                emit_alert(
                    symbol=symbol,
                    alert_type="auto_trade_error",
                    title=f"{symbol} auto-trade SELL failed",
                    message=error_message[:140],
                    severity="high",
                )
                continue

            filled_amount = safe_float(order_result.get("filled")) or amount
            pnl_usdt = (current_price - entry_price) * filled_amount
            AUTO_TRADE_DAILY_PNL[day_key] += pnl_usdt
            AUTO_TRADE_LAST_ACTION_TS[symbol] = now
            AUTO_TRADE_POSITIONS.pop(symbol, None)

            push_auto_trade_event(
                symbol,
                "EXIT",
                "SELL",
                f"{reason} • PnL {pnl_usdt:.2f} USDT",
                price=current_price,
                amount=filled_amount,
                pnl_usdt=pnl_usdt,
                mode=str(order_result.get("mode") or "live"),
            )
            emit_alert(
                symbol=symbol,
                alert_type="auto_trade_sell",
                title=f"{symbol} auto SELL executed",
                message=f"{reason} • PnL {pnl_usdt:.2f} USDT",
                severity=("medium" if pnl_usdt >= 0 else "high"),
            )

        daily_pnl = AUTO_TRADE_DAILY_PNL.get(day_key, 0.0)
        risk_halted = daily_pnl <= -MAX_DAILY_LOSS_USDT

        if risk_halted:
            if AUTO_TRADE_HALT_DAY != day_key:
                AUTO_TRADE_HALT_DAY = day_key
                emit_alert(
                    symbol=DEFAULT_SYMBOL,
                    alert_type="auto_trade_halt",
                    title="Auto-trade halted by daily risk limit",
                    message=(
                        f"Daily PnL reached {daily_pnl:.2f} USDT "
                        f"(limit -{MAX_DAILY_LOSS_USDT:.2f})"
                    ),
                    severity="high",
                )
            return

        AUTO_TRADE_HALT_DAY = None

        for row in market_rows:
            symbol = str(row.get("symbol") or "")
            if not symbol or symbol not in AUTO_TRADE_SYMBOLS_SET:
                continue
            if row.get("error"):
                continue
            if symbol in AUTO_TRADE_POSITIONS:
                continue

            last_action_ts = AUTO_TRADE_LAST_ACTION_TS.get(symbol, 0.0)
            if now - last_action_ts < COOLDOWN_SECONDS:
                continue

            current_price = safe_float(row.get("price"))
            if current_price is None or current_price <= 0:
                continue
            if not should_enter_auto_trade(row):
                continue

            notional_usdt = TRADE_SIZE_USDT
            if TRADE_SIZE_PERCENT > 0 and available_usdt is not None and available_usdt > 0:
                sized_by_percent = available_usdt * (TRADE_SIZE_PERCENT / 100)
                notional_usdt = min(sized_by_percent, available_usdt)

            min_notional_usdt = get_symbol_min_notional_usdt(symbol)
            required_min_notional = min_notional_usdt * (1 + AUTO_TRADE_MIN_BUFFER_PCT / 100)
            notional_usdt = max(notional_usdt, required_min_notional)
            if available_usdt is not None:
                notional_usdt = min(notional_usdt, available_usdt)

            if notional_usdt <= 0 or notional_usdt < required_min_notional:
                continue

            amount = notional_usdt / current_price
            order_result = execute_auto_trade_order(symbol, "buy", amount, current_price)
            if not order_result.get("ok"):
                error_message = str(order_result.get("error") or "Unknown buy error")
                push_auto_trade_event(
                    symbol,
                    "ENTRY_FAILED",
                    "BUY",
                    error_message,
                    price=current_price,
                    amount=amount,
                    success=False,
                )
                emit_alert(
                    symbol=symbol,
                    alert_type="auto_trade_error",
                    title=f"{symbol} auto-trade BUY failed",
                    message=error_message[:140],
                    severity="high",
                )
                continue

            filled_amount = safe_float(order_result.get("filled")) or amount
            entry_price = safe_float(order_result.get("average")) or current_price
            AUTO_TRADE_POSITIONS[symbol] = {
                "entry_price": entry_price,
                "amount": filled_amount,
                "notional_usdt": notional_usdt,
                "opened_at": int(now),
                "mode": str(order_result.get("mode") or "live"),
            }
            AUTO_TRADE_LAST_ACTION_TS[symbol] = now
            if available_usdt is not None:
                available_usdt = max(0.0, available_usdt - notional_usdt)

            push_auto_trade_event(
                symbol,
                "ENTRY",
                "BUY",
                f"Auto BUY with strength {row.get('strength')} ({row.get('strength_confidence')}%)",
                price=entry_price,
                amount=filled_amount,
                mode=str(order_result.get("mode") or "live"),
            )
            emit_alert(
                symbol=symbol,
                alert_type="auto_trade_buy",
                title=f"{symbol} auto BUY executed",
                message=f"Entry {entry_price:.6f} • size {filled_amount:.6f}",
                severity="medium",
            )


def build_auto_trade_status(selected_symbol: str) -> dict[str, Any]:
    with AUTO_TRADE_LOCK:
        day_key = utc_day_key()
        daily_pnl = AUTO_TRADE_DAILY_PNL.get(day_key, 0.0)
        position = AUTO_TRADE_POSITIONS.get(selected_symbol)

        return {
            "enabled": AUTO_TRADE_ENABLED,
            "paper_trading": PAPER_TRADING,
            "exchange": EXCHANGE_NAME,
            "symbols": AUTO_TRADE_SYMBOLS,
            "trade_size_usdt": TRADE_SIZE_USDT,
            "trade_size_percent": round(TRADE_SIZE_PERCENT, 4),
            "min_notional_usdt": round(AUTO_TRADE_MIN_NOTIONAL_USDT, 4),
            "min_buffer_pct": round(AUTO_TRADE_MIN_BUFFER_PCT, 4),
            "min_confidence": AUTO_TRADE_MIN_CONFIDENCE,
            "cooldown_seconds": COOLDOWN_SECONDS,
            "daily_pnl_usdt": round(daily_pnl, 4),
            "daily_loss_limit_usdt": round(MAX_DAILY_LOSS_USDT, 4),
            "halted": (AUTO_TRADE_HALT_DAY == day_key),
            "open_positions": len(AUTO_TRADE_POSITIONS),
            "selected_position": position,
            "recent_events": list(AUTO_TRADE_EVENTS)[-12:],
        }


def build_market_rows() -> list[dict[str, Any]]:
    tickers = fetch_tickers_for_symbols(SYMBOLS)
    rows: list[dict[str, Any]] = []

    for symbol in SYMBOLS:
        try:
            ticker = tickers.get(symbol, {})
            indicators = get_symbol_indicators(symbol)

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
            strength = build_signal_strength(symbol)

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
                    "signal": signal,
                    "strength": strength["label"],
                    "strength_score": strength["score"],
                    "strength_confidence": strength["confidence"],
                    "strength_timeframes": strength["timeframes"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to build row for %s", symbol)
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
                    "signal": "HOLD",
                    "strength": "HOLD",
                    "strength_score": 0.0,
                    "strength_confidence": 0,
                    "strength_timeframes": 0,
                    "error": str(exc),
                }
            )

    return rows


def build_top_movers(market_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    clean_rows = [row for row in market_rows if not row.get("error")]

    with_change = [
        row
        for row in clean_rows
        if safe_float(row.get("change_24h")) is not None
    ]
    with_volume = [
        row
        for row in clean_rows
        if safe_float(row.get("volume_24h")) is not None
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
            for row in rows[:TOP_MOVER_COUNT]
        ]

    return {
        "gainers": simplify(gainers_raw),
        "losers": simplify(losers_raw),
        "volume": simplify(volume_raw),
    }


def format_orderbook_side(levels: list[Any], depth: int) -> list[dict[str, float]]:
    formatted: list[dict[str, float]] = []
    cumulative = 0.0

    for level in levels[:depth]:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue

        price = safe_float(level[0])
        amount = safe_float(level[1])
        if price is None or amount is None:
            continue

        cumulative += amount
        formatted.append(
            {
                "price": round(price, 6),
                "amount": round(amount, 6),
                "total": round(cumulative, 6),
            }
        )

    return formatted


def build_orderflow_payload(symbol: str) -> dict[str, Any]:
    now = time.time()
    cached = ORDERFLOW_CACHE.get(symbol)
    if cached and now - cached.get("updated_at", 0.0) < ORDERFLOW_CACHE_SECONDS:
        return cached["payload"]

    orderbook_payload: dict[str, Any] = {
        "bids": [],
        "asks": [],
        "spread": None,
        "spread_pct": None,
        "mid": None,
    }
    trades_payload: list[dict[str, Any]] = []
    error_parts: list[str] = []

    try:
        orderbook = exchange.fetch_order_book(symbol, limit=ORDERBOOK_DEPTH)
        bids = format_orderbook_side(orderbook.get("bids", []), ORDERBOOK_DEPTH)
        asks = format_orderbook_side(orderbook.get("asks", []), ORDERBOOK_DEPTH)

        best_bid = bids[0]["price"] if bids else None
        best_ask = asks[0]["price"] if asks else None
        spread = None
        spread_pct = None
        mid = None
        if best_bid is not None and best_ask is not None and best_ask >= best_bid:
            spread = best_ask - best_bid
            mid = (best_ask + best_bid) / 2
            spread_pct = (spread / mid * 100) if mid > 0 else None

        orderbook_payload = {
            "bids": bids,
            "asks": asks,
            "spread": round(spread, 6) if spread is not None else None,
            "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
            "mid": round(mid, 6) if mid is not None else None,
        }
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch order book for %s", symbol)
        error_parts.append("Order book unavailable")

    try:
        raw_trades = exchange.fetch_trades(symbol, limit=TRADE_TAPE_LIMIT)
        # Most exchanges return oldest -> newest. We show latest first in tape.
        for trade in reversed(raw_trades[-TRADE_TAPE_LIMIT:]):
            price = safe_float(trade.get("price"))
            amount = safe_float(trade.get("amount"))
            if price is None or amount is None:
                continue

            side = str(trade.get("side") or "").lower()
            if side not in {"buy", "sell"}:
                side = "neutral"

            cost = safe_float(trade.get("cost"))
            if cost is None:
                cost = price * amount

            timestamp = safe_float(trade.get("timestamp"))
            trades_payload.append(
                {
                    "timestamp": int(timestamp) if timestamp is not None else None,
                    "side": side,
                    "price": round(price, 6),
                    "amount": round(amount, 6),
                    "cost": round(cost, 6) if cost is not None else None,
                }
            )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch recent trades for %s", symbol)
        error_parts.append("Recent trades unavailable")

    payload = {
        "symbol": symbol,
        "orderbook": orderbook_payload,
        "trades": trades_payload,
        "error": " • ".join(error_parts) if error_parts else None,
    }

    ORDERFLOW_CACHE[symbol] = {"updated_at": now, "payload": payload}
    return payload


def build_wallet_payload(market_rows: list[dict[str, Any]]) -> dict[str, Any]:
    now = time.time()

    with WALLET_LOCK:
        cached = WALLET_CACHE.get("payload")
        cached_updated_at = safe_float(WALLET_CACHE.get("updated_at")) or 0.0
        if cached and now - cached_updated_at < WALLET_CACHE_SECONDS:
            return cached

        if not API_KEY or not API_SECRET:
            payload = {
                "enabled": False,
                "connected": False,
                "exchange": EXCHANGE_NAME,
                "updated_at": int(now),
                "asset_count": 0,
                "total_usdt_estimate": None,
                "usdt_free": None,
                "usdt_total": None,
                "assets": [],
                "error": "Wallet unavailable: API key/secret is missing in .env",
            }
            WALLET_CACHE["updated_at"] = now
            WALLET_CACHE["payload"] = payload
            return payload

        try:
            balance = exchange.fetch_balance()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to fetch wallet balance")
            message = str(exc).strip() or "Unable to fetch wallet balance"
            payload = {
                "enabled": True,
                "connected": False,
                "exchange": EXCHANGE_NAME,
                "updated_at": int(now),
                "asset_count": 0,
                "total_usdt_estimate": None,
                "usdt_free": None,
                "usdt_total": None,
                "assets": [],
                "error": message[:220],
            }
            WALLET_CACHE["updated_at"] = now
            WALLET_CACHE["payload"] = payload
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

            price_usdt = 1.0 if asset == "USDT" else safe_float(price_lookup.get(f"{asset}/USDT"))
            usdt_value = (total * price_usdt) if price_usdt is not None else None
            if usdt_value is not None:
                total_usdt_estimate += usdt_value

            assets.append(
                {
                    "asset": asset,
                    "free": round(free, 8),
                    "used": round(used, 8),
                    "total": round(total, 8),
                    "price_usdt": (round(price_usdt, 8) if price_usdt is not None else None),
                    "usdt_value": (round(usdt_value, 6) if usdt_value is not None else None),
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
        payload = {
            "enabled": True,
            "connected": True,
            "exchange": EXCHANGE_NAME,
            "updated_at": int(now),
            "asset_count": len(assets),
            "total_usdt_estimate": round(total_usdt_estimate, 2),
            "usdt_free": (usdt_entry.get("free") if usdt_entry else 0.0),
            "usdt_total": (usdt_entry.get("total") if usdt_entry else 0.0),
            "assets": assets[:25],
            "error": None,
        }
        WALLET_CACHE["updated_at"] = now
        WALLET_CACHE["payload"] = payload
        return payload


def build_chart_payload(symbol: str, timeframe: str) -> dict[str, Any]:
    key = f"{symbol}:{timeframe}"
    now = time.time()

    cached = CHART_CACHE.get(key)
    if cached and now - cached.get("updated_at", 0.0) < CHART_CACHE_SECONDS:
        return cached["payload"]

    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=CHART_CANDLE_LIMIT)
    if not candles:
        raise ValueError("No chart candles available")

    df = pd.DataFrame(
        candles,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
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

    CHART_CACHE[key] = {"updated_at": now, "payload": payload}
    return payload


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


def build_snapshot(selected_symbol: str, timeframe: str) -> dict[str, Any]:
    market_rows = build_market_rows()
    movers = build_top_movers(market_rows)
    wallet = build_wallet_payload(market_rows)
    update_alert_state(market_rows)
    run_auto_trading(market_rows, wallet)
    recent_alerts = get_recent_alerts()

    symbols_present = [row["symbol"] for row in market_rows if row.get("symbol")]
    if selected_symbol not in symbols_present:
        selected_symbol = symbols_present[0] if symbols_present else DEFAULT_SYMBOL

    try:
        orderflow = build_orderflow_payload(selected_symbol)
    except Exception:  # noqa: BLE001
        logger.exception("Orderflow payload build failed for %s", selected_symbol)
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
        chart = build_chart_payload(selected_symbol, timeframe)
    except Exception:  # noqa: BLE001
        logger.exception(
            "Chart payload build failed for symbol=%s timeframe=%s",
            selected_symbol,
            timeframe,
        )
        snapshot_error = (
            f"Chart data unavailable for {selected_symbol} ({timeframe}). Retrying..."
        )

        cached_key = f"{selected_symbol}:{timeframe}"
        cached_chart = CHART_CACHE.get(cached_key, {}).get("payload")
        if isinstance(cached_chart, dict):
            chart = cached_chart
        else:
            chart = empty_chart_payload(selected_symbol, timeframe)

    selected_row = next(
        (row for row in market_rows if row.get("symbol") == selected_symbol),
        None,
    )
    auto_trade = build_auto_trade_status(selected_symbol)

    return {
        "type": "snapshot",
        "timestamp": int(time.time()),
        "symbols": SYMBOLS,
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


async def receive_client_updates(websocket: WebSocket, state: dict[str, str]) -> None:
    while True:
        try:
            message = await websocket.receive_json()
        except WebSocketDisconnect:
            raise
        except Exception:  # noqa: BLE001
            continue

        if not isinstance(message, dict):
            continue

        msg_type = str(message.get("type", ""))
        symbol = message.get("symbol")
        timeframe = message.get("timeframe")

        if msg_type in {"set_view", "set_symbol"} and isinstance(symbol, str):
            if symbol in SYMBOLS:
                state["symbol"] = symbol

        if msg_type in {"set_view", "set_timeframe"} and isinstance(timeframe, str):
            if timeframe in ALLOWED_TIMEFRAMES:
                state["timeframe"] = timeframe


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
        "uptime_seconds": now - SERVICE_STARTED_AT,
        "exchange": EXCHANGE_NAME,
        "symbol_count": len(SYMBOLS),
        "default_timeframe": DEFAULT_TIMEFRAME,
        "auto_trade_enabled": AUTO_TRADE_ENABLED,
        "paper_trading": PAPER_TRADING,
        "auto_trade_symbol_count": len(AUTO_TRADE_SYMBOLS),
    }


@app.websocket("/ws")
async def market_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("WebSocket client connected")

    client_state: dict[str, str] = {
        "symbol": DEFAULT_SYMBOL,
        "timeframe": DEFAULT_TIMEFRAME,
    }

    receiver_task = asyncio.create_task(receive_client_updates(websocket, client_state))

    try:
        while True:
            try:
                snapshot = await asyncio.to_thread(
                    build_snapshot,
                    client_state["symbol"],
                    client_state["timeframe"],
                )
                client_state["symbol"] = snapshot["selected_symbol"]
                await websocket.send_json(snapshot)
            except WebSocketDisconnect:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("Snapshot generation failed")
                with suppress(Exception):
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Live market stream is temporarily unavailable. Retrying...",
                        },
                    )
            await asyncio.sleep(PUSH_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as exc:  # noqa: BLE001
        logger.exception("WebSocket stream failed")
        with suppress(Exception):
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "WebSocket stream failed. Please reconnect.",
                }
            )
    finally:
        receiver_task.cancel()
        with suppress(asyncio.CancelledError):
            await receiver_task
