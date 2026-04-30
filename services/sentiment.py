from __future__ import annotations

import logging
import math
import re
import time
from typing import Any

from services.indicators import safe_float
from services.state import RuntimeState


POSITIVE_TERMS = {
    "accumulate",
    "adoption",
    "beat",
    "breakout",
    "bull",
    "bullish",
    "buy",
    "growth",
    "long",
    "moon",
    "partnership",
    "pump",
    "rally",
    "recover",
    "reversal",
    "strong",
    "support",
    "upgrade",
}

NEGATIVE_TERMS = {
    "bear",
    "bearish",
    "breakdown",
    "crash",
    "dump",
    "exploit",
    "fear",
    "hack",
    "lawsuit",
    "liquidation",
    "risk",
    "rug",
    "sell",
    "short",
    "weak",
}


class SentimentService:
    def __init__(
        self,
        state: RuntimeState,
        *,
        enabled: bool,
        lookback_minutes: int,
        logger: logging.Logger,
    ) -> None:
        self.state = state
        self.enabled = enabled
        self.lookback_minutes = max(1, int(lookback_minutes))
        self.logger = logger

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def score_text(text: str) -> float:
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_+-]*", str(text or "").lower())
        if not tokens:
            return 0.0

        positive = sum(1 for token in tokens if token in POSITIVE_TERMS)
        negative = sum(1 for token in tokens if token in NEGATIVE_TERMS)
        total = positive + negative
        if total <= 0:
            return 0.0
        return (positive - negative) / total * 100.0

    def ingest(
        self,
        *,
        symbol: str,
        source: str,
        text: str,
        score: float | None = None,
        weight: float = 1.0,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        normalized_symbol = str(symbol or "GLOBAL").strip().upper() or "GLOBAL"
        normalized_source = str(source or "manual").strip().lower()[:40] or "manual"
        text_value = str(text or "").strip()
        scored = safe_float(score)
        if scored is None:
            scored = self.score_text(text_value)
        scored = self._clamp(float(scored), -100.0, 100.0)
        safe_weight = self._clamp(float(safe_float(weight) or 1.0), 0.05, 10.0)
        event_ts = int(timestamp or time.time())

        with self.state.sentiment_lock:
            self.state.sentiment_counter += 1
            event = {
                "id": self.state.sentiment_counter,
                "timestamp": event_ts,
                "symbol": normalized_symbol,
                "source": normalized_source,
                "score": round(scored, 2),
                "weight": round(safe_weight, 4),
                "text": text_value[:240],
            }
            self.state.sentiment_events.append(event)
            self.state.sentiment_by_symbol[normalized_symbol] = self.score_symbol(
                normalized_symbol,
                now_ts=event_ts,
                lock_already_held=True,
            )

        return event

    def score_symbol(
        self,
        symbol: str,
        *,
        now_ts: int | None = None,
        lock_already_held: bool = False,
    ) -> dict[str, Any]:
        now_value = int(now_ts or time.time())
        normalized_symbol = str(symbol or "GLOBAL").strip().upper() or "GLOBAL"
        lookback_seconds = self.lookback_minutes * 60
        min_ts = now_value - lookback_seconds

        def build_score() -> dict[str, Any]:
            events = [
                event
                for event in self.state.sentiment_events
                if int(event.get("timestamp") or 0) >= min_ts
                and str(event.get("symbol") or "").upper() in {normalized_symbol, "GLOBAL"}
            ]
            weighted_score = 0.0
            total_weight = 0.0
            sources: set[str] = set()
            for event in events:
                age_seconds = max(0, now_value - int(event.get("timestamp") or now_value))
                decay = math.exp(-age_seconds / max(60.0, lookback_seconds / 2.0))
                weight = max(0.0, safe_float(event.get("weight")) or 0.0) * decay
                weighted_score += (safe_float(event.get("score")) or 0.0) * weight
                total_weight += weight
                source = str(event.get("source") or "").strip()
                if source:
                    sources.add(source)

            score = weighted_score / total_weight if total_weight > 0 else 0.0
            if score >= 15:
                bias = "BUY"
            elif score <= -15:
                bias = "SELL"
            else:
                bias = "HOLD"

            confidence = min(100, int(round(abs(score) * 0.75 + min(25.0, total_weight * 5.0))))
            latest_ts = max((int(event.get("timestamp") or 0) for event in events), default=None)
            return {
                "enabled": self.enabled,
                "symbol": normalized_symbol,
                "score": round(score, 2),
                "bias": bias,
                "confidence": confidence,
                "source_count": len(sources),
                "event_count": len(events),
                "updated_at": latest_ts,
            }

        if lock_already_held:
            return build_score()

        with self.state.sentiment_lock:
            payload = build_score()
            self.state.sentiment_by_symbol[normalized_symbol] = payload
            return payload
