from __future__ import annotations

import logging
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

    def should_enter_auto_trade(self, row: dict[str, Any]) -> bool:
        signal = str(row.get("signal") or "HOLD").upper()
        strength = str(row.get("strength") or "HOLD").upper()
        confidence = int(safe_float(row.get("strength_confidence")) or 0)

        return (
            signal == "BUY"
            and strength in {"BUY", "STRONG BUY"}
            and confidence >= self.settings.auto_trade_min_confidence
        )

    def get_position_exit_reason(
        self,
        row: dict[str, Any],
        entry_price: float,
    ) -> tuple[str | None, float]:
        current_price = safe_float(row.get("price"))
        if current_price is None or entry_price <= 0:
            return None, 0.0

        pnl_pct = ((current_price - entry_price) / entry_price) * 100
        signal = str(row.get("signal") or "HOLD").upper()
        strength = str(row.get("strength") or "HOLD").upper()

        if pnl_pct <= -self.settings.stop_loss_pct:
            return "STOP LOSS", pnl_pct
        if pnl_pct >= self.settings.take_profit_pct:
            return "TAKE PROFIT", pnl_pct
        if signal == "SELL" or strength in {"SELL", "STRONG SELL"}:
            return "SIGNAL EXIT", pnl_pct
        return None, pnl_pct

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
            status_reason = "Waiting for valid BUY signal"

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

                entry_price = safe_float(position.get("entry_price")) or 0.0
                amount = safe_float(position.get("amount")) or 0.0
                current_price = safe_float(row.get("price"))
                if amount <= 0 or current_price is None:
                    continue

                reason, _ = self.get_position_exit_reason(row, entry_price)
                if not reason:
                    status_reason = f"{symbol}: position open, monitoring exit conditions"
                    continue

                order_result = self.execute_auto_trade_order(symbol, "sell", amount, current_price)
                if not order_result.get("ok"):
                    error_message = str(order_result.get("error") or "Unknown sell error")
                    status_reason = f"{symbol}: SELL failed ({error_message[:80]})"
                    self.push_auto_trade_event(
                        symbol,
                        "EXIT_FAILED",
                        "SELL",
                        error_message,
                        price=current_price,
                        amount=amount,
                        success=False,
                    )
                    self.alerts.emit_alert(
                        symbol=symbol,
                        alert_type="auto_trade_error",
                        title=f"{symbol} auto-trade SELL failed",
                        message=error_message[:140],
                        severity="high",
                    )
                    continue

                filled_amount = safe_float(order_result.get("filled")) or amount
                pnl_usdt = (current_price - entry_price) * filled_amount
                self.state.auto_trade_daily_pnl[day_key] += pnl_usdt
                self.state.auto_trade_last_action_ts[symbol] = now
                self.state.auto_trade_positions.pop(symbol, None)
                status_reason = f"{symbol}: position closed ({reason})"

                self.push_auto_trade_event(
                    symbol,
                    "EXIT",
                    "SELL",
                    f"{reason} • PnL {pnl_usdt:.2f} USDT",
                    price=current_price,
                    amount=filled_amount,
                    pnl_usdt=pnl_usdt,
                    mode=str(order_result.get("mode") or "live"),
                )
                self.alerts.emit_alert(
                    symbol=symbol,
                    alert_type="auto_trade_sell",
                    title=f"{symbol} auto SELL executed",
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

                last_action_ts = self.state.auto_trade_last_action_ts.get(symbol, 0.0)
                if now - last_action_ts < self.settings.cooldown_seconds:
                    remaining = int(self.settings.cooldown_seconds - (now - last_action_ts))
                    status_reason = f"{symbol}: cooldown {max(0, remaining)}s"
                    continue

                current_price = safe_float(row.get("price"))
                if current_price is None or current_price <= 0:
                    status_reason = f"{symbol}: waiting valid price"
                    continue
                if not self.should_enter_auto_trade(row):
                    status_reason = (
                        f"{symbol}: waiting BUY signal/strength/confidence >= "
                        f"{self.settings.auto_trade_min_confidence}%"
                    )
                    continue

                notional_usdt = self.settings.trade_size_usdt
                if (
                    self.settings.trade_size_percent > 0
                    and available_usdt is not None
                    and available_usdt > 0
                ):
                    sized_by_percent = available_usdt * (self.settings.trade_size_percent / 100)
                    notional_usdt = min(sized_by_percent, available_usdt)

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
                order_result = self.execute_auto_trade_order(symbol, "buy", amount, current_price)
                if not order_result.get("ok"):
                    error_message = str(order_result.get("error") or "Unknown buy error")
                    status_reason = f"{symbol}: BUY failed ({error_message[:80]})"
                    self.push_auto_trade_event(
                        symbol,
                        "ENTRY_FAILED",
                        "BUY",
                        error_message,
                        price=current_price,
                        amount=amount,
                        success=False,
                    )
                    self.alerts.emit_alert(
                        symbol=symbol,
                        alert_type="auto_trade_error",
                        title=f"{symbol} auto-trade BUY failed",
                        message=error_message[:140],
                        severity="high",
                    )
                    continue

                filled_amount = safe_float(order_result.get("filled")) or amount
                entry_price = safe_float(order_result.get("average")) or current_price
                self.state.auto_trade_positions[symbol] = {
                    "entry_price": entry_price,
                    "amount": filled_amount,
                    "notional_usdt": notional_usdt,
                    "opened_at": int(now),
                    "mode": str(order_result.get("mode") or "live"),
                }
                self.state.auto_trade_last_action_ts[symbol] = now
                if available_usdt is not None:
                    available_usdt = max(0.0, available_usdt - notional_usdt)
                status_reason = f"{symbol}: BUY opened at {entry_price:.6f}"

                self.push_auto_trade_event(
                    symbol,
                    "ENTRY",
                    "BUY",
                    (
                        f"Auto BUY with strength {row.get('strength')} "
                        f"({row.get('strength_confidence')}%)"
                    ),
                    price=entry_price,
                    amount=filled_amount,
                    mode=str(order_result.get("mode") or "live"),
                )
                self.alerts.emit_alert(
                    symbol=symbol,
                    alert_type="auto_trade_buy",
                    title=f"{symbol} auto BUY executed",
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
                "trade_size_percent": round(self.settings.trade_size_percent, 4),
                "min_notional_usdt": round(self.settings.auto_trade_min_notional_usdt, 4),
                "min_buffer_pct": round(self.settings.auto_trade_min_buffer_pct, 4),
                "min_confidence": self.settings.auto_trade_min_confidence,
                "cooldown_seconds": self.settings.cooldown_seconds,
                "daily_pnl_usdt": round(daily_pnl, 4),
                "daily_loss_limit_usdt": round(self.settings.max_daily_loss_usdt, 4),
                "halted": (self.state.auto_trade_halt_day == day_key),
                "open_positions": len(self.state.auto_trade_positions),
                "selected_position": position,
                "recent_events": list(self.state.auto_trade_events)[-12:],
                "last_reason": self.state.auto_trade_last_reason,
            }
