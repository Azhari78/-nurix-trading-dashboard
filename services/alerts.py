from __future__ import annotations

import time
from typing import Any

from services.indicators import get_rsi_zone, safe_float
from services.state import RuntimeState


class AlertService:
    def __init__(self, state: RuntimeState) -> None:
        self.state = state

    def create_alert(
        self,
        symbol: str,
        alert_type: str,
        title: str,
        message: str,
        severity: str,
    ) -> None:
        self.state.alert_counter += 1
        self.state.alert_events.append(
            {
                "id": self.state.alert_counter,
                "timestamp": int(time.time()),
                "symbol": symbol,
                "type": alert_type,
                "title": title,
                "message": message,
                "severity": severity,
            }
        )

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
            self.create_alert(symbol, alert_type, title, message, severity)
