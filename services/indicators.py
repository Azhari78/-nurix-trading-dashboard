from __future__ import annotations

from typing import Any

import pandas as pd


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce")

    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()

    rs = avg_gain / avg_loss.replace(0, 1e-10)
    df["rsi"] = 100 - (100 / (1 + rs))

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_histogram"] = df["macd"] - df["macd_signal"]

    prev_close = close.shift(1)
    tr_hl = high - low
    tr_hc = (high - prev_close).abs()
    tr_lc = (low - prev_close).abs()
    true_range = pd.concat([tr_hl, tr_hc, tr_lc], axis=1).max(axis=1)
    df["atr14"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    df["atr_pct"] = (df["atr14"] / close.replace(0, pd.NA)) * 100.0

    df["volume_sma20"] = volume.rolling(window=20, min_periods=5).mean()
    df["volume_ratio"] = volume / df["volume_sma20"].replace(0, pd.NA)

    return df


def get_signal(price: float, rsi: float, ema20: float, ema50: float) -> str:
    if price > ema20 > ema50 and rsi < 70:
        return "BUY"
    if price < ema20 < ema50 and rsi > 30:
        return "SELL"
    return "HOLD"


def get_rsi_zone(rsi: float | None) -> str:
    if rsi is None:
        return "unknown"
    if rsi >= 70:
        return "overbought"
    if rsi <= 30:
        return "oversold"
    return "neutral"


def score_indicator_state(
    price: float,
    rsi: float,
    ema20: float,
    ema50: float,
    macd: float,
    macd_signal: float,
) -> int:
    score = 0

    score += 1 if price >= ema20 else -1
    score += 1 if ema20 >= ema50 else -1
    score += 1 if macd >= macd_signal else -1

    if rsi >= 55:
        score += 1
    elif rsi <= 45:
        score -= 1

    if rsi > 75:
        score -= 1
    elif rsi < 25:
        score += 1

    return score


def classify_strength(average_score: float) -> str:
    if average_score >= 2.5:
        return "STRONG BUY"
    if average_score >= 1.0:
        return "BUY"
    if average_score <= -2.5:
        return "STRONG SELL"
    if average_score <= -1.0:
        return "SELL"
    return "HOLD"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_ai_filter_state(
    *,
    price: float,
    rsi: float,
    ema20: float,
    ema50: float,
    macd: float,
    macd_signal: float,
    strength_score: float,
    change_24h: float | None,
) -> dict[str, float | int | str]:
    score = 0.0

    # Trend regime
    if price >= ema20 >= ema50:
        score += 1.8
    elif price <= ema20 <= ema50:
        score -= 1.8
    elif price >= ema20:
        score += 0.7
    else:
        score -= 0.7

    # Momentum (MACD spread)
    macd_spread = macd - macd_signal
    score += clamp(macd_spread * 8.0, -1.4, 1.4)

    # RSI context
    if rsi >= 72:
        score -= 1.0
    elif rsi <= 28:
        score += 1.0
    elif rsi >= 55:
        score += 0.45
    elif rsi <= 45:
        score -= 0.45

    # Multi-timeframe strength from existing engine
    score += clamp(strength_score / 5.0, -1.0, 1.0)

    # Short-term change moderation
    if change_24h is not None:
        score += clamp(change_24h / 6.0, -0.6, 0.6)

    score = clamp(score, -5.0, 5.0)
    confidence = min(100, int(round(abs(score) / 5.0 * 100)))

    if score >= 1.2:
        bias = "BUY"
    elif score <= -1.2:
        bias = "SELL"
    else:
        bias = "HOLD"

    return {
        "score": round(score, 2),
        "confidence": confidence,
        "bias": bias,
    }
