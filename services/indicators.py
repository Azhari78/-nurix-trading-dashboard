from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dominant_fourier_cycle(close: pd.Series, window: int = 64) -> tuple[float, float]:
    values = pd.to_numeric(close, errors="coerce").dropna().tail(window).to_numpy(dtype=float)
    if len(values) < 20:
        return np.nan, np.nan

    values = values - np.linspace(values[0], values[-1], len(values))
    values = values - values.mean()
    if not np.isfinite(values).all() or float(np.std(values)) <= 1e-12:
        return np.nan, np.nan

    magnitudes = np.abs(np.fft.rfft(values))
    frequencies = np.fft.rfftfreq(len(values), d=1.0)
    if len(magnitudes) <= 2:
        return np.nan, np.nan

    magnitudes[0] = 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        periods = 1.0 / frequencies
    valid = np.isfinite(periods) & (periods >= 4.0) & (periods <= len(values) / 2.0)
    if not np.any(valid):
        return np.nan, np.nan

    valid_indices = np.where(valid)[0]
    dominant_index = int(valid_indices[np.argmax(magnitudes[valid])])
    total_power = float(np.sum(magnitudes[valid]))
    if total_power <= 1e-12:
        return np.nan, np.nan

    period = float(periods[dominant_index])
    strength = float(magnitudes[dominant_index] / total_power * 100.0)
    return period, strength


def _hurst_exponent(close: pd.Series, max_lag: int = 20) -> float:
    values = pd.to_numeric(close, errors="coerce").dropna().tail(120).to_numpy(dtype=float)
    if len(values) < max_lag * 2:
        return np.nan

    lags: list[int] = []
    tau: list[float] = []
    for lag in range(2, max_lag + 1):
        diff = values[lag:] - values[:-lag]
        std = float(np.std(diff))
        if std > 1e-12:
            lags.append(lag)
            tau.append(std)

    if len(lags) < 5:
        return np.nan

    slope = float(np.polyfit(np.log(lags), np.log(tau), 1)[0])
    return float(np.clip(slope, 0.0, 1.0))


def add_indicators(df: pd.DataFrame, extra_ema_spans: tuple[int, ...] = ()) -> pd.DataFrame:
    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce")

    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    for span in sorted({int(span) for span in extra_ema_spans if int(span) >= 2}):
        if span in {20, 50}:
            continue
        df[f"ema{span}"] = close.ewm(span=span, adjust=False).mean()

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
    df["atr_pct"] = (df["atr14"] / close.replace(0, np.nan)) * 100.0

    vm_plus = (high - low.shift(1)).abs()
    vm_minus = (low - high.shift(1)).abs()
    tr_sum = true_range.rolling(window=14, min_periods=14).sum().replace(0, np.nan)
    df["vortex_plus"] = vm_plus.rolling(window=14, min_periods=14).sum() / tr_sum
    df["vortex_minus"] = vm_minus.rolling(window=14, min_periods=14).sum() / tr_sum
    df["vortex_diff"] = df["vortex_plus"] - df["vortex_minus"]

    rsi_min = df["rsi"].rolling(window=14, min_periods=14).min()
    rsi_max = df["rsi"].rolling(window=14, min_periods=14).max()
    stoch_rsi = ((df["rsi"] - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)) * 100.0
    df["stoch_rsi_k"] = stoch_rsi.rolling(window=3, min_periods=1).mean()
    df["stoch_rsi_d"] = df["stoch_rsi_k"].rolling(window=3, min_periods=1).mean()

    bp = close - pd.concat([low, prev_close], axis=1).min(axis=1)
    uo_tr = pd.concat([high, prev_close], axis=1).max(axis=1) - pd.concat(
        [low, prev_close],
        axis=1,
    ).min(axis=1)
    avg7 = bp.rolling(window=7, min_periods=7).sum() / uo_tr.rolling(window=7, min_periods=7).sum().replace(0, np.nan)
    avg14 = bp.rolling(window=14, min_periods=14).sum() / uo_tr.rolling(window=14, min_periods=14).sum().replace(0, np.nan)
    avg28 = bp.rolling(window=28, min_periods=28).sum() / uo_tr.rolling(window=28, min_periods=28).sum().replace(0, np.nan)
    df["ultimate_oscillator"] = 100.0 * ((4.0 * avg7) + (2.0 * avg14) + avg28) / 7.0

    typical_price = (high + low + close) / 3.0
    keltner_mid = typical_price.ewm(span=20, adjust=False).mean()
    keltner_range = df["atr14"] * 2.0
    df["keltner_mid"] = keltner_mid
    df["keltner_upper"] = keltner_mid + keltner_range
    df["keltner_lower"] = keltner_mid - keltner_range
    df["keltner_width_pct"] = (keltner_range * 2.0 / close.replace(0, np.nan)) * 100.0
    df["keltner_position"] = (
        (close - df["keltner_lower"])
        / (df["keltner_upper"] - df["keltner_lower"]).replace(0, np.nan)
        * 100.0
    )

    df["fourier_cycle_period"] = np.nan
    df["fourier_cycle_strength"] = np.nan
    df["hurst_exponent"] = np.nan
    if len(df.index) > 0:
        cycle_period, cycle_strength = _dominant_fourier_cycle(close)
        df.at[df.index[-1], "fourier_cycle_period"] = cycle_period
        df.at[df.index[-1], "fourier_cycle_strength"] = cycle_strength
        df.at[df.index[-1], "hurst_exponent"] = _hurst_exponent(close)

    df["volume_sma20"] = volume.rolling(window=20, min_periods=5).mean()
    df["volume_ratio"] = volume / df["volume_sma20"].replace(0, np.nan)

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
