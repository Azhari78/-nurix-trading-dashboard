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
    "DOT/USDT",
    "POL/USDT",
    "LTC/USDT",
    "BCH/USDT",
    "XLM/USDT",
    "ATOM/USDT",
    "XMR/USDT",
    "ETC/USDT",
    "UNI/USDT",
    "FIL/USDT",
    "HBAR/USDT",
    "NEAR/USDT",
    "ICP/USDT",
    "VET/USDT",
    "CRO/USDT",
    "ALGO/USDT",
    "SUI/USDT",
    "INJ/USDT",
    "APT/USDT",
    "OP/USDT",
    "ARB/USDT",
    "RNDR/USDT",
    "IMX/USDT",
    "STX/USDT",
    "MKR/USDT",
    "AAVE/USDT",
    "THETA/USDT",
    "EOS/USDT",
    "EGLD/USDT",
    "MANA/USDT",
    "SAND/USDT",
    "FTM/USDT",
    "TIA/USDT",
    "PEPE/USDT",
    "SHIB/USDT",
    "BONK/USDT",
    "GRT/USDT",
    "LDO/USDT",
    "FLOW/USDT",
    "SEI/USDT",
]
ALLOWED_TIMEFRAMES = {"1m", "5m", "15m", "1h", "4h", "1d"}
FALLBACK_DEFAULT_TIMEFRAME = "1m"
ALLOWED_AUTO_TRADE_MODES = {"long_only", "short_only", "both"}
FALLBACK_AUTO_TRADE_MODE = "long_only"


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


def parse_copy_trade_followers(raw_value: str | None) -> tuple[tuple[str, float], ...]:
    if not raw_value:
        return ()

    followers: list[tuple[str, float]] = []
    seen: set[str] = set()

    for raw_part in raw_value.split(","):
        part = raw_part.strip()
        if not part:
            continue

        if ":" in part:
            name_raw, mult_raw = part.split(":", 1)
        else:
            name_raw, mult_raw = part, "1.0"

        name = name_raw.strip()
        if not name:
            continue

        key = name.lower()
        if key in seen:
            continue

        try:
            multiplier = float(mult_raw.strip())
        except (TypeError, ValueError):
            multiplier = 1.0

        multiplier = max(0.05, min(multiplier, 5.0))
        followers.append((name, multiplier))
        seen.add(key)

    return tuple(followers)


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


def parse_auto_trade_mode(raw_value: str | None) -> str:
    mode = (raw_value or FALLBACK_AUTO_TRADE_MODE).strip().lower()
    if mode in ALLOWED_AUTO_TRADE_MODES:
        return mode

    logger.warning(
        "Invalid AUTO_TRADE_STRATEGY_MODE=%s, using fallback=%s",
        mode,
        FALLBACK_AUTO_TRADE_MODE,
    )
    return FALLBACK_AUTO_TRADE_MODE


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


