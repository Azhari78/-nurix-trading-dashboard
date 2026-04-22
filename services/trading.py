from __future__ import annotations

import logging
import random
import time
from contextlib import suppress
from typing import Any

from services.alerts import AlertService
from services.config import Settings
from services.exchange import ExchangeGateway
from services.indicators import safe_float
from services.state import RuntimeState


class TradingService:
    def __init__(
        self,
        settings: Settings,
        state: RuntimeState,
        exchange: ExchangeGateway,
        alerts: AlertService,
        logger: logging.Logger,
    ) -> None:
        self.settings = settings
        self.state = state
        self.exchange = exchange
        self.alerts = alerts
        self.logger = logger
        self._runtime_auto_trade_symbols = list(settings.auto_trade_symbols)
        self._runtime_auto_trade_symbols_set = set(settings.auto_trade_symbols_set)
        self._symbol_validation_done = False
        self._session_windows = self._parse_session_windows(settings.auto_trade_session_utc)

    @staticmethod
    def utc_day_key() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _parse_session_windows(raw_value: str) -> list[tuple[int, int]]:
        windows: list[tuple[int, int]] = []
        for part in (raw_value or "").split(","):
            chunk = part.strip()
            if not chunk:
                continue
            if "-" not in chunk:
                try:
                    hour = int(chunk)
                except ValueError:
                    continue
                hour = hour % 24
                windows.append((hour, hour))
                continue

            left, right = chunk.split("-", 1)
            try:
                start = int(left.strip()) % 24
                end = int(right.strip()) % 24
            except ValueError:
                continue
            windows.append((start, end))

        if not windows:
            return [(0, 23)]
        return windows

    def _session_allows_entry(self, now_ts: float) -> bool:
        if not self.settings.auto_trade_session_filter_enabled:
            return True
        hour = int(time.gmtime(now_ts).tm_hour)
        for start, end in self._session_windows:
            if start <= end:
                if start <= hour <= end:
                    return True
            else:
                if hour >= start or hour <= end:
                    return True
        return False

    def _ensure_symbol_whitelist(self) -> None:
        if self._symbol_validation_done:
            return
        self._symbol_validation_done = True
        if not self.settings.auto_trade_symbol_validation_enabled:
            return

        try:
            self.exchange.call("load_markets")
            valid: list[str] = []
            invalid: list[str] = []
            for symbol in self.settings.auto_trade_symbols:
                try:
                    self.exchange.call("market", symbol)
                    valid.append(symbol)
                except Exception:  # noqa: BLE001
                    invalid.append(symbol)

            if valid:
                self._runtime_auto_trade_symbols = valid
                self._runtime_auto_trade_symbols_set = set(valid)
            else:
                self._runtime_auto_trade_symbols = [self.settings.default_symbol]
                self._runtime_auto_trade_symbols_set = {self.settings.default_symbol}

            if invalid:
                self.alerts.emit_alert(
                    symbol=self.settings.default_symbol,
                    alert_type="auto_trade_symbol_filtered",
                    title="Auto-trade symbol whitelist filtered",
                    message=f"Unsupported on exchange: {', '.join(invalid[:6])}",
                    severity="medium",
                )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Symbol whitelist validation skipped: %s", exc)

    @staticmethod
    def _pnl_pct(entry_price: float, current_price: float, position_side: str) -> float:
        if entry_price <= 0:
            return 0.0
        if position_side == "SHORT":
            return ((entry_price - current_price) / entry_price) * 100
        return ((current_price - entry_price) / entry_price) * 100

    def _record_journal(
        self,
        *,
        symbol: str,
        event_type: str,
        side: str,
        reason: str,
        pnl_usdt: float | None,
        pnl_pct: float | None,
        notional_usdt: float | None,
        price: float | None,
        amount: float | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.state.auto_trade_journal.append(
            {
                "timestamp": int(time.time()),
                "symbol": symbol,
                "event_type": event_type,
                "side": side,
                "reason": reason,
                "pnl_usdt": round(pnl_usdt, 4) if pnl_usdt is not None else None,
                "pnl_pct": round(pnl_pct, 4) if pnl_pct is not None else None,
                "notional_usdt": round(notional_usdt, 4) if notional_usdt is not None else None,
                "price": round(price, 6) if price is not None else None,
                "amount": round(amount, 8) if amount is not None else None,
                "metadata": metadata or {},
            }
        )

    def _update_symbol_stats(self, symbol: str, pnl_usdt: float) -> None:
        stats = self.state.auto_trade_stats_by_symbol.setdefault(
            symbol,
            {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl_usdt": 0.0,
            },
        )
        stats["trades"] += 1
        stats["pnl_usdt"] += pnl_usdt
        if pnl_usdt >= 0:
            stats["wins"] += 1
            self.state.auto_trade_consecutive_losses = 0
        else:
            stats["losses"] += 1
            self.state.auto_trade_consecutive_losses += 1

    def _recent_realized_trades(self, now_ts: float, lookback_seconds: int = 7 * 86400) -> list[dict[str, Any]]:
        minimum_ts = int(now_ts) - lookback_seconds
        return [
            row
            for row in self.state.auto_trade_journal
            if row.get("event_type") in {"EXIT", "PARTIAL_EXIT"}
            and int(row.get("timestamp") or 0) >= minimum_ts
            and row.get("pnl_usdt") is not None
        ]

    def _compute_forward_guardrail(self, now_ts: float) -> tuple[float, str]:
        if not self.settings.auto_trade_forward_guardrail_enabled:
            self.state.auto_trade_guardrail_active = False
            self.state.auto_trade_guardrail_reason = ""
            return 1.0, ""

        realized = self._recent_realized_trades(now_ts)
        if len(realized) < self.settings.auto_trade_forward_guardrail_min_trades:
            self.state.auto_trade_guardrail_active = False
            self.state.auto_trade_guardrail_reason = ""
            return 1.0, ""

        wins = sum(1 for row in realized if (safe_float(row.get("pnl_usdt")) or 0.0) >= 0)
        trade_count = len(realized)
        win_rate = wins / trade_count if trade_count > 0 else 0.0
        avg_pnl = sum(safe_float(row.get("pnl_usdt")) or 0.0 for row in realized) / trade_count

        baseline_win_rate = self.settings.auto_trade_forward_baseline_win_rate
        baseline_avg_pnl = self.settings.auto_trade_forward_baseline_avg_pnl_usdt
        underperform = (
            win_rate < baseline_win_rate
            or avg_pnl < baseline_avg_pnl
        )
        severe = (
            win_rate < self.settings.auto_trade_forward_guardrail_severe_win_rate
            and trade_count >= max(5, self.settings.auto_trade_forward_guardrail_min_trades // 2)
        )

        if severe and self.settings.auto_trade_forward_guardrail_halt_enabled:
            self.state.auto_trade_halt_until = max(
                self.state.auto_trade_halt_until,
                now_ts + self.settings.auto_trade_kill_switch_pause_seconds,
            )
            self.state.auto_trade_halt_reason = (
                f"Forward guardrail severe underperformance (win rate {win_rate:.0%})"
            )

        if underperform:
            reason = (
                f"Forward guardrail active: win {win_rate:.0%} / avg pnl {avg_pnl:.2f}"
            )
            self.state.auto_trade_guardrail_active = True
            self.state.auto_trade_guardrail_reason = reason
            return self.settings.auto_trade_forward_guardrail_risk_mult, reason

        self.state.auto_trade_guardrail_active = False
        self.state.auto_trade_guardrail_reason = ""
        return 1.0, ""

    def _volatility_size_multiplier(self, row: dict[str, Any]) -> float:
        if not self.settings.auto_trade_volatility_sizing_enabled:
            return 1.0

        atr_pct = safe_float(row.get("atr_pct"))
        if atr_pct is None or atr_pct <= 0:
            return 1.0

        raw = self.settings.auto_trade_target_atr_pct / atr_pct
        return self._clamp(
            raw,
            self.settings.auto_trade_volatility_size_min_mult,
            self.settings.auto_trade_volatility_size_max_mult,
        )

    def _is_extreme_volatility(self, row: dict[str, Any]) -> tuple[bool, str]:
        if not self.settings.auto_trade_extreme_volatility_block_enabled:
            return False, ""

        atr_pct = safe_float(row.get("atr_pct"))
        if atr_pct is not None and atr_pct > self.settings.auto_trade_max_atr_pct:
            return True, f"ATR {atr_pct:.2f}% > {self.settings.auto_trade_max_atr_pct:.2f}%"

        change_24h = safe_float(row.get("change_24h"))
        if (
            change_24h is not None
            and abs(change_24h) > self.settings.auto_trade_max_abs_change_24h_pct
        ):
            return (
                True,
                (
                    f"24h change {change_24h:.2f}% exceeds "
                    f"{self.settings.auto_trade_max_abs_change_24h_pct:.2f}%"
                ),
            )
        return False, ""

    def _adaptive_cooldown_multiplier(self, row: dict[str, Any]) -> float:
        if not self.settings.auto_trade_cooldown_adaptive_enabled:
            return 1.0

        loss_mult = 1.0 + (
            self.state.auto_trade_consecutive_losses
            * self.settings.auto_trade_cooldown_loss_step_mult
        )

        atr_pct = safe_float(row.get("atr_pct"))
        if atr_pct is None or atr_pct <= 0:
            vol_mult = 1.0
        else:
            vol_mult = atr_pct / self.settings.auto_trade_cooldown_volatility_target_pct
            vol_mult = self._clamp(vol_mult, 0.8, self.settings.auto_trade_cooldown_volatility_max_mult)

        return self._clamp(loss_mult * vol_mult, 0.8, self.settings.auto_trade_cooldown_volatility_max_mult)

    def push_auto_trade_event(
        self,
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
        self.state.auto_trade_counter += 1
        self.state.auto_trade_events.append(
            {
                "id": self.state.auto_trade_counter,
                "timestamp": int(time.time()),
                "symbol": symbol,
                "event_type": event_type,
                "action": action,
                "message": message,
                "price": round(price, 6) if price is not None else None,
                "amount": round(amount, 8) if amount is not None else None,
                "pnl_usdt": round(pnl_usdt, 4) if pnl_usdt is not None else None,
                "mode": mode or ("paper" if self.settings.paper_trading else "live"),
                "success": success,
            }
        )

    def execute_auto_trade_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        reference_price: float,
    ) -> dict[str, Any]:
        if self.settings.paper_trading:
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
                trade_amount = float(self.exchange.call("amount_to_precision", symbol, amount))
            if trade_amount <= 0:
                return {"ok": False, "error": "Computed amount is zero after precision rounding"}

            try:
                order = self.exchange.call(
                    "create_order",
                    symbol,
                    "market",
                    side,
                    trade_amount,
                )
            except Exception as exc:  # noqa: BLE001
                if (
                    side == "buy"
                    and "createMarketBuyOrderRequiresPrice" in str(exc)
                    and reference_price > 0
                ):
                    order = self.exchange.call(
                        "create_order",
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
            self.logger.exception("Auto trade order failed for %s side=%s", symbol, side)
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _normalize_side(value: Any) -> str:
        side = str(value or "LONG").strip().upper()
        return side if side in {"LONG", "SHORT"} else "LONG"

    def _strategy_allows(self, side: str) -> bool:
        mode = self.settings.auto_trade_strategy_mode
        if mode == "long_only":
            return side == "LONG"
        if mode == "short_only":
            return side == "SHORT"
        return side in {"LONG", "SHORT"}

    def _rule_allows_side(self, row: dict[str, Any], side: str) -> tuple[bool, str]:
        price = safe_float(row.get("price"))
        ema20 = safe_float(row.get("ema20"))
        ema50 = safe_float(row.get("ema50"))
        rsi = safe_float(row.get("rsi"))
        macd = safe_float(row.get("macd"))
        macd_signal = safe_float(row.get("macd_signal"))
        volume_ratio = safe_float(row.get("volume_ratio"))
        strength_confidence = int(safe_float(row.get("strength_confidence")) or 0)

        if price is None or ema50 is None or ema20 is None or rsi is None:
            return False, "waiting price/EMA20/EMA50/RSI"

        if strength_confidence < self.settings.auto_trade_min_strength_confidence:
            return (
                False,
                (
                    f"strength confidence {strength_confidence}% < "
                    f"{self.settings.auto_trade_min_strength_confidence}%"
                ),
            )

        if (
            self.settings.auto_trade_min_volume_ratio > 0
            and volume_ratio is not None
            and volume_ratio < self.settings.auto_trade_min_volume_ratio
        ):
            return (
                False,
                (
                    f"volume ratio {volume_ratio:.2f} < "
                    f"{self.settings.auto_trade_min_volume_ratio:.2f}"
                ),
            )

        if side == "LONG":
            if price <= ema50:
                return False, "LONG rule: price must be > MA50"
            if self.settings.auto_trade_entry_confirm_ema_stack and ema20 <= ema50:
                return False, "LONG rule: EMA20 must be > EMA50"
            if (
                self.settings.auto_trade_entry_confirm_macd
                and macd is not None
                and macd_signal is not None
                and macd < macd_signal
            ):
                return False, "LONG rule: MACD must be above signal"
            if rsi < self.settings.long_rsi_min or rsi > self.settings.long_rsi_max:
                return (
                    False,
                    (
                        f"LONG rule: RSI {rsi:.2f} outside "
                        f"{self.settings.long_rsi_min:.0f}-{self.settings.long_rsi_max:.0f}"
                    ),
                )
            return True, ""

        if price >= ema50:
            return False, "SHORT rule: price must be < MA50"
        if self.settings.auto_trade_entry_confirm_ema_stack and ema20 >= ema50:
            return False, "SHORT rule: EMA20 must be < EMA50"
        if (
            self.settings.auto_trade_entry_confirm_macd
            and macd is not None
            and macd_signal is not None
            and macd > macd_signal
        ):
            return False, "SHORT rule: MACD must be below signal"
        if rsi < self.settings.short_rsi_min or rsi > self.settings.short_rsi_max:
            return (
                False,
                (
                    f"SHORT rule: RSI {rsi:.2f} outside "
                    f"{self.settings.short_rsi_min:.0f}-{self.settings.short_rsi_max:.0f}"
                ),
            )
        return True, ""

    def _ai_filter_allows(self, row: dict[str, Any], side: str) -> tuple[bool, str]:
        if not self.settings.ai_filter_enabled:
            return True, ""

        ai_bias = str(row.get("ai_bias") or "HOLD").upper()
        ai_confidence = int(safe_float(row.get("ai_confidence")) or 0)
        ai_score = float(safe_float(row.get("ai_score")) or 0.0)

        if ai_confidence < self.settings.ai_filter_min_confidence:
            return (
                False,
                (
                    f"AI confidence {ai_confidence}% < "
                    f"{self.settings.ai_filter_min_confidence}%"
                ),
            )

        if abs(ai_score) < self.settings.ai_filter_min_score_abs:
            return (
                False,
                (
                    f"AI score abs {abs(ai_score):.2f} < "
                    f"{self.settings.ai_filter_min_score_abs:.2f}"
                ),
            )

        if side == "LONG" and ai_bias != "BUY":
            return False, f"AI bias is {ai_bias}, not BUY"
        if side == "SHORT" and ai_bias != "SELL":
            return False, f"AI bias is {ai_bias}, not SELL"

        return True, ""

    def decide_entry_side(self, row: dict[str, Any]) -> tuple[str | None, str]:
        reasons: list[str] = []
        candidates: list[str] = []

        if self._strategy_allows("LONG"):
            ok, reason = self._rule_allows_side(row, "LONG")
            if ok:
                candidates.append("LONG")
            elif reason:
                reasons.append(reason)

        if self.settings.auto_trade_enable_short and self._strategy_allows("SHORT"):
            ok, reason = self._rule_allows_side(row, "SHORT")
            if ok:
                candidates.append("SHORT")
            elif reason:
                reasons.append(reason)

        if not candidates:
            return None, (reasons[0] if reasons else "waiting LONG/SHORT entry rules")

        if len(candidates) == 1:
            return candidates[0], ""

        ai_score = safe_float(row.get("ai_score")) or 0.0
        if ai_score < 0:
            return "SHORT", ""
        return "LONG", ""

    def _side_stop_take_trail(self, side: str) -> tuple[float, float, float]:
        if side == "SHORT":
            return (
                self.settings.short_stop_loss_pct,
                self.settings.short_take_profit_pct,
                self.settings.short_trailing_pct,
            )
        return (
            self.settings.long_stop_loss_pct,
            self.settings.long_take_profit_pct,
            self.settings.long_trailing_pct,
        )

    def get_position_exit_reason(
        self,
        row: dict[str, Any],
        position: dict[str, Any],
    ) -> tuple[str | None, float]:
        entry_price = safe_float(position.get("entry_price")) or 0.0
        current_price = safe_float(row.get("price"))
        if current_price is None or entry_price <= 0:
            return None, 0.0

        position_side = self._normalize_side(position.get("side"))
        stop_loss_pct, take_profit_pct, trailing_pct = self._side_stop_take_trail(position_side)

        if position_side == "SHORT":
            pnl_pct = ((entry_price - current_price) / entry_price) * 100
        else:
            pnl_pct = ((current_price - entry_price) / entry_price) * 100

        if (
            self.settings.auto_trade_break_even_enabled
            and pnl_pct >= self.settings.auto_trade_break_even_trigger_pct
            and not bool(position.get("break_even_armed"))
        ):
            position["break_even_armed"] = True

        if pnl_pct <= -stop_loss_pct:
            return "STOP LOSS", pnl_pct
        if pnl_pct >= take_profit_pct:
            return "TAKE PROFIT", pnl_pct
        if (
            self.settings.auto_trade_break_even_enabled
            and bool(position.get("break_even_armed"))
            and pnl_pct <= self.settings.auto_trade_break_even_buffer_pct
        ):
            return "BREAK EVEN EXIT", pnl_pct

        if trailing_pct > 0:
            if position_side == "SHORT":
                lowest_price = safe_float(position.get("lowest_price")) or entry_price
                if current_price < lowest_price:
                    lowest_price = current_price
                    position["lowest_price"] = lowest_price

                if lowest_price < entry_price:
                    trailing_stop = lowest_price * (1 + trailing_pct / 100)
                    if current_price >= trailing_stop:
                        return "TRAILING STOP", pnl_pct
            else:
                highest_price = safe_float(position.get("highest_price")) or entry_price
                if current_price > highest_price:
                    highest_price = current_price
                    position["highest_price"] = highest_price

                if highest_price > entry_price:
                    trailing_stop = highest_price * (1 - trailing_pct / 100)
                    if current_price <= trailing_stop:
                        return "TRAILING STOP", pnl_pct

        signal = str(row.get("signal") or "HOLD").upper()
        strength = str(row.get("strength") or "HOLD").upper()
        if position_side == "SHORT":
            if signal == "BUY" or strength in {"BUY", "STRONG BUY"}:
                return "SIGNAL EXIT", pnl_pct
        else:
            if signal == "SELL" or strength in {"SELL", "STRONG SELL"}:
                return "SIGNAL EXIT", pnl_pct

        return None, pnl_pct

    @staticmethod
    def _entry_order_side(position_side: str) -> str:
        return "sell" if position_side == "SHORT" else "buy"

    @staticmethod
    def _exit_order_side(position_side: str) -> str:
        return "buy" if position_side == "SHORT" else "sell"

    @staticmethod
    def _pnl_usdt(position_side: str, entry_price: float, current_price: float, amount: float) -> float:
        if position_side == "SHORT":
            return (entry_price - current_price) * amount
        return (current_price - entry_price) * amount

    def _next_cooldown_seconds(self, row: dict[str, Any] | None = None) -> int:
        low = max(1, self.settings.cooldown_min_seconds)
        high = max(low, self.settings.cooldown_max_seconds)
        if low == high:
            base_value = low
        else:
            base_value = random.randint(low, high)

        multiplier = 1.0
        if row is not None:
            multiplier = self._adaptive_cooldown_multiplier(row)
        return max(1, int(round(base_value * multiplier)))

    def _base_notional_usdt(
        self,
        available_usdt: float | None,
        row: dict[str, Any],
        risk_multiplier: float = 1.0,
    ) -> float:
        notional_usdt = self.settings.trade_size_usdt

        if (
            self.settings.trade_size_percent > 0
            and available_usdt is not None
            and available_usdt > 0
        ):
            notional_usdt = available_usdt * (self.settings.trade_size_percent / 100)

        notional_usdt *= self._volatility_size_multiplier(row)
        notional_usdt *= max(0.05, risk_multiplier)
        notional_usdt = max(notional_usdt, self.settings.trade_size_usdt_min)
        notional_usdt = min(notional_usdt, self.settings.trade_size_usdt_max)
        return notional_usdt

    def get_symbol_min_notional_usdt(self, symbol: str) -> float:
        try:
            if not self.state.markets_loaded:
                self.exchange.call("load_markets")
                self.state.markets_loaded = True

            market = self.exchange.call("market", symbol)
            limits = market.get("limits", {}) if isinstance(market, dict) else {}
            cost_limits = limits.get("cost", {}) if isinstance(limits, dict) else {}
            min_cost = (
                safe_float(cost_limits.get("min"))
                if isinstance(cost_limits, dict)
                else None
            )
            if min_cost is not None and min_cost > 0:
                return min_cost
        except Exception:  # noqa: BLE001
            self.logger.warning(
                "Could not determine exchange min notional for %s, fallback to configured minimum",
                symbol,
            )

        return self.settings.auto_trade_min_notional_usdt

    def run_auto_trading(
        self,
        market_rows: list[dict[str, Any]],
        wallet_payload: dict[str, Any] | None = None,
    ) -> None:
        if not self.settings.auto_trade_enabled:
            self.state.auto_trade_last_reason = "Auto-trade is disabled"
            return
        self._ensure_symbol_whitelist()

        now = time.time()
        eval_interval = max(1.0, self.settings.push_interval_seconds * 0.8)

        with self.state.auto_trade_lock:
            if now - self.state.auto_trade_last_eval_at < eval_interval:
                return
            self.state.auto_trade_last_eval_at = now

            available_usdt = safe_float((wallet_payload or {}).get("usdt_free"))
            status_reason = "Waiting for valid trade setup"
            if self.state.auto_trade_halt_until > now:
                remaining = int(self.state.auto_trade_halt_until - now)
                self.state.auto_trade_last_reason = (
                    f"Risk halt ({max(0, remaining)}s): "
                    f"{self.state.auto_trade_halt_reason or 'cooldown risk pause'}"
                )
                return

            day_key = self.utc_day_key()
            if day_key not in self.state.auto_trade_daily_pnl:
                self.state.auto_trade_daily_pnl[day_key] = 0.0
            if self.state.auto_trade_halt_day and self.state.auto_trade_halt_day != day_key:
                self.state.auto_trade_halt_day = None

            rows_by_symbol = {
                str(row.get("symbol")): row for row in market_rows if row.get("symbol")
            }
            forward_risk_multiplier, forward_reason = self._compute_forward_guardrail(now)
            self.state.auto_trade_last_risk_multiplier = forward_risk_multiplier

            for symbol, position in list(self.state.auto_trade_positions.items()):
                row = rows_by_symbol.get(symbol)
                if not row:
                    continue

                amount = safe_float(position.get("amount")) or 0.0
                current_price = safe_float(row.get("price"))
                position_side = self._normalize_side(position.get("side"))
                entry_price = safe_float(position.get("entry_price")) or 0.0

                if amount <= 0 or current_price is None:
                    continue

                pnl_pct_live = self._pnl_pct(entry_price, current_price, position_side)
                if (
                    self.settings.auto_trade_partial_take_profit_enabled
                    and not bool(position.get("partial_tp_done"))
                    and pnl_pct_live >= self.settings.auto_trade_partial_take_profit_pct
                    and amount > 0
                ):
                    partial_amount = amount * self.settings.auto_trade_partial_take_profit_ratio
                    exit_side = self._exit_order_side(position_side)
                    partial_result = self.execute_auto_trade_order(
                        symbol,
                        exit_side,
                        partial_amount,
                        current_price,
                    )
                    if partial_result.get("ok"):
                        realized_amount = safe_float(partial_result.get("filled")) or partial_amount
                        realized_pnl = self._pnl_usdt(
                            position_side,
                            entry_price,
                            current_price,
                            realized_amount,
                        )
                        self.state.auto_trade_daily_pnl[day_key] += realized_pnl
                        position["amount"] = max(0.0, amount - realized_amount)
                        position["partial_tp_done"] = True
                        self.state.auto_trade_last_action_ts[symbol] = now
                        self.state.auto_trade_cooldown_until[symbol] = (
                            now + self._next_cooldown_seconds(row)
                        )
                        self._record_journal(
                            symbol=symbol,
                            event_type="PARTIAL_EXIT",
                            side=position_side,
                            reason="PARTIAL TAKE PROFIT",
                            pnl_usdt=realized_pnl,
                            pnl_pct=pnl_pct_live,
                            notional_usdt=safe_float(position.get("notional_usdt")),
                            price=current_price,
                            amount=realized_amount,
                            metadata={"remaining_amount": round(position["amount"], 8)},
                        )
                        self.push_auto_trade_event(
                            symbol,
                            "PARTIAL_EXIT",
                            f"{exit_side.upper()} ({position_side} PARTIAL)",
                            f"Partial TP hit • PnL {realized_pnl:.2f} USDT",
                            price=current_price,
                            amount=realized_amount,
                            pnl_usdt=realized_pnl,
                            mode=str(partial_result.get("mode") or "live"),
                        )
                        self.alerts.emit_alert(
                            symbol=symbol,
                            alert_type="auto_trade_partial_exit",
                            title=f"{symbol} auto {position_side} PARTIAL EXIT",
                            message=f"Partial TP • PnL {realized_pnl:.2f} USDT",
                            severity="medium",
                        )
                        status_reason = f"{symbol}: partial TP executed"
                        if position["amount"] <= 0:
                            self.state.auto_trade_positions.pop(symbol, None)
                        continue

                reason, _ = self.get_position_exit_reason(row, position)
                if not reason:
                    status_reason = (
                        f"{symbol}: {position_side} open, monitoring exit conditions"
                    )
                    continue

                exit_side = self._exit_order_side(position_side)
                order_result = self.execute_auto_trade_order(symbol, exit_side, amount, current_price)
                if not order_result.get("ok"):
                    error_message = str(order_result.get("error") or "Unknown exit error")
                    status_reason = f"{symbol}: EXIT failed ({error_message[:80]})"
                    self.push_auto_trade_event(
                        symbol,
                        "EXIT_FAILED",
                        f"{exit_side.upper()} ({position_side} EXIT)",
                        error_message,
                        price=current_price,
                        amount=amount,
                        success=False,
                    )
                    self.alerts.emit_alert(
                        symbol=symbol,
                        alert_type="auto_trade_error",
                        title=f"{symbol} auto-trade EXIT failed",
                        message=error_message[:140],
                        severity="high",
                    )
                    continue

                entry_price = safe_float(position.get("entry_price")) or current_price
                filled_amount = safe_float(order_result.get("filled")) or amount
                pnl_usdt = self._pnl_usdt(position_side, entry_price, current_price, filled_amount)
                self.state.auto_trade_daily_pnl[day_key] += pnl_usdt
                self.state.auto_trade_last_action_ts[symbol] = now
                self.state.auto_trade_cooldown_until[symbol] = now + self._next_cooldown_seconds(row)
                self.state.auto_trade_positions.pop(symbol, None)
                status_reason = f"{symbol}: {position_side} closed ({reason})"
                self._update_symbol_stats(symbol, pnl_usdt)
                self._record_journal(
                    symbol=symbol,
                    event_type="EXIT",
                    side=position_side,
                    reason=reason,
                    pnl_usdt=pnl_usdt,
                    pnl_pct=self._pnl_pct(entry_price, current_price, position_side),
                    notional_usdt=safe_float(position.get("notional_usdt")),
                    price=current_price,
                    amount=filled_amount,
                )

                self.push_auto_trade_event(
                    symbol,
                    "EXIT",
                    f"{exit_side.upper()} ({position_side} EXIT)",
                    f"{reason} • PnL {pnl_usdt:.2f} USDT",
                    price=current_price,
                    amount=filled_amount,
                    pnl_usdt=pnl_usdt,
                    mode=str(order_result.get("mode") or "live"),
                )
                self.alerts.emit_alert(
                    symbol=symbol,
                    alert_type="auto_trade_exit",
                    title=f"{symbol} auto {position_side} EXIT executed",
                    message=f"{reason} • PnL {pnl_usdt:.2f} USDT",
                    severity=("medium" if pnl_usdt >= 0 else "high"),
                )

                if (
                    pnl_usdt < 0
                    and self.state.auto_trade_consecutive_losses
                    >= self.settings.auto_trade_max_consecutive_losses
                ):
                    self.state.auto_trade_halt_until = max(
                        self.state.auto_trade_halt_until,
                        now + self.settings.auto_trade_kill_switch_pause_seconds,
                    )
                    self.state.auto_trade_halt_reason = (
                        f"Consecutive losses reached {self.state.auto_trade_consecutive_losses}"
                    )
                    self.alerts.emit_alert(
                        symbol=symbol,
                        alert_type="auto_trade_kill_switch",
                        title="Auto-trade kill switch activated",
                        message=(
                            f"Paused for {self.settings.auto_trade_kill_switch_pause_seconds}s "
                            f"after {self.state.auto_trade_consecutive_losses} losses"
                        ),
                        severity="high",
                    )

            daily_pnl = self.state.auto_trade_daily_pnl.get(day_key, 0.0)
            risk_halted = daily_pnl <= -self.settings.max_daily_loss_usdt

            if risk_halted:
                if self.state.auto_trade_halt_day != day_key:
                    self.state.auto_trade_halt_day = day_key
                    self.alerts.emit_alert(
                        symbol=self.settings.default_symbol,
                        alert_type="auto_trade_halt",
                        title="Auto-trade halted by daily risk limit",
                        message=(
                            f"Daily PnL reached {daily_pnl:.2f} USDT "
                            f"(limit -{self.settings.max_daily_loss_usdt:.2f})"
                        ),
                        severity="high",
                    )
                self.state.auto_trade_last_reason = (
                    f"Risk halt active: daily PnL {daily_pnl:.2f} USDT "
                    f"(limit -{self.settings.max_daily_loss_usdt:.2f})"
                )
                return

            self.state.auto_trade_halt_day = None
            if not self._session_allows_entry(now):
                self.state.auto_trade_last_reason = (
                    "Session filter active: outside allowed UTC windows "
                    f"({self.settings.auto_trade_session_utc})"
                )
                return

            for row in market_rows:
                symbol = str(row.get("symbol") or "")
                if not symbol or symbol not in self._runtime_auto_trade_symbols_set:
                    continue

                if row.get("error"):
                    status_reason = f"{symbol}: market data error"
                    continue

                blocked, blocked_reason = self._is_extreme_volatility(row)
                if blocked:
                    status_reason = f"{symbol}: volatility filter ({blocked_reason})"
                    continue

                if symbol in self.state.auto_trade_positions:
                    status_reason = f"{symbol}: position already open"
                    continue

                if len(self.state.auto_trade_positions) >= self.settings.auto_trade_max_open_positions:
                    status_reason = (
                        "max open positions reached "
                        f"({len(self.state.auto_trade_positions)}/"
                        f"{self.settings.auto_trade_max_open_positions})"
                    )
                    break

                cooldown_until = self.state.auto_trade_cooldown_until.get(symbol, 0.0)
                if now < cooldown_until:
                    remaining = int(cooldown_until - now)
                    status_reason = f"{symbol}: cooldown {max(0, remaining)}s"
                    continue

                current_price = safe_float(row.get("price"))
                if current_price is None or current_price <= 0:
                    status_reason = f"{symbol}: waiting valid price"
                    continue

                entry_side, reason_text = self.decide_entry_side(row)
                if not entry_side:
                    status_reason = f"{symbol}: {reason_text}"
                    continue

                ai_ok, ai_reason = self._ai_filter_allows(row, entry_side)
                if not ai_ok:
                    status_reason = f"{symbol}: AI filter blocked ({ai_reason})"
                    continue

                if entry_side == "SHORT" and not self.settings.paper_trading:
                    status_reason = (
                        f"{symbol}: short entry blocked in LIVE spot mode "
                        "(use PAPER or derivatives integration)"
                    )
                    continue

                notional_usdt = self._base_notional_usdt(
                    available_usdt,
                    row,
                    risk_multiplier=forward_risk_multiplier,
                )

                min_notional_usdt = self.get_symbol_min_notional_usdt(symbol)
                required_min_notional = min_notional_usdt * (
                    1 + self.settings.auto_trade_min_buffer_pct / 100
                )
                notional_usdt = max(notional_usdt, required_min_notional)
                if available_usdt is not None:
                    notional_usdt = min(notional_usdt, available_usdt)

                if notional_usdt <= 0 or notional_usdt < required_min_notional:
                    status_reason = (
                        f"{symbol}: insufficient USDT for min notional "
                        f"{required_min_notional:.2f}"
                    )
                    continue

                amount = notional_usdt / current_price
                exchange_side = self._entry_order_side(entry_side)
                order_result = self.execute_auto_trade_order(
                    symbol,
                    exchange_side,
                    amount,
                    current_price,
                )
                if not order_result.get("ok"):
                    error_message = str(order_result.get("error") or "Unknown entry error")
                    status_reason = f"{symbol}: ENTRY failed ({error_message[:80]})"
                    self.push_auto_trade_event(
                        symbol,
                        "ENTRY_FAILED",
                        f"{exchange_side.upper()} ({entry_side})",
                        error_message,
                        price=current_price,
                        amount=amount,
                        success=False,
                    )
                    self.alerts.emit_alert(
                        symbol=symbol,
                        alert_type="auto_trade_error",
                        title=f"{symbol} auto-trade ENTRY failed",
                        message=error_message[:140],
                        severity="high",
                    )
                    self._record_journal(
                        symbol=symbol,
                        event_type="ENTRY_FAILED",
                        side=entry_side,
                        reason=error_message[:180],
                        pnl_usdt=None,
                        pnl_pct=None,
                        notional_usdt=notional_usdt,
                        price=current_price,
                        amount=amount,
                    )
                    continue

                filled_amount = safe_float(order_result.get("filled")) or amount
                entry_price = safe_float(order_result.get("average")) or current_price

                self.state.auto_trade_positions[symbol] = {
                    "side": entry_side,
                    "entry_price": entry_price,
                    "amount": filled_amount,
                    "initial_amount": filled_amount,
                    "notional_usdt": notional_usdt,
                    "opened_at": int(now),
                    "mode": str(order_result.get("mode") or "live"),
                    "highest_price": entry_price,
                    "lowest_price": entry_price,
                    "partial_tp_done": False,
                    "break_even_armed": False,
                }
                self.state.auto_trade_last_action_ts[symbol] = now
                self.state.auto_trade_cooldown_until[symbol] = now + self._next_cooldown_seconds(row)
                if available_usdt is not None:
                    available_usdt = max(0.0, available_usdt - notional_usdt)
                status_reason = f"{symbol}: {entry_side} opened at {entry_price:.6f}"
                if forward_reason:
                    status_reason = f"{status_reason} • {forward_reason}"

                self.push_auto_trade_event(
                    symbol,
                    "ENTRY",
                    f"{exchange_side.upper()} ({entry_side})",
                    (
                        f"Rule {entry_side} passed • RSI {row.get('rsi')} • "
                        f"AI {row.get('ai_bias')} ({row.get('ai_confidence')}%)"
                    ),
                    price=entry_price,
                    amount=filled_amount,
                    mode=str(order_result.get("mode") or "live"),
                )
                self.alerts.emit_alert(
                    symbol=symbol,
                    alert_type="auto_trade_entry",
                    title=f"{symbol} auto {entry_side} ENTRY executed",
                    message=f"Entry {entry_price:.6f} • size {filled_amount:.6f}",
                    severity="medium",
                )
                self._record_journal(
                    symbol=symbol,
                    event_type="ENTRY",
                    side=entry_side,
                    reason=reason_text or "Rule entry passed",
                    pnl_usdt=None,
                    pnl_pct=None,
                    notional_usdt=notional_usdt,
                    price=entry_price,
                    amount=filled_amount,
                    metadata={
                        "rsi": row.get("rsi"),
                        "atr_pct": row.get("atr_pct"),
                        "volume_ratio": row.get("volume_ratio"),
                        "guardrail_mult": round(forward_risk_multiplier, 4),
                    },
                )

            if forward_reason and "guardrail" not in status_reason.lower():
                status_reason = f"{status_reason} • {forward_reason}"
            self.state.auto_trade_last_reason = status_reason

    def build_auto_trade_status(self, selected_symbol: str) -> dict[str, Any]:
        with self.state.auto_trade_lock:
            now = time.time()
            day_key = self.utc_day_key()
            daily_pnl = self.state.auto_trade_daily_pnl.get(day_key, 0.0)
            position = self.state.auto_trade_positions.get(selected_symbol)

            return {
                "enabled": self.settings.auto_trade_enabled,
                "paper_trading": self.settings.paper_trading,
                "exchange": self.settings.exchange_name,
                "symbols": self._runtime_auto_trade_symbols,
                "trade_size_usdt": self.settings.trade_size_usdt,
                "trade_size_usdt_min": round(self.settings.trade_size_usdt_min, 4),
                "trade_size_usdt_max": round(self.settings.trade_size_usdt_max, 4),
                "trade_size_percent": round(self.settings.trade_size_percent, 4),
                "risk_multiplier": round(self.state.auto_trade_last_risk_multiplier, 4),
                "guardrail_active": self.state.auto_trade_guardrail_active,
                "guardrail_reason": self.state.auto_trade_guardrail_reason,
                "min_notional_usdt": round(self.settings.auto_trade_min_notional_usdt, 4),
                "min_buffer_pct": round(self.settings.auto_trade_min_buffer_pct, 4),
                "strategy_mode": self.settings.auto_trade_strategy_mode,
                "short_enabled": self.settings.auto_trade_enable_short,
                "max_open_positions": self.settings.auto_trade_max_open_positions,
                "long_rsi_min": round(self.settings.long_rsi_min, 2),
                "long_rsi_max": round(self.settings.long_rsi_max, 2),
                "short_rsi_min": round(self.settings.short_rsi_min, 2),
                "short_rsi_max": round(self.settings.short_rsi_max, 2),
                "long_stop_loss_pct": round(self.settings.long_stop_loss_pct, 2),
                "long_take_profit_pct": round(self.settings.long_take_profit_pct, 2),
                "long_trailing_pct": round(self.settings.long_trailing_pct, 2),
                "short_stop_loss_pct": round(self.settings.short_stop_loss_pct, 2),
                "short_take_profit_pct": round(self.settings.short_take_profit_pct, 2),
                "short_trailing_pct": round(self.settings.short_trailing_pct, 2),
                "ai_filter_enabled": self.settings.ai_filter_enabled,
                "ai_filter_min_confidence": self.settings.ai_filter_min_confidence,
                "ai_filter_min_score_abs": round(self.settings.ai_filter_min_score_abs, 2),
                "cooldown_seconds": self.settings.cooldown_seconds,
                "cooldown_min_seconds": self.settings.cooldown_min_seconds,
                "cooldown_max_seconds": self.settings.cooldown_max_seconds,
                "daily_pnl_usdt": round(daily_pnl, 4),
                "daily_loss_limit_usdt": round(self.settings.max_daily_loss_usdt, 4),
                "halted": (
                    self.state.auto_trade_halt_day == day_key
                    or self.state.auto_trade_halt_until > now
                ),
                "halt_until": int(self.state.auto_trade_halt_until),
                "halt_reason": self.state.auto_trade_halt_reason,
                "consecutive_losses": self.state.auto_trade_consecutive_losses,
                "open_positions": len(self.state.auto_trade_positions),
                "selected_position": position,
                "recent_events": list(self.state.auto_trade_events)[-12:],
                "recent_journal": list(self.state.auto_trade_journal)[-12:],
                "stats_by_symbol": self.state.auto_trade_stats_by_symbol,
                "last_reason": self.state.auto_trade_last_reason,
            }
