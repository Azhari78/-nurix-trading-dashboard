from __future__ import annotations

import threading
from collections import deque
from typing import Any


class RuntimeState:
    def __init__(self, *, alert_max_events: int, auto_trade_max_events: int) -> None:
        self.indicator_cache: dict[str, dict[str, Any]] = {}
        self.chart_cache: dict[str, dict[str, Any]] = {}
        self.orderflow_cache: dict[str, dict[str, Any]] = {}
        self.wallet_cache: dict[str, Any] = {}
        self.symbol_state: dict[str, dict[str, Any]] = {}

        self.alert_events: deque[dict[str, Any]] = deque(maxlen=alert_max_events)
        self.alert_counter = 0
        self.alert_lock = threading.Lock()

        self.wallet_lock = threading.Lock()
        self.auto_trade_lock = threading.Lock()
        self.paper_wallet_initialized = False
        self.paper_wallet_free_usdt = 0.0
        self.paper_wallet_used_usdt = 0.0
        self.paper_wallet_realized_pnl_usdt = 0.0

        self.auto_trade_events: deque[dict[str, Any]] = deque(maxlen=auto_trade_max_events)
        self.auto_trade_counter = 0
        self.auto_trade_positions: dict[str, dict[str, Any]] = {}
        self.auto_trade_last_action_ts: dict[str, float] = {}
        self.auto_trade_cooldown_until: dict[str, float] = {}
        self.auto_trade_daily_pnl: dict[str, float] = {}
        self.auto_trade_last_eval_at = 0.0
        self.auto_trade_halt_day: str | None = None
        self.auto_trade_halt_until = 0.0
        self.auto_trade_halt_reason: str | None = None
        self.auto_trade_halt_reason_by_day: dict[str, str] = {}
        self.auto_trade_consecutive_losses = 0
        self.auto_trade_last_risk_multiplier = 1.0
        self.auto_trade_guardrail_active = False
        self.auto_trade_guardrail_reason = ""
        self.auto_trade_guardrail_halt_sample_key = ""
        self.auto_trade_last_reason = "Waiting for market conditions"
        self.auto_trade_last_daily_recap_day: str | None = None
        self.auto_trade_adaptive_profile = "MIDDLE"
        self.auto_trade_adaptive_reason = ""
        self.auto_trade_adaptive_ai_min_confidence = 0
        self.auto_trade_adaptive_risk_multiplier = 1.0
        self.auto_trade_adaptive_cooldown_multiplier = 1.0
        self.auto_trade_last_convert_at = 0.0
        self.auto_trade_journal: deque[dict[str, Any]] = deque(maxlen=1000)
        self.auto_trade_stats_by_symbol: dict[str, dict[str, Any]] = {}
        self.copy_trade_events: deque[dict[str, Any]] = deque(maxlen=max(50, auto_trade_max_events))
        self.copy_trade_counter = 0
        self.copy_trade_positions: dict[str, dict[str, dict[str, Any]]] = {}
        self.copy_trade_stats: dict[str, dict[str, Any]] = {}

        self.markets_loaded = False

        self.wallet_day_key: str | None = None
        self.wallet_day_start_total_usdt: float | None = None