def parse_ema_pair(
    fast_raw: str | None,
    slow_raw: str | None,
    default_fast: int,
    default_slow: int,
) -> tuple[int, int]:
    fast = max(parse_env_int(fast_raw, default_fast), 2)
    slow = max(parse_env_int(slow_raw, default_slow), 2)
    if slow <= fast:
        slow = fast + 1
    return fast, slow


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
    telegram_enabled: bool
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_auto_trade_only: bool
    auto_trade_enabled: bool
    paper_trading: bool
    paper_wallet_enabled: bool
    paper_wallet_start_usdt: float
    trade_size_usdt: float
    trade_size_percent: float
    auto_trade_min_notional_usdt: float
    auto_trade_min_buffer_pct: float
    auto_trade_strategy_mode: str
    auto_trade_enable_short: bool
    auto_trade_max_open_positions: int
    auto_trade_auto_convert_to_usdt: bool
    auto_trade_auto_convert_min_usdt: float
    auto_trade_auto_convert_interval_seconds: int
    auto_trade_symbol_validation_enabled: bool
    trade_size_usdt_min: float
    trade_size_usdt_max: float
    auto_trade_volatility_sizing_enabled: bool
    auto_trade_target_atr_pct: float
    auto_trade_volatility_size_min_mult: float
    auto_trade_volatility_size_max_mult: float
    auto_trade_kelly_enabled: bool
    auto_trade_kelly_fraction: float
    auto_trade_kelly_max_fraction_pct: float
    auto_trade_kelly_min_trades: int
    auto_trade_correlation_risk_enabled: bool
    auto_trade_max_correlation: float
    auto_trade_correlation_lookback: int
    auto_trade_correlation_risk_mult: float
    auto_trade_max_drawdown_enabled: bool
    auto_trade_max_drawdown_pct: float
    auto_trade_dynamic_exit_enabled: bool
    auto_trade_dynamic_exit_min_mult: float
    auto_trade_dynamic_exit_max_mult: float
    auto_trade_extreme_volatility_exit_enabled: bool
    auto_trade_extreme_volatility_exit_atr_pct: float
    auto_trade_extreme_volatility_exit_change_pct: float
    auto_trade_profit_lock_enabled: bool
    auto_trade_profit_lock_trigger_usdt: float
    auto_trade_profit_lock_giveback_pct: float
    auto_trade_profit_lock_pause_seconds: int
    auto_trade_profit_lock_risk_mult: float
    auto_trade_confidence_sizing_enabled: bool
    auto_trade_confidence_size_min_mult: float
    auto_trade_confidence_size_max_mult: float
    auto_trade_regime_sizing_enabled: bool
    auto_trade_regime_risk_on_mult: float
    auto_trade_regime_risk_off_mult: float
    auto_trade_compounding_enabled: bool
    auto_trade_compounding_profit_step_usdt: float
    auto_trade_compounding_max_mult: float
    auto_trade_entry_quality_enabled: bool
    auto_trade_min_entry_score: int
    auto_trade_min_entry_probability: int
    auto_trade_min_risk_reward: float
    auto_trade_min_liquidity_score: float
    auto_trade_max_correlated_positions: int
    auto_trade_circuit_breaker_enabled: bool
    auto_trade_circuit_breaker_volatility_symbols: int
    auto_trade_max_daily_loss_pct: float
    long_rsi_min: float
    long_rsi_max: float
    short_rsi_min: float
    short_rsi_max: float
    auto_trade_entry_confirm_ema_stack: bool
    auto_trade_entry_confirm_macd: bool
    auto_trade_entry_require_macd_data: bool
    auto_trade_profile_weak_ema_fast: int
    auto_trade_profile_weak_ema_slow: int
    auto_trade_profile_middle_ema_fast: int
    auto_trade_profile_middle_ema_slow: int
    auto_trade_profile_strong_ema_fast: int
    auto_trade_profile_strong_ema_slow: int
    auto_trade_min_volume_ratio: float
    auto_trade_min_strength_confidence: int
    auto_trade_session_filter_enabled: bool
    auto_trade_session_utc: str
    auto_trade_extreme_volatility_block_enabled: bool
    auto_trade_max_atr_pct: float
    auto_trade_max_abs_change_24h_pct: float
    auto_trade_execution_cost_gate_enabled: bool
    auto_trade_max_spread_pct: float
    auto_trade_estimated_fee_pct: float
    auto_trade_estimated_slippage_pct: float
    auto_trade_min_edge_pct: float
    auto_trade_symbol_rank_enabled: bool
    auto_trade_symbol_rank_top_n: int
    long_stop_loss_pct: float
    long_take_profit_pct: float
    long_trailing_pct: float
    short_stop_loss_pct: float
    short_take_profit_pct: float
    short_trailing_pct: float
    auto_trade_take_profit_run_enabled: bool
    auto_trade_take_profit_run_min_pnl_pct: float
    auto_trade_partial_take_profit_enabled: bool
    auto_trade_partial_take_profit_pct: float
    auto_trade_partial_take_profit_ratio: float
    auto_trade_break_even_enabled: bool
    auto_trade_break_even_trigger_pct: float
    auto_trade_break_even_buffer_pct: float
    auto_trade_time_stop_enabled: bool
    auto_trade_time_stop_minutes: int
    auto_trade_time_stop_min_pnl_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    cooldown_min_seconds: int
    cooldown_max_seconds: int
    cooldown_seconds: int
    auto_trade_cooldown_adaptive_enabled: bool
    auto_trade_cooldown_loss_step_mult: float
    auto_trade_cooldown_volatility_target_pct: float
    auto_trade_cooldown_volatility_max_mult: float
    auto_trade_max_consecutive_losses: int
    auto_trade_kill_switch_pause_seconds: int
    max_daily_loss_usdt: float
    auto_trade_min_confidence: int
    ai_filter_enabled: bool
    ai_filter_min_confidence: int
    ai_filter_min_score_abs: float
    advanced_ai_enabled: bool
    advanced_ai_quantum_enabled: bool
    sentiment_enabled: bool
    sentiment_lookback_minutes: int
    market_regime_enabled: bool
    market_regime_clusters: int
    auto_trade_self_learning_enabled: bool
    auto_trade_self_learning_min_trades: int
    auto_trade_self_learning_risk_min_mult: float
    auto_trade_self_learning_risk_max_mult: float
    auto_trade_lstm_learning_enabled: bool
    auto_trade_lstm_learning_rate: float
    auto_trade_auto_adapt_enabled: bool
    auto_trade_auto_adapt_low_atr_pct: float
    auto_trade_auto_adapt_high_atr_pct: float
    auto_trade_auto_adapt_conf_step: int
    auto_trade_auto_adapt_risk_on_mult: float
    auto_trade_auto_adapt_risk_off_mult: float
    auto_trade_auto_adapt_cooldown_on_mult: float
    auto_trade_auto_adapt_cooldown_off_mult: float
    auto_trade_forward_guardrail_enabled: bool
    auto_trade_forward_guardrail_min_trades: int
    auto_trade_forward_baseline_win_rate: float
    auto_trade_forward_baseline_avg_pnl_usdt: float
    auto_trade_forward_guardrail_risk_mult: float
    auto_trade_forward_guardrail_severe_win_rate: float
    auto_trade_forward_guardrail_halt_enabled: bool
    copy_trade_enabled: bool
    copy_trade_followers: tuple[tuple[str, float], ...]
    copy_trade_slippage_bps: float
    auto_trade_symbols: list[str]
    auto_trade_symbols_set: set[str]
    auto_trade_state_file: str
    auto_trade_state_save_interval_seconds: int
    service_started_at: int


