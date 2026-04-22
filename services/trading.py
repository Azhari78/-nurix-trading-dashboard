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

    @staticmethod
    def utc_day_key() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

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
        ema50 = safe_float(row.get("ema50"))
        rsi = safe_float(row.get("rsi"))

        if price is None or ema50 is None or rsi is None:
            return False, "waiting price/EMA50/RSI"

        if side == "LONG":
            if price <= ema50:
                return False, "LONG rule: price must be > MA50"
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

        if pnl_pct <= -stop_loss_pct:
            return "STOP LOSS", pnl_pct
        if pnl_pct >= take_profit_pct:
            return "TAKE PROFIT", pnl_pct

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

    def _next_cooldown_seconds(self) -> int:
        low = max(1, self.settings.cooldown_min_seconds)
        high = max(low, self.settings.cooldown_max_seconds)
        if low == high:
            return low
        return random.randint(low, high)

    def _base_notional_usdt(self, available_usdt: float | None) -> float:
        notional_usdt = self.settings.trade_size_usdt

        if (
            self.settings.trade_size_percent > 0
            and available_usdt is not None
            and available_usdt > 0
        ):
            notional_usdt = available_usdt * (self.settings.trade_size_percent / 100)

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

        now = time.time()
        eval_interval = max(1.0, self.settings.push_interval_seconds * 0.8)

        with self.state.auto_trade_lock:
            if now - self.state.auto_trade_last_eval_at < eval_interval:
                return
            self.state.auto_trade_last_eval_at = now

            available_usdt = safe_float((wallet_payload or {}).get("usdt_free"))
            status_reason = "Waiting for valid trade setup"

            day_key = self.utc_day_key()
            if day_key not in self.state.auto_trade_daily_pnl:
                self.state.auto_trade_daily_pnl[day_key] = 0.0
            if self.state.auto_trade_halt_day and self.state.auto_trade_halt_day != day_key:
                self.state.auto_trade_halt_day = None

            rows_by_symbol = {
                str(row.get("symbol")): row for row in market_rows if row.get("symbol")
            }

            for symbol, position in list(self.state.auto_trade_positions.items()):
                row = rows_by_symbol.get(symbol)
                if not row:
                    continue

                amount = safe_float(position.get("amount")) or 0.0
                current_price = safe_float(row.get("price"))
                position_side = self._normalize_side(position.get("side"))

                if amount <= 0 or current_price is None:
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
                self.state.auto_trade_cooldown_until[symbol] = now + self._next_cooldown_seconds()
                self.state.auto_trade_positions.pop(symbol, None)
                status_reason = f"{symbol}: {position_side} closed ({reason})"

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

            for row in market_rows:
                symbol = str(row.get("symbol") or "")
                if not symbol or symbol not in self.settings.auto_trade_symbols_set:
                    continue

                if row.get("error"):
                    status_reason = f"{symbol}: market data error"
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

                notional_usdt = self._base_notional_usdt(available_usdt)

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
                    continue

                filled_amount = safe_float(order_result.get("filled")) or amount
                entry_price = safe_float(order_result.get("average")) or current_price

                self.state.auto_trade_positions[symbol] = {
                    "side": entry_side,
                    "entry_price": entry_price,
                    "amount": filled_amount,
                    "notional_usdt": notional_usdt,
                    "opened_at": int(now),
                    "mode": str(order_result.get("mode") or "live"),
                    "highest_price": entry_price,
                    "lowest_price": entry_price,
                }
                self.state.auto_trade_last_action_ts[symbol] = now
                self.state.auto_trade_cooldown_until[symbol] = now + self._next_cooldown_seconds()
                if available_usdt is not None:
                    available_usdt = max(0.0, available_usdt - notional_usdt)
                status_reason = f"{symbol}: {entry_side} opened at {entry_price:.6f}"

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

            self.state.auto_trade_last_reason = status_reason

    def build_auto_trade_status(self, selected_symbol: str) -> dict[str, Any]:
        with self.state.auto_trade_lock:
            day_key = self.utc_day_key()
            daily_pnl = self.state.auto_trade_daily_pnl.get(day_key, 0.0)
            position = self.state.auto_trade_positions.get(selected_symbol)

            return {
                "enabled": self.settings.auto_trade_enabled,
                "paper_trading": self.settings.paper_trading,
                "exchange": self.settings.exchange_name,
                "symbols": self.settings.auto_trade_symbols,
                "trade_size_usdt": self.settings.trade_size_usdt,
                "trade_size_usdt_min": round(self.settings.trade_size_usdt_min, 4),
                "trade_size_usdt_max": round(self.settings.trade_size_usdt_max, 4),
                "trade_size_percent": round(self.settings.trade_size_percent, 4),
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
                "halted": (self.state.auto_trade_halt_day == day_key),
                "open_positions": len(self.state.auto_trade_positions),
                "selected_position": position,
                "recent_events": list(self.state.auto_trade_events)[-12:],
                "last_reason": self.state.auto_trade_last_reason,
            }
