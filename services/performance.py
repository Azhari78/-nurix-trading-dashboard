from __future__ import annotations

import math
from typing import Any

from services.indicators import safe_float


def _max_drawdown(values: list[float]) -> tuple[float, float]:
    peak = 0.0
    trough = 0.0
    max_drawdown = 0.0
    equity = 0.0

    for value in values:
        equity += value
        if equity > peak:
            peak = equity
            trough = equity
        if equity < trough:
            trough = equity
        drawdown = peak - trough
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    drawdown_pct = (max_drawdown / peak * 100.0) if peak > 0 else 0.0
    return max_drawdown, drawdown_pct


def _ratio(mean_value: float, denominator: float, scale: float) -> float:
    if denominator <= 1e-12:
        return 0.0
    return mean_value / denominator * scale


def build_performance_analytics(
    journal_rows: list[dict[str, Any]],
    daily_pnl_history: list[dict[str, Any]],
) -> dict[str, Any]:
    realized = [
        row
        for row in journal_rows
        if row.get("event_type") in {"EXIT", "PARTIAL_EXIT"}
        and safe_float(row.get("pnl_usdt")) is not None
    ]
    realized.sort(key=lambda row: int(row.get("timestamp") or 0))

    pnl_values = [float(safe_float(row.get("pnl_usdt")) or 0.0) for row in realized]
    trade_count = len(pnl_values)
    wins = [value for value in pnl_values if value >= 0]
    losses = [value for value in pnl_values if value < 0]
    gross_profit = sum(wins)
    gross_loss_abs = abs(sum(losses))
    win_rate = (len(wins) / trade_count) if trade_count else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (gross_loss_abs / len(losses)) if losses else 0.0
    expectancy = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)
    profit_factor = (gross_profit / gross_loss_abs) if gross_loss_abs > 1e-12 else 0.0
    max_drawdown_usdt, max_drawdown_pct = _max_drawdown(pnl_values)

    trade_returns = []
    for row in realized:
        pnl = safe_float(row.get("pnl_usdt"))
        notional = safe_float(row.get("notional_usdt"))
        if pnl is None or notional is None or notional <= 0:
            continue
        trade_returns.append(pnl / notional)

    if trade_returns:
        mean_return = sum(trade_returns) / len(trade_returns)
        variance = sum((value - mean_return) ** 2 for value in trade_returns) / len(trade_returns)
        std_dev = math.sqrt(variance)
        downside = [min(0.0, value) for value in trade_returns]
        downside_dev = math.sqrt(sum(value * value for value in downside) / len(downside))
        scale = math.sqrt(min(365.0, max(1.0, len(trade_returns))))
        sharpe = _ratio(mean_return, std_dev, scale)
        sortino = _ratio(mean_return, downside_dev, scale)
    else:
        sharpe = 0.0
        sortino = 0.0

    daily_values = [
        float(safe_float(row.get("pnl_usdt")) or 0.0)
        for row in daily_pnl_history
    ]
    daily_drawdown_usdt, daily_drawdown_pct = _max_drawdown(list(reversed(daily_values)))

    by_symbol: dict[str, dict[str, Any]] = {}
    for row in realized:
        symbol = str(row.get("symbol") or "-").upper()
        pnl = float(safe_float(row.get("pnl_usdt")) or 0.0)
        bucket = by_symbol.setdefault(
            symbol,
            {"trades": 0, "wins": 0, "losses": 0, "pnl_usdt": 0.0},
        )
        bucket["trades"] += 1
        bucket["pnl_usdt"] += pnl
        if pnl >= 0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1

    symbol_rows = []
    for symbol, stats in by_symbol.items():
        trades = int(stats["trades"])
        wins_count = int(stats["wins"])
        symbol_rows.append(
            {
                "symbol": symbol,
                "trades": trades,
                "wins": wins_count,
                "losses": int(stats["losses"]),
                "win_rate": round(wins_count / trades, 4) if trades else 0.0,
                "pnl_usdt": round(float(stats["pnl_usdt"]), 4),
            }
        )
    symbol_rows.sort(key=lambda row: float(row.get("pnl_usdt") or 0.0), reverse=True)

    return {
        "trade_count": trade_count,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 4),
        "gross_profit_usdt": round(gross_profit, 4),
        "gross_loss_usdt": round(-gross_loss_abs, 4),
        "profit_factor": round(profit_factor, 4),
        "expectancy_usdt": round(expectancy, 4),
        "avg_win_usdt": round(avg_win, 4),
        "avg_loss_usdt": round(avg_loss, 4),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "max_drawdown_usdt": round(max_drawdown_usdt, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
        "daily_max_drawdown_usdt": round(daily_drawdown_usdt, 4),
        "daily_max_drawdown_pct": round(daily_drawdown_pct, 4),
        "pnl_usdt": round(sum(pnl_values), 4),
        "by_symbol": symbol_rows[:12],
    }
