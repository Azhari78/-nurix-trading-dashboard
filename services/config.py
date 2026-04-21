from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

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
LOCKED_AUTO_TRADE_SYMBOLS = ["BTC/USDT"]
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


@dataclass(frozen=True)
class Settings:
    title: str
    signal_timeframe: str
    signal_candle_limit: int
    chart_candle_limit: int
    push_interval_seconds: float
    indicator_cache_seconds: int
    chart_cache_seconds: int
    alert_max_events: int
    alert_recent_count: int
    top_mover_count: int
    orderbook_depth: int
    trade_tape_limit: int
    orderflow_cache_seconds: float
    wallet_cache_seconds: int
    strength_timeframes: tuple[str, ...]
    auto_trade_max_events: int
    exchange_name: str
    symbols: list[str]
    default_symbol: str
    allowed_timeframes: set[str]
    default_timeframe: str
    stream_ssl_verify: bool
    stream_ssl_ca_bundle: str
    api_key: str
    api_secret: str
    use_sandbox: bool
    auto_trade_enabled: bool
    paper_trading: bool
    trade_size_usdt: float
    trade_size_percent: float
    auto_trade_min_notional_usdt: float
    auto_trade_min_buffer_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    cooldown_seconds: int
    max_daily_loss_usdt: float
    auto_trade_min_confidence: int
    auto_trade_symbols: list[str]
    auto_trade_symbols_set: set[str]
    service_started_at: int


def load_settings() -> Settings:
    symbols = parse_env_symbols(os.getenv("SYMBOLS"))
    default_symbol = symbols[0]
    default_timeframe = parse_default_timeframe(os.getenv("DEFAULT_TIMEFRAME"))

    auto_trade_enabled = parse_env_bool(os.getenv("AUTO_TRADE_ENABLED"), False)
    paper_trading = parse_env_bool(os.getenv("PAPER_TRADING"), True)
    api_key = (os.getenv("API_KEY") or "").strip()
    api_secret = (os.getenv("API_SECRET") or "").strip()

    requested_auto_trade_symbols = (
        parse_env_symbols(os.getenv("AUTO_TRADE_SYMBOLS"))
        if (os.getenv("AUTO_TRADE_SYMBOLS") or "").strip()
        else symbols[: min(3, len(symbols))]
    )
    auto_trade_symbols = [
        symbol
        for symbol in LOCKED_AUTO_TRADE_SYMBOLS
        if symbol in requested_auto_trade_symbols
    ]
    if not auto_trade_symbols:
        auto_trade_symbols = LOCKED_AUTO_TRADE_SYMBOLS.copy()

    if auto_trade_enabled and not paper_trading and (not api_key or not api_secret):
        logger.warning(
            "AUTO_TRADE_ENABLED is true but API_KEY/API_SECRET are missing. Auto trading disabled.",
        )
        auto_trade_enabled = False

    return Settings(
        title="Nurix Trading Dashboard",
        signal_timeframe="1m",
        signal_candle_limit=120,
        chart_candle_limit=220,
        push_interval_seconds=2.0,
        indicator_cache_seconds=20,
        chart_cache_seconds=3,
        alert_max_events=200,
        alert_recent_count=30,
        top_mover_count=4,
        orderbook_depth=12,
        trade_tape_limit=16,
        orderflow_cache_seconds=1.5,
        wallet_cache_seconds=10,
        strength_timeframes=("1m", "5m", "15m"),
        auto_trade_max_events=120,
        exchange_name=(os.getenv("EXCHANGE_NAME", "gateio") or "gateio").strip().lower(),
        symbols=symbols,
        default_symbol=default_symbol,
        allowed_timeframes=ALLOWED_TIMEFRAMES,
        default_timeframe=default_timeframe,
        stream_ssl_verify=parse_env_bool(os.getenv("STREAM_SSL_VERIFY"), True),
        stream_ssl_ca_bundle=(os.getenv("STREAM_SSL_CA_BUNDLE") or "").strip(),
        api_key=api_key,
        api_secret=api_secret,
        use_sandbox=parse_env_bool(os.getenv("USE_SANDBOX"), False),
        auto_trade_enabled=auto_trade_enabled,
        paper_trading=paper_trading,
        trade_size_usdt=max(parse_env_float(os.getenv("TRADE_SIZE_USDT"), 20.0), 1.0),
        trade_size_percent=min(
            max(parse_env_float(os.getenv("TRADE_SIZE_PERCENT"), 0.0), 0.0),
            100.0,
        ),
        auto_trade_min_notional_usdt=max(
            parse_env_float(os.getenv("AUTO_TRADE_MIN_NOTIONAL_USDT"), 3.0),
            1.0,
        ),
        auto_trade_min_buffer_pct=max(
            parse_env_float(os.getenv("AUTO_TRADE_MIN_BUFFER_PCT"), 1.5),
            0.0,
        ),
        stop_loss_pct=max(parse_env_float(os.getenv("STOP_LOSS_PCT"), 1.0), 0.1),
        take_profit_pct=max(parse_env_float(os.getenv("TAKE_PROFIT_PCT"), 2.0), 0.1),
        cooldown_seconds=max(parse_env_int(os.getenv("COOLDOWN_SECONDS"), 300), 1),
        max_daily_loss_usdt=max(
            parse_env_float(os.getenv("MAX_DAILY_LOSS_USDT"), 50.0),
            1.0,
        ),
        auto_trade_min_confidence=min(
            max(parse_env_int(os.getenv("AUTO_TRADE_MIN_CONFIDENCE"), 55), 0),
            100,
        ),
        auto_trade_symbols=auto_trade_symbols,
        auto_trade_symbols_set=set(auto_trade_symbols),
        service_started_at=int(time.time()),
    )
