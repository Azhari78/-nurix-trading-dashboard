from __future__ import annotations

import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from services.indicators import get_rsi_zone, safe_float
from services.state import RuntimeState


class AlertService:
    def __init__(
        self,
        state: RuntimeState,
        settings: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.state = state
        self.settings = settings
        self.logger = logger or logging.getLogger(__name__)

    def create_alert(
        self,
        symbol: str,
        alert_type: str,
        title: str,
        message: str,
        severity: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.state.alert_counter += 1
        event = {
            "id": self.state.alert_counter,
            "timestamp": int(time.time()),
            "symbol": symbol,
            "type": alert_type,
            "title": title,
            "message": message,
            "severity": severity,
            "meta": meta or {},
        }
        self.state.alert_events.append(event)
        return event

    def _telegram_ready(self) -> bool:
        if not self.settings:
            return False
        return bool(
            getattr(self.settings, "telegram_enabled", False)
            and getattr(self.settings, "telegram_bot_token", "")
            and getattr(self.settings, "telegram_chat_id", "")
        )

    def _should_send_telegram(self, alert_type: str) -> bool:
        if not self._telegram_ready():
            return False
        if not bool(getattr(self.settings, "telegram_auto_trade_only", True)):
            return True
        return alert_type.startswith("auto_trade_")

    @staticmethod
    def _format_price(value: float) -> str:
        if value <= 0:
            return "-"
        return f"{value:.6f}"

    @staticmethod
    def _format_qty(value: float) -> str:
        if value <= 0:
            return "-"
        if value >= 1:
            return f"{value:.4f}"
        return f"{value:.8f}".rstrip("0").rstrip(".")

    @staticmethod
    def _derive_auto_trade_meta(alert: dict[str, Any]) -> dict[str, Any]:
        meta = dict(alert.get("meta") or {})
        title_upper = str(alert.get("title") or "").upper()
        alert_type = str(alert.get("type") or "")

        if "LONG" in title_upper and not meta.get("position_side"):
            meta["position_side"] = "LONG"
        if "SHORT" in title_upper and not meta.get("position_side"):
            meta["position_side"] = "SHORT"

        if "PARTIAL EXIT" in title_upper and not meta.get("event"):
            meta["event"] = "PARTIAL_EXIT"
        elif "ENTRY" in title_upper and not meta.get("event"):
            meta["event"] = "ENTRY"
        elif "EXIT" in title_upper and not meta.get("event"):
            meta["event"] = "EXIT"

        if not meta.get("event"):
            if alert_type == "auto_trade_entry":
                meta["event"] = "ENTRY"
            elif alert_type == "auto_trade_partial_exit":
                meta["event"] = "PARTIAL_EXIT"
            elif alert_type == "auto_trade_exit":
                meta["event"] = "EXIT"
            elif alert_type == "auto_trade_error":
                meta["event"] = "ERROR"
            elif alert_type == "auto_trade_halt":
                meta["event"] = "HALT"
            elif alert_type == "auto_trade_kill_switch":
                meta["event"] = "KILL_SWITCH"

        event = str(meta.get("event") or "").upper()
        position_side = str(meta.get("position_side") or "").upper()
        if not meta.get("order_side"):
            if event == "ENTRY":
                if position_side == "LONG":
                    meta["order_side"] = "BUY"
                elif position_side == "SHORT":
                    meta["order_side"] = "SELL"
            elif event in {"EXIT", "PARTIAL_EXIT"}:
                if position_side == "LONG":
                    meta["order_side"] = "SELL"
                elif position_side == "SHORT":
                    meta["order_side"] = "BUY"

        message = str(alert.get("message") or "")
        if "price" not in meta:
            entry_match = re.search(r"Entry\\s+([0-9]+(?:\\.[0-9]+)?)", message, re.IGNORECASE)
            if entry_match:
                with_value = entry_match.group(1)
                try:
                    meta["price"] = float(with_value)
                except ValueError:
                    pass
        if "amount" not in meta:
            qty_match = re.search(r"size\\s+([0-9]+(?:\\.[0-9]+)?)", message, re.IGNORECASE)
            if qty_match:
                with_value = qty_match.group(1)
                try:
                    meta["amount"] = float(with_value)
                except ValueError:
                    pass
        if "pnl_usdt" not in meta:
            pnl_match = re.search(r"PnL\\s+(-?[0-9]+(?:\\.[0-9]+)?)\\s*USDT", message, re.IGNORECASE)
            if pnl_match:
                with_value = pnl_match.group(1)
                try:
                    meta["pnl_usdt"] = float(with_value)
                except ValueError:
                    pass
        return meta

    def _format_telegram_message(self, alert: dict[str, Any]) -> str:
        raw_timestamp = int(alert.get("timestamp") or 0)
        timestamp = datetime.fromtimestamp(raw_timestamp, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        symbol = str(alert.get("symbol") or "-")
        title = str(alert.get("title") or "Alert")
        alert_type = str(alert.get("type") or "")
        message = str(alert.get("message") or "")
        severity = str(alert.get("severity") or "medium").upper()

        if alert_type.startswith("auto_trade_"):
            meta = self._derive_auto_trade_meta(alert)
            event = str(meta.get("event") or "").upper()
            position_side = str(meta.get("position_side") or "-").upper()
            order_side = str(meta.get("order_side") or "-").upper()
            mode = str(meta.get("mode") or "").upper()
            reason = str(meta.get("reason") or "").strip()
            price = safe_float(meta.get("price"))
            amount = safe_float(meta.get("amount"))
            pnl_usdt = safe_float(meta.get("pnl_usdt"))

            event_label = {
                "ENTRY": "ENTRY OPENED",
                "EXIT": "EXIT CLOSED",
                "PARTIAL_EXIT": "PARTIAL EXIT",
                "ENTRY_FAILED": "ENTRY FAILED",
                "EXIT_FAILED": "EXIT FAILED",
                "ERROR": "ENGINE ERROR",
                "HALT": "RISK HALT",
                "KILL_SWITCH": "KILL SWITCH",
            }.get(event, title.upper())

            event_icon = {
                "ENTRY": "🟢",
                "EXIT": "🔵",
                "PARTIAL_EXIT": "🟦",
                "ENTRY_FAILED": "🟠",
                "EXIT_FAILED": "🟠",
                "ERROR": "🔴",
                "HALT": "⛔",
                "KILL_SWITCH": "🛑",
            }.get(event, "📣")

            order_icon = "🟢" if order_side == "BUY" else "🔴" if order_side == "SELL" else "⚪"

            lines = [
                f"{event_icon} Nurix Auto-Trade {event_label}",
                f"• Pair: {symbol}",
            ]
            if position_side and position_side != "-":
                lines.append(f"• Side: {position_side}")
            if order_side and order_side != "-":
                lines.append(f"• Order: {order_side} {order_icon}")
            if mode:
                lines.append(f"• Mode: {mode}")
            if price is not None and price > 0:
                lines.append(f"• Price: {self._format_price(price)}")
            if amount is not None and amount > 0:
                lines.append(f"• Qty: {self._format_qty(amount)}")
            if pnl_usdt is not None:
                lines.append(f"• PnL: {pnl_usdt:+.2f} USDT")
            if reason:
                lines.append(f"• Reason: {reason}")
            include_info = bool(message)
            if event in {"ENTRY", "EXIT", "PARTIAL_EXIT"}:
                include_info = False
            if include_info:
                lines.append(f"• Info: {message}")
            lines.append(f"• Severity: {severity}")
            lines.append(f"• Time: {timestamp}")
            return "\n".join(lines)

        return (
            f"📣 Nurix Alert\n"
            f"• Symbol: {symbol}\n"
            f"• Type: {title}\n"
            f"• Severity: {severity}\n"
            f"• Message: {message}\n"
            f"• Time: {timestamp}"
        )

    def _send_telegram_message(self, text: str) -> None:
        token = str(getattr(self.settings, "telegram_bot_token", ""))
        chat_id = str(getattr(self.settings, "telegram_chat_id", ""))
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
        request = urllib.request.Request(url=url, data=payload, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=8):
                return
        except Exception as exc:  # noqa: BLE001 - best-effort notifier
            self.logger.warning("Telegram notification failed: %s", exc)

    def _send_telegram_async(self, alert: dict[str, Any]) -> None:
        alert_type = str(alert.get("type") or "")
        if not self._should_send_telegram(alert_type):
            return
        text = self._format_telegram_message(alert)
        thread = threading.Thread(
            target=self._send_telegram_message,
            args=(text,),
            daemon=True,
        )
        thread.start()

    def update_alert_state(self, market_rows: list[dict[str, Any]]) -> None:
        with self.state.alert_lock:
            for row in market_rows:
                symbol = row.get("symbol")
                if not symbol:
                    continue

                signal = row.get("signal")
                rsi = safe_float(row.get("rsi"))
                rsi_zone = get_rsi_zone(rsi)
                ai_bias = str(row.get("ai_bias") or "HOLD").upper()

                previous = self.state.symbol_state.get(symbol, {})
                prev_signal = previous.get("signal")
                prev_zone = previous.get("rsi_zone")
                prev_ai_bias = previous.get("ai_bias")

                if prev_signal and signal and prev_signal != signal:
                    self.create_alert(
                        symbol=symbol,
                        alert_type="signal_flip",
                        title=f"{symbol} signal changed",
                        message=f"{prev_signal} → {signal}",
                        severity=("high" if signal in {"BUY", "SELL"} else "medium"),
                    )

                if prev_zone and rsi_zone != prev_zone:
                    if rsi_zone == "overbought":
                        self.create_alert(
                            symbol=symbol,
                            alert_type="rsi_overbought",
                            title=f"{symbol} RSI overbought",
                            message=f"RSI reached {rsi:.2f}",
                            severity="medium",
                        )
                    elif rsi_zone == "oversold":
                        self.create_alert(
                            symbol=symbol,
                            alert_type="rsi_oversold",
                            title=f"{symbol} RSI oversold",
                            message=f"RSI reached {rsi:.2f}",
                            severity="medium",
                        )

                if prev_ai_bias and ai_bias != prev_ai_bias:
                    self.create_alert(
                        symbol=symbol,
                        alert_type="ai_bias_change",
                        title=f"{symbol} AI filter bias changed",
                        message=f"{prev_ai_bias} → {ai_bias}",
                        severity=("high" if ai_bias in {"BUY", "SELL"} else "medium"),
                    )

                self.state.symbol_state[symbol] = {
                    "signal": signal,
                    "rsi_zone": rsi_zone,
                    "ai_bias": ai_bias,
                }

    def get_recent_alerts(self, limit: int) -> list[dict[str, Any]]:
        with self.state.alert_lock:
            if limit <= 0:
                return []
            return list(self.state.alert_events)[-limit:]

    def emit_alert(
        self,
        symbol: str,
        alert_type: str,
        title: str,
        message: str,
        severity: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        with self.state.alert_lock:
            alert = self.create_alert(symbol, alert_type, title, message, severity, meta=meta)
        self._send_telegram_async(alert)
