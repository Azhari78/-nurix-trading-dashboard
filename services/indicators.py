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
