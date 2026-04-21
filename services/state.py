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

        self.auto_trade_events: deque[dict[str, Any]] = deque(maxlen=auto_trade_max_events)
        self.auto_trade_counter = 0
        self.auto_trade_positions: dict[str, dict[str, Any]] = {}
        self.auto_trade_last_action_ts: dict[str, float] = {}
        self.auto_trade_daily_pnl: dict[str, float] = {}
        self.auto_trade_last_eval_at = 0.0
        self.auto_trade_halt_day: str | None = None
        self.auto_trade_last_reason = "Waiting for market conditions"

        self.markets_loaded = False

        self.wallet_day_key: str | None = None
        self.wallet_day_start_total_usdt: float | None = None