def load_settings() -> Settings:
    symbols = parse_env_symbols(os.getenv("SYMBOLS"))
    default_symbol = symbols[0]
    default_timeframe = parse_default_timeframe(os.getenv("DEFAULT_TIMEFRAME"))
    signal_timeframe = parse_default_timeframe(os.getenv("SIGNAL_TIMEFRAME"))

    auto_trade_enabled = parse_env_bool(os.getenv("AUTO_TRADE_ENABLED"), False)
    paper_trading = parse_env_bool(os.getenv("PAPER_TRADING"), True)
    paper_wallet_enabled = parse_env_bool(os.getenv("PAPER_WALLET_ENABLED"), True)
    paper_wallet_start_usdt = max(
        parse_env_float(os.getenv("PAPER_WALLET_START_USDT"), 10000.0),
        0.0,
    )
    api_key = (os.getenv("API_KEY") or "").strip()
    api_secret = (os.getenv("API_SECRET") or "").strip()
    telegram_enabled = parse_env_bool(os.getenv("TELEGRAM_ENABLED"), False)
    telegram_bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    telegram_chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    telegram_auto_trade_only = parse_env_bool(
        os.getenv("TELEGRAM_AUTO_TRADE_ONLY"),
        True,
    )

    if telegram_enabled and (not telegram_bot_token or not telegram_chat_id):
        logger.warning(
            "TELEGRAM_ENABLED is true but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are missing. "
            "Telegram disabled."
        )
        telegram_enabled = False

    requested_auto_trade_symbols = (
        parse_env_symbols(os.getenv("AUTO_TRADE_SYMBOLS"))
        if (os.getenv("AUTO_TRADE_SYMBOLS") or "").strip()
        else symbols[: min(3, len(symbols))]
    )
    auto_trade_symbols = [symbol for symbol in requested_auto_trade_symbols if symbol in symbols]
    if not auto_trade_symbols:
        auto_trade_symbols = [default_symbol]

    auto_trade_strategy_mode = parse_auto_trade_mode(os.getenv("AUTO_TRADE_STRATEGY_MODE"))
    auto_trade_enable_short = parse_env_bool(
        os.getenv("AUTO_TRADE_ENABLE_SHORT"),
        auto_trade_strategy_mode in {"short_only", "both"},
    )
    if auto_trade_strategy_mode in {"short_only", "both"} and not auto_trade_enable_short:
        logger.warning(
            "AUTO_TRADE_STRATEGY_MODE=%s but AUTO_TRADE_ENABLE_SHORT=false, forcing long_only",
            auto_trade_strategy_mode,
        )
        auto_trade_strategy_mode = "long_only"

    if auto_trade_enabled and not paper_trading and (not api_key or not api_secret):
        logger.warning(
            "AUTO_TRADE_ENABLED is true but API_KEY/API_SECRET are missing. Auto trading disabled.",
        )
        auto_trade_enabled = False

    base_trade_size_usdt = max(parse_env_float(os.getenv("TRADE_SIZE_USDT"), 20.0), 1.0)
    trade_size_usdt_min = max(
        parse_env_float(os.getenv("TRADE_SIZE_USDT_MIN"), base_trade_size_usdt),
        1.0,
    )
    trade_size_usdt_max = max(
        parse_env_float(os.getenv("TRADE_SIZE_USDT_MAX"), trade_size_usdt_min),
        trade_size_usdt_min,
    )
    trade_size_usdt = min(max(base_trade_size_usdt, trade_size_usdt_min), trade_size_usdt_max)

    base_stop_loss_pct = max(parse_env_float(os.getenv("STOP_LOSS_PCT"), 1.0), 0.1)
    base_take_profit_pct = max(parse_env_float(os.getenv("TAKE_PROFIT_PCT"), 2.0), 0.1)

    long_stop_loss_pct = max(
        parse_env_float(os.getenv("LONG_STOP_LOSS_PCT"), base_stop_loss_pct),
        0.1,
    )
    long_take_profit_pct = max(
        parse_env_float(os.getenv("LONG_TAKE_PROFIT_PCT"), base_take_profit_pct),
        0.1,
    )
    short_stop_loss_pct = max(
        parse_env_float(os.getenv("SHORT_STOP_LOSS_PCT"), base_stop_loss_pct),
        0.1,
    )
    short_take_profit_pct = max(
        parse_env_float(os.getenv("SHORT_TAKE_PROFIT_PCT"), base_take_profit_pct),
        0.1,
    )

    long_trailing_pct = max(parse_env_float(os.getenv("LONG_TRAILING_PCT"), 5.0), 0.0)
    short_trailing_pct = max(parse_env_float(os.getenv("SHORT_TRAILING_PCT"), 4.0), 0.0)

    long_rsi_min = min(max(parse_env_float(os.getenv("LONG_RSI_MIN"), 45.0), 0.0), 100.0)
    long_rsi_max = min(max(parse_env_float(os.getenv("LONG_RSI_MAX"), 60.0), 0.0), 100.0)
    if long_rsi_max < long_rsi_min:
        long_rsi_min, long_rsi_max = long_rsi_max, long_rsi_min

    short_rsi_min = min(max(parse_env_float(os.getenv("SHORT_RSI_MIN"), 60.0), 0.0), 100.0)
    short_rsi_max = min(max(parse_env_float(os.getenv("SHORT_RSI_MAX"), 75.0), 0.0), 100.0)
    if short_rsi_max < short_rsi_min:
        short_rsi_min, short_rsi_max = short_rsi_max, short_rsi_min

    cooldown_seconds = max(parse_env_int(os.getenv("COOLDOWN_SECONDS"), 300), 1)
    cooldown_min_seconds = max(
        parse_env_int(os.getenv("COOLDOWN_MIN_SECONDS"), cooldown_seconds),
        1,
    )
    cooldown_max_seconds = max(
        parse_env_int(os.getenv("COOLDOWN_MAX_SECONDS"), cooldown_seconds),
        cooldown_min_seconds,
    )

    raw_auto_trade_max_open_positions = parse_env_int(
        os.getenv("AUTO_TRADE_MAX_OPEN_POSITIONS"),
        2,
    )
    # 0 or negative means unlimited open positions.
    auto_trade_max_open_positions = (
        0
        if raw_auto_trade_max_open_positions <= 0
        else raw_auto_trade_max_open_positions
    )
    auto_trade_auto_convert_to_usdt = parse_env_bool(
        os.getenv("AUTO_TRADE_AUTO_CONVERT_TO_USDT"),
        False,
    )
    auto_trade_auto_convert_min_usdt = max(
        parse_env_float(os.getenv("AUTO_TRADE_AUTO_CONVERT_MIN_USDT"), 3.0),
        0.0,
    )
    auto_trade_auto_convert_interval_seconds = max(
        parse_env_int(os.getenv("AUTO_TRADE_AUTO_CONVERT_INTERVAL_SECONDS"), 120),
        5,
    )
    auto_trade_symbol_validation_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_SYMBOL_VALIDATION_ENABLED"),
        True,
    )

    auto_trade_volatility_sizing_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_VOLATILITY_SIZING_ENABLED"),
        True,
    )
    auto_trade_target_atr_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_TARGET_ATR_PCT"), 0.9),
        0.05,
    )
    auto_trade_volatility_size_min_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_VOLATILITY_SIZE_MIN_MULT"), 0.55),
        0.05,
    )
    auto_trade_volatility_size_max_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_VOLATILITY_SIZE_MAX_MULT"), 1.65),
        auto_trade_volatility_size_min_mult,
    )
    auto_trade_kelly_enabled = parse_env_bool(os.getenv("AUTO_TRADE_KELLY_ENABLED"), True)
    auto_trade_kelly_fraction = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_KELLY_FRACTION"), 0.25), 0.01),
        1.0,
    )
    auto_trade_kelly_max_fraction_pct = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_KELLY_MAX_FRACTION_PCT"), 4.0), 0.1),
        25.0,
    )
    auto_trade_kelly_min_trades = max(
        parse_env_int(os.getenv("AUTO_TRADE_KELLY_MIN_TRADES"), 12),
        3,
    )
    auto_trade_correlation_risk_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_CORRELATION_RISK_ENABLED"),
        True,
    )
    auto_trade_max_correlation = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_MAX_CORRELATION"), 0.82), 0.1),
        0.99,
    )
    auto_trade_correlation_lookback = max(
        parse_env_int(os.getenv("AUTO_TRADE_CORRELATION_LOOKBACK"), 60),
        10,
    )
    auto_trade_correlation_risk_mult = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_CORRELATION_RISK_MULT"), 0.55), 0.05),
        1.0,
    )
    auto_trade_max_drawdown_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_MAX_DRAWDOWN_ENABLED"),
        True,
    )
    auto_trade_max_drawdown_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_MAX_DRAWDOWN_PCT"), 15.0),
        0.5,
    )
    auto_trade_dynamic_exit_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_DYNAMIC_EXIT_ENABLED"),
        True,
    )
    auto_trade_dynamic_exit_min_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_DYNAMIC_EXIT_MIN_MULT"), 0.65),
        0.1,
    )
    auto_trade_dynamic_exit_max_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_DYNAMIC_EXIT_MAX_MULT"), 1.75),
        auto_trade_dynamic_exit_min_mult,
    )
    auto_trade_extreme_volatility_exit_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_EXTREME_VOLATILITY_EXIT_ENABLED"),
        True,
    )
    auto_trade_extreme_volatility_exit_atr_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_EXTREME_VOLATILITY_EXIT_ATR_PCT"), 7.5),
        0.1,
    )
    auto_trade_extreme_volatility_exit_change_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_EXTREME_VOLATILITY_EXIT_CHANGE_PCT"), 24.0),
        0.1,
    )
    auto_trade_profit_lock_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_PROFIT_LOCK_ENABLED"),
        True,
    )
    auto_trade_profit_lock_trigger_usdt = max(
        parse_env_float(os.getenv("AUTO_TRADE_PROFIT_LOCK_TRIGGER_USDT"), 8.0),
        0.0,
    )
    auto_trade_profit_lock_giveback_pct = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_PROFIT_LOCK_GIVEBACK_PCT"), 35.0), 1.0),
        95.0,
    )
    auto_trade_profit_lock_pause_seconds = max(
        parse_env_int(os.getenv("AUTO_TRADE_PROFIT_LOCK_PAUSE_SECONDS"), 1800),
        30,
    )
    auto_trade_profit_lock_risk_mult = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_PROFIT_LOCK_RISK_MULT"), 0.55), 0.05),
        1.0,
    )
    auto_trade_confidence_sizing_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_CONFIDENCE_SIZING_ENABLED"),
        True,
    )
    auto_trade_confidence_size_min_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_CONFIDENCE_SIZE_MIN_MULT"), 0.55),
        0.05,
    )
    auto_trade_confidence_size_max_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_CONFIDENCE_SIZE_MAX_MULT"), 1.35),
        auto_trade_confidence_size_min_mult,
    )
    auto_trade_regime_sizing_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_REGIME_SIZING_ENABLED"),
        True,
    )
    auto_trade_regime_risk_on_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_REGIME_RISK_ON_MULT"), 1.1),
        0.1,
    )
    auto_trade_regime_risk_off_mult = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_REGIME_RISK_OFF_MULT"), 0.65), 0.05),
        1.0,
    )
    auto_trade_compounding_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_COMPOUNDING_ENABLED"),
        True,
    )
    auto_trade_compounding_profit_step_usdt = max(
        parse_env_float(os.getenv("AUTO_TRADE_COMPOUNDING_PROFIT_STEP_USDT"), 20.0),
        0.01,
    )
    auto_trade_compounding_max_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_COMPOUNDING_MAX_MULT"), 1.35),
        1.0,
    )
    auto_trade_entry_quality_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_ENTRY_QUALITY_ENABLED"),
        True,
    )
    auto_trade_min_entry_score = min(
        max(parse_env_int(os.getenv("AUTO_TRADE_MIN_ENTRY_SCORE"), 70), 0),
        100,
    )
    auto_trade_min_entry_probability = min(
        max(parse_env_int(os.getenv("AUTO_TRADE_MIN_ENTRY_PROBABILITY"), 75), 0),
        100,
    )
    auto_trade_min_risk_reward = max(
        parse_env_float(os.getenv("AUTO_TRADE_MIN_RISK_REWARD"), 2.0),
        0.1,
    )
    auto_trade_min_liquidity_score = max(
        parse_env_float(os.getenv("AUTO_TRADE_MIN_LIQUIDITY_SCORE"), 1.0),
        0.0,
    )
    auto_trade_max_correlated_positions = max(
        parse_env_int(os.getenv("AUTO_TRADE_MAX_CORRELATED_POSITIONS"), 3),
        1,
    )
    auto_trade_circuit_breaker_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_CIRCUIT_BREAKER_ENABLED"),
        True,
    )
    auto_trade_circuit_breaker_volatility_symbols = max(
        parse_env_int(os.getenv("AUTO_TRADE_CIRCUIT_BREAKER_VOLATILITY_SYMBOLS"), 3),
        1,
    )
    auto_trade_max_daily_loss_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_MAX_DAILY_LOSS_PCT"), 3.0),
        0.1,
    )

    auto_trade_entry_confirm_ema_stack = parse_env_bool(
        os.getenv("AUTO_TRADE_ENTRY_CONFIRM_EMA_STACK"),
        True,
    )
    auto_trade_entry_confirm_macd = parse_env_bool(
        os.getenv("AUTO_TRADE_ENTRY_CONFIRM_MACD"),
        True,
    )
    auto_trade_entry_require_macd_data = parse_env_bool(
        os.getenv("AUTO_TRADE_ENTRY_REQUIRE_MACD_DATA"),
        True,
    )
    weak_ema_fast, weak_ema_slow = parse_ema_pair(
        os.getenv("AUTO_TRADE_PROFILE_WEAK_EMA_FAST"),
        os.getenv("AUTO_TRADE_PROFILE_WEAK_EMA_SLOW"),
        34,
        89,
    )
    middle_ema_fast, middle_ema_slow = parse_ema_pair(
        os.getenv("AUTO_TRADE_PROFILE_MIDDLE_EMA_FAST"),
        os.getenv("AUTO_TRADE_PROFILE_MIDDLE_EMA_SLOW"),
        20,
        50,
    )
    strong_ema_fast, strong_ema_slow = parse_ema_pair(
        os.getenv("AUTO_TRADE_PROFILE_STRONG_EMA_FAST"),
        os.getenv("AUTO_TRADE_PROFILE_STRONG_EMA_SLOW"),
        9,
        21,
    )
    auto_trade_min_volume_ratio = max(
        parse_env_float(os.getenv("AUTO_TRADE_MIN_VOLUME_RATIO"), 0.8),
        0.0,
    )
    auto_trade_min_strength_confidence = min(
        max(parse_env_int(os.getenv("AUTO_TRADE_MIN_STRENGTH_CONFIDENCE"), 25), 0),
        100,
    )

    auto_trade_session_filter_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_SESSION_FILTER_ENABLED"),
        False,
    )
    auto_trade_session_utc = (os.getenv("AUTO_TRADE_SESSION_UTC") or "0-23").strip()
    auto_trade_extreme_volatility_block_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_EXTREME_VOLATILITY_BLOCK_ENABLED"),
        True,
    )
    auto_trade_max_atr_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_MAX_ATR_PCT"), 5.5),
        0.1,
    )
    auto_trade_max_abs_change_24h_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_MAX_ABS_CHANGE_24H_PCT"), 20.0),
        0.1,
    )
    auto_trade_execution_cost_gate_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_EXECUTION_COST_GATE_ENABLED"),
        True,
    )
    auto_trade_max_spread_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_MAX_SPREAD_PCT"), 0.12),
        0.0,
    )
    auto_trade_estimated_fee_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_ESTIMATED_FEE_PCT"), 0.08),
        0.0,
    )
    auto_trade_estimated_slippage_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_ESTIMATED_SLIPPAGE_PCT"), 0.05),
        0.0,
    )
    auto_trade_min_edge_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_MIN_EDGE_PCT"), 0.12),
        0.0,
    )
    auto_trade_symbol_rank_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_SYMBOL_RANK_ENABLED"),
        True,
    )
    auto_trade_symbol_rank_top_n = max(
        parse_env_int(os.getenv("AUTO_TRADE_SYMBOL_RANK_TOP_N"), 2),
        1,
    )
    auto_trade_take_profit_run_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_TP_AND_RUN_ENABLED"),
        False,
    )
    auto_trade_take_profit_run_min_pnl_pct = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_TP_AND_RUN_MIN_PNL_PCT"), 0.0), 0.0),
        100.0,
    )

    auto_trade_partial_take_profit_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_PARTIAL_TP_ENABLED"),
        True,
    )
    auto_trade_partial_take_profit_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_PARTIAL_TP_PCT"), 3.0),
        0.1,
    )
    auto_trade_partial_take_profit_ratio = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_PARTIAL_TP_RATIO"), 0.5), 0.05),
        0.95,
    )
    auto_trade_break_even_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_BREAK_EVEN_ENABLED"),
        True,
    )
    auto_trade_break_even_trigger_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_BREAK_EVEN_TRIGGER_PCT"), 1.25),
        0.1,
    )
    auto_trade_break_even_buffer_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_BREAK_EVEN_BUFFER_PCT"), 0.1),
        0.0,
    )
    auto_trade_time_stop_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_TIME_STOP_ENABLED"),
        True,
    )
    auto_trade_time_stop_minutes = max(
        parse_env_int(os.getenv("AUTO_TRADE_TIME_STOP_MINUTES"), 15),
        1,
    )
    auto_trade_time_stop_min_pnl_pct = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_TIME_STOP_MIN_PNL_PCT"), 0.15), -5.0),
        5.0,
    )

    auto_trade_cooldown_adaptive_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_COOLDOWN_ADAPTIVE_ENABLED"),
        True,
    )
    auto_trade_cooldown_loss_step_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_COOLDOWN_LOSS_STEP_MULT"), 0.35),
        0.0,
    )
    auto_trade_cooldown_volatility_target_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_COOLDOWN_VOL_TARGET_ATR_PCT"), 0.9),
        0.05,
    )
    auto_trade_cooldown_volatility_max_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_COOLDOWN_VOL_MAX_MULT"), 3.0),
        1.0,
    )
    auto_trade_max_consecutive_losses = max(
        parse_env_int(os.getenv("AUTO_TRADE_MAX_CONSECUTIVE_LOSSES"), 3),
        1,
    )
    auto_trade_kill_switch_pause_seconds = max(
        parse_env_int(os.getenv("AUTO_TRADE_KILL_SWITCH_PAUSE_SECONDS"), 1800),
        30,
    )

    auto_trade_forward_guardrail_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_FORWARD_GUARDRAIL_ENABLED"),
        True,
    )
    auto_trade_forward_guardrail_min_trades = max(
        parse_env_int(os.getenv("AUTO_TRADE_FORWARD_GUARDRAIL_MIN_TRADES"), 14),
        1,
    )
    auto_trade_forward_baseline_win_rate = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_FORWARD_BASELINE_WIN_RATE"), 0.5), 0.0),
        1.0,
    )
    auto_trade_forward_baseline_avg_pnl_usdt = parse_env_float(
        os.getenv("AUTO_TRADE_FORWARD_BASELINE_AVG_PNL_USDT"),
        0.0,
    )
    auto_trade_forward_guardrail_risk_mult = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_FORWARD_GUARDRAIL_RISK_MULT"), 0.6), 0.05),
        1.0,
    )
    auto_trade_forward_guardrail_severe_win_rate = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_FORWARD_GUARDRAIL_SEVERE_WIN_RATE"), 0.35), 0.0),
        1.0,
    )
    auto_trade_forward_guardrail_halt_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_FORWARD_GUARDRAIL_HALT_ENABLED"),
        False,
    )
    market_regime_enabled = parse_env_bool(os.getenv("MARKET_REGIME_ENABLED"), True)
    market_regime_clusters = max(
        parse_env_int(os.getenv("MARKET_REGIME_CLUSTERS"), 3),
        2,
    )
    auto_trade_self_learning_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_SELF_LEARNING_ENABLED"),
        True,
    )
    auto_trade_self_learning_min_trades = max(
        parse_env_int(os.getenv("AUTO_TRADE_SELF_LEARNING_MIN_TRADES"), 10),
        3,
    )
    auto_trade_self_learning_risk_min_mult = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_SELF_LEARNING_RISK_MIN_MULT"), 0.55), 0.05),
        1.0,
    )
    auto_trade_self_learning_risk_max_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_SELF_LEARNING_RISK_MAX_MULT"), 1.2),
        auto_trade_self_learning_risk_min_mult,
    )
    auto_trade_lstm_learning_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_LSTM_LEARNING_ENABLED"),
        True,
    )
    auto_trade_lstm_learning_rate = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_LSTM_LEARNING_RATE"), 0.08), 0.001),
        0.5,
    )

    auto_trade_auto_adapt_enabled = parse_env_bool(
        os.getenv("AUTO_TRADE_AUTO_ADAPT_ENABLED"),
        True,
    )
    auto_trade_auto_adapt_low_atr_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_AUTO_ADAPT_LOW_ATR_PCT"), 0.9),
        0.05,
    )
    auto_trade_auto_adapt_high_atr_pct = max(
        parse_env_float(os.getenv("AUTO_TRADE_AUTO_ADAPT_HIGH_ATR_PCT"), 2.2),
        auto_trade_auto_adapt_low_atr_pct,
    )
    auto_trade_auto_adapt_conf_step = min(
        max(parse_env_int(os.getenv("AUTO_TRADE_AUTO_ADAPT_CONF_STEP"), 8), 0),
        40,
    )
    auto_trade_auto_adapt_risk_on_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_AUTO_ADAPT_RISK_ON_MULT"), 1.15),
        0.2,
    )
    auto_trade_auto_adapt_risk_off_mult = min(
        max(parse_env_float(os.getenv("AUTO_TRADE_AUTO_ADAPT_RISK_OFF_MULT"), 0.7), 0.05),
        1.0,
    )
    auto_trade_auto_adapt_cooldown_on_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_AUTO_ADAPT_COOLDOWN_ON_MULT"), 0.8),
        0.2,
    )
    auto_trade_auto_adapt_cooldown_off_mult = max(
        parse_env_float(os.getenv("AUTO_TRADE_AUTO_ADAPT_COOLDOWN_OFF_MULT"), 1.4),
        1.0,
    )

    copy_trade_enabled = parse_env_bool(os.getenv("COPY_TRADE_ENABLED"), False)
    copy_trade_followers = parse_copy_trade_followers(os.getenv("COPY_TRADE_FOLLOWERS"))
    copy_trade_slippage_bps = min(
        max(parse_env_float(os.getenv("COPY_TRADE_SLIPPAGE_BPS"), 4.0), 0.0),
        50.0,
    )
    auto_trade_state_file = (os.getenv("AUTO_TRADE_STATE_FILE") or "data/auto_trade_state.json").strip()
    if not auto_trade_state_file:
        auto_trade_state_file = "data/auto_trade_state.json"
    auto_trade_state_save_interval_seconds = max(
        parse_env_int(os.getenv("AUTO_TRADE_STATE_SAVE_INTERVAL_SECONDS"), 5),
        1,
    )

    return Settings(
        title="Nurix Trading Dashboard",
        signal_timeframe=signal_timeframe,
        signal_candle_limit=120,
        chart_candle_limit=220,
        push_interval_seconds=2.0,
        # Higher cache window is safer for larger watchlists (e.g. top-50),
        # reducing REST pressure and long-run instability.
        indicator_cache_seconds=(45 if len(symbols) >= 30 else 20),
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
        telegram_enabled=telegram_enabled,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        telegram_auto_trade_only=telegram_auto_trade_only,
        auto_trade_enabled=auto_trade_enabled,
        paper_trading=paper_trading,
        paper_wallet_enabled=paper_wallet_enabled,
        paper_wallet_start_usdt=paper_wallet_start_usdt,
        trade_size_usdt=trade_size_usdt,
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
        auto_trade_strategy_mode=auto_trade_strategy_mode,
        auto_trade_enable_short=auto_trade_enable_short,
        auto_trade_max_open_positions=auto_trade_max_open_positions,
        auto_trade_auto_convert_to_usdt=auto_trade_auto_convert_to_usdt,
        auto_trade_auto_convert_min_usdt=auto_trade_auto_convert_min_usdt,
        auto_trade_auto_convert_interval_seconds=auto_trade_auto_convert_interval_seconds,
        auto_trade_symbol_validation_enabled=auto_trade_symbol_validation_enabled,
        trade_size_usdt_min=trade_size_usdt_min,
        trade_size_usdt_max=trade_size_usdt_max,
        auto_trade_volatility_sizing_enabled=auto_trade_volatility_sizing_enabled,
        auto_trade_target_atr_pct=auto_trade_target_atr_pct,
        auto_trade_volatility_size_min_mult=auto_trade_volatility_size_min_mult,
        auto_trade_volatility_size_max_mult=auto_trade_volatility_size_max_mult,
        auto_trade_kelly_enabled=auto_trade_kelly_enabled,
        auto_trade_kelly_fraction=auto_trade_kelly_fraction,
        auto_trade_kelly_max_fraction_pct=auto_trade_kelly_max_fraction_pct,
        auto_trade_kelly_min_trades=auto_trade_kelly_min_trades,
        auto_trade_correlation_risk_enabled=auto_trade_correlation_risk_enabled,
        auto_trade_max_correlation=auto_trade_max_correlation,
        auto_trade_correlation_lookback=auto_trade_correlation_lookback,
        auto_trade_correlation_risk_mult=auto_trade_correlation_risk_mult,
        auto_trade_max_drawdown_enabled=auto_trade_max_drawdown_enabled,
        auto_trade_max_drawdown_pct=auto_trade_max_drawdown_pct,
        auto_trade_dynamic_exit_enabled=auto_trade_dynamic_exit_enabled,
        auto_trade_dynamic_exit_min_mult=auto_trade_dynamic_exit_min_mult,
        auto_trade_dynamic_exit_max_mult=auto_trade_dynamic_exit_max_mult,
        auto_trade_extreme_volatility_exit_enabled=auto_trade_extreme_volatility_exit_enabled,
        auto_trade_extreme_volatility_exit_atr_pct=auto_trade_extreme_volatility_exit_atr_pct,
        auto_trade_extreme_volatility_exit_change_pct=auto_trade_extreme_volatility_exit_change_pct,
        auto_trade_profit_lock_enabled=auto_trade_profit_lock_enabled,
        auto_trade_profit_lock_trigger_usdt=auto_trade_profit_lock_trigger_usdt,
        auto_trade_profit_lock_giveback_pct=auto_trade_profit_lock_giveback_pct,
        auto_trade_profit_lock_pause_seconds=auto_trade_profit_lock_pause_seconds,
        auto_trade_profit_lock_risk_mult=auto_trade_profit_lock_risk_mult,
        auto_trade_confidence_sizing_enabled=auto_trade_confidence_sizing_enabled,
        auto_trade_confidence_size_min_mult=auto_trade_confidence_size_min_mult,
        auto_trade_confidence_size_max_mult=auto_trade_confidence_size_max_mult,
        auto_trade_regime_sizing_enabled=auto_trade_regime_sizing_enabled,
        auto_trade_regime_risk_on_mult=auto_trade_regime_risk_on_mult,
        auto_trade_regime_risk_off_mult=auto_trade_regime_risk_off_mult,
        auto_trade_compounding_enabled=auto_trade_compounding_enabled,
        auto_trade_compounding_profit_step_usdt=auto_trade_compounding_profit_step_usdt,
        auto_trade_compounding_max_mult=auto_trade_compounding_max_mult,
        auto_trade_entry_quality_enabled=auto_trade_entry_quality_enabled,
        auto_trade_min_entry_score=auto_trade_min_entry_score,
        auto_trade_min_entry_probability=auto_trade_min_entry_probability,
        auto_trade_min_risk_reward=auto_trade_min_risk_reward,
        auto_trade_min_liquidity_score=auto_trade_min_liquidity_score,
        auto_trade_max_correlated_positions=auto_trade_max_correlated_positions,
        auto_trade_circuit_breaker_enabled=auto_trade_circuit_breaker_enabled,
        auto_trade_circuit_breaker_volatility_symbols=auto_trade_circuit_breaker_volatility_symbols,
        auto_trade_max_daily_loss_pct=auto_trade_max_daily_loss_pct,
        long_rsi_min=long_rsi_min,
        long_rsi_max=long_rsi_max,
        short_rsi_min=short_rsi_min,
        short_rsi_max=short_rsi_max,
        auto_trade_entry_confirm_ema_stack=auto_trade_entry_confirm_ema_stack,
        auto_trade_entry_confirm_macd=auto_trade_entry_confirm_macd,
        auto_trade_entry_require_macd_data=auto_trade_entry_require_macd_data,
        auto_trade_profile_weak_ema_fast=weak_ema_fast,
        auto_trade_profile_weak_ema_slow=weak_ema_slow,
        auto_trade_profile_middle_ema_fast=middle_ema_fast,
        auto_trade_profile_middle_ema_slow=middle_ema_slow,
        auto_trade_profile_strong_ema_fast=strong_ema_fast,
        auto_trade_profile_strong_ema_slow=strong_ema_slow,
        auto_trade_min_volume_ratio=auto_trade_min_volume_ratio,
        auto_trade_min_strength_confidence=auto_trade_min_strength_confidence,
        auto_trade_session_filter_enabled=auto_trade_session_filter_enabled,
        auto_trade_session_utc=auto_trade_session_utc,
        auto_trade_extreme_volatility_block_enabled=auto_trade_extreme_volatility_block_enabled,
        auto_trade_max_atr_pct=auto_trade_max_atr_pct,
        auto_trade_max_abs_change_24h_pct=auto_trade_max_abs_change_24h_pct,
        auto_trade_execution_cost_gate_enabled=auto_trade_execution_cost_gate_enabled,
        auto_trade_max_spread_pct=auto_trade_max_spread_pct,
        auto_trade_estimated_fee_pct=auto_trade_estimated_fee_pct,
        auto_trade_estimated_slippage_pct=auto_trade_estimated_slippage_pct,
        auto_trade_min_edge_pct=auto_trade_min_edge_pct,
        auto_trade_symbol_rank_enabled=auto_trade_symbol_rank_enabled,
        auto_trade_symbol_rank_top_n=auto_trade_symbol_rank_top_n,
        long_stop_loss_pct=long_stop_loss_pct,
        long_take_profit_pct=long_take_profit_pct,
        long_trailing_pct=long_trailing_pct,
        short_stop_loss_pct=short_stop_loss_pct,
        short_take_profit_pct=short_take_profit_pct,
        short_trailing_pct=short_trailing_pct,
        auto_trade_take_profit_run_enabled=auto_trade_take_profit_run_enabled,
        auto_trade_take_profit_run_min_pnl_pct=auto_trade_take_profit_run_min_pnl_pct,
        auto_trade_partial_take_profit_enabled=auto_trade_partial_take_profit_enabled,
        auto_trade_partial_take_profit_pct=auto_trade_partial_take_profit_pct,
        auto_trade_partial_take_profit_ratio=auto_trade_partial_take_profit_ratio,
        auto_trade_break_even_enabled=auto_trade_break_even_enabled,
        auto_trade_break_even_trigger_pct=auto_trade_break_even_trigger_pct,
        auto_trade_break_even_buffer_pct=auto_trade_break_even_buffer_pct,
        auto_trade_time_stop_enabled=auto_trade_time_stop_enabled,
        auto_trade_time_stop_minutes=auto_trade_time_stop_minutes,
        auto_trade_time_stop_min_pnl_pct=auto_trade_time_stop_min_pnl_pct,
        stop_loss_pct=base_stop_loss_pct,
        take_profit_pct=base_take_profit_pct,
        cooldown_min_seconds=cooldown_min_seconds,
        cooldown_max_seconds=cooldown_max_seconds,
        cooldown_seconds=cooldown_seconds,
        auto_trade_cooldown_adaptive_enabled=auto_trade_cooldown_adaptive_enabled,
        auto_trade_cooldown_loss_step_mult=auto_trade_cooldown_loss_step_mult,
        auto_trade_cooldown_volatility_target_pct=auto_trade_cooldown_volatility_target_pct,
        auto_trade_cooldown_volatility_max_mult=auto_trade_cooldown_volatility_max_mult,
        auto_trade_max_consecutive_losses=auto_trade_max_consecutive_losses,
        auto_trade_kill_switch_pause_seconds=auto_trade_kill_switch_pause_seconds,
        max_daily_loss_usdt=max(
            parse_env_float(os.getenv("MAX_DAILY_LOSS_USDT"), 50.0),
            1.0,
        ),
        auto_trade_min_confidence=min(
            max(parse_env_int(os.getenv("AUTO_TRADE_MIN_CONFIDENCE"), 55), 0),
            100,
        ),
        ai_filter_enabled=parse_env_bool(os.getenv("AI_FILTER_ENABLED"), True),
        ai_filter_min_confidence=min(
            max(parse_env_int(os.getenv("AI_FILTER_MIN_CONFIDENCE"), 55), 0),
            100,
        ),
        ai_filter_min_score_abs=max(
            parse_env_float(os.getenv("AI_FILTER_MIN_SCORE_ABS"), 1.0),
            0.0,
        ),
        advanced_ai_enabled=parse_env_bool(os.getenv("ADVANCED_AI_ENABLED"), True),
        advanced_ai_quantum_enabled=parse_env_bool(
            os.getenv("ADVANCED_AI_QUANTUM_ENABLED"),
            True,
        ),
        sentiment_enabled=parse_env_bool(os.getenv("SENTIMENT_ENABLED"), True),
        sentiment_lookback_minutes=max(
            parse_env_int(os.getenv("SENTIMENT_LOOKBACK_MINUTES"), 180),
            1,
        ),
        market_regime_enabled=market_regime_enabled,
        market_regime_clusters=market_regime_clusters,
        auto_trade_self_learning_enabled=auto_trade_self_learning_enabled,
        auto_trade_self_learning_min_trades=auto_trade_self_learning_min_trades,
        auto_trade_self_learning_risk_min_mult=auto_trade_self_learning_risk_min_mult,
        auto_trade_self_learning_risk_max_mult=auto_trade_self_learning_risk_max_mult,
        auto_trade_lstm_learning_enabled=auto_trade_lstm_learning_enabled,
        auto_trade_lstm_learning_rate=auto_trade_lstm_learning_rate,
        auto_trade_auto_adapt_enabled=auto_trade_auto_adapt_enabled,
        auto_trade_auto_adapt_low_atr_pct=auto_trade_auto_adapt_low_atr_pct,
        auto_trade_auto_adapt_high_atr_pct=auto_trade_auto_adapt_high_atr_pct,
        auto_trade_auto_adapt_conf_step=auto_trade_auto_adapt_conf_step,
        auto_trade_auto_adapt_risk_on_mult=auto_trade_auto_adapt_risk_on_mult,
        auto_trade_auto_adapt_risk_off_mult=auto_trade_auto_adapt_risk_off_mult,
        auto_trade_auto_adapt_cooldown_on_mult=auto_trade_auto_adapt_cooldown_on_mult,
        auto_trade_auto_adapt_cooldown_off_mult=auto_trade_auto_adapt_cooldown_off_mult,
        auto_trade_forward_guardrail_enabled=auto_trade_forward_guardrail_enabled,
        auto_trade_forward_guardrail_min_trades=auto_trade_forward_guardrail_min_trades,
        auto_trade_forward_baseline_win_rate=auto_trade_forward_baseline_win_rate,
        auto_trade_forward_baseline_avg_pnl_usdt=auto_trade_forward_baseline_avg_pnl_usdt,
        auto_trade_forward_guardrail_risk_mult=auto_trade_forward_guardrail_risk_mult,
        auto_trade_forward_guardrail_severe_win_rate=auto_trade_forward_guardrail_severe_win_rate,
        auto_trade_forward_guardrail_halt_enabled=auto_trade_forward_guardrail_halt_enabled,
        copy_trade_enabled=copy_trade_enabled,
        copy_trade_followers=copy_trade_followers,
        copy_trade_slippage_bps=copy_trade_slippage_bps,
        auto_trade_symbols=auto_trade_symbols,
        auto_trade_symbols_set=set(auto_trade_symbols),
        auto_trade_state_file=auto_trade_state_file,
        auto_trade_state_save_interval_seconds=auto_trade_state_save_interval_seconds,
        service_started_at=int(time.time()),
    )
