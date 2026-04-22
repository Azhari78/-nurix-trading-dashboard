from __future__ import annotations

import logging
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

    def _format_telegram_message(self, alert: dict[str, Any]) -> str:
        raw_timestamp = int(alert.get("timestamp") or 0)
        timestamp = datetime.fromtimestamp(raw_timestamp, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
        symbol = str(alert.get("symbol") or "-")
        title = str(alert.get("title") or "Alert")
        message = str(alert.get("message") or "")
        severity = str(alert.get("severity") or "medium").upper()
        return (
            f"Nurix Alert\n"
            f"Symbol: {symbol}\n"
            f"Type: {title}\n"
            f"Severity: {severity}\n"
            f"Message: {message}\n"
            f"Time: {timestamp}"
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
    ) -> None:
        with self.state.alert_lock:
            alert = self.create_alert(symbol, alert_type, title, message, severity)
        self._send_telegram_async(alert)
