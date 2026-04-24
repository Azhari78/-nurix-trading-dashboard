from __future__ import annotations

import json
import logging
import random
import time
from contextlib import suppress
from pathlib import Path
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
        self._runtime_auto_trade_symbols = list(settings.auto_trade_symbols)
        self._runtime_auto_trade_symbols_set = set(settings.auto_trade_symbols_set)
        self._symbol_validation_done = False
        self._session_windows = self._parse_session_windows(settings.auto_trade_session_utc)
        self._copy_followers = [
            {"name": follower_name, "multiplier": float(multiplier)}
            for follower_name, multiplier in settings.copy_trade_followers
            if follower_name and float(multiplier) > 0
        ]
        for follower in self._copy_followers:
            name = str(follower["name"])
            self.state.copy_trade_positions.setdefault(name, {})
            self.state.copy_trade_stats.setdefault(
                name,
                {"trades": 0, "wins": 0, "losses": 0, "pnl_usdt": 0.0},
            )
        self._state_file_path = Path(settings.auto_trade_state_file).expanduser()
        self._state_save_interval_seconds = max(
            1,
            int(settings.auto_trade_state_save_interval_seconds),
        )
        self._last_state_save_at = 0.0
        self._load_persisted_runtime_state()

    def _state_persistence_enabled(self) -> bool:
        return bool(self.settings.paper_trading and self.settings.paper_wallet_enabled)

    @staticmethod
    def _float_map(raw_map: Any, *, uppercase_keys: bool = True) -> dict[str, float]:
        if not isinstance(raw_map, dict):
            return {}
        values: dict[str, float] = {}
        for key_raw, value_raw in raw_map.items():
            key = str(key_raw or "").strip()
            if not key:
                continue
            if uppercase_keys:
                key = key.upper()
            value = safe_float(value_raw)
            values[key] = float(value) if value is not None else 0.0
        return values

    def _load_persisted_runtime_state(self) -> None:
        if not self._state_persistence_enabled():
            return

        state_path = self._state_file_path
        if not state_path.is_file():
            return

        try:
            with state_path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Could not load persisted auto-trade state from %s: %s", state_path, exc)
            return

        if not isinstance(payload, dict):
            self.logger.warning("Ignoring persisted auto-trade state in %s: invalid payload", state_path)
            return

        positions_raw = payload.get("auto_trade_positions")
        restored_positions: dict[str, dict[str, Any]] = {}
        if isinstance(positions_raw, dict):
            for symbol_raw, position_raw in positions_raw.items():
                symbol = str(symbol_raw or "").strip().upper()
                if not symbol or not isinstance(position_raw, dict):
                    continue
                restored_positions[symbol] = dict(position_raw)

        with self.state.auto_trade_lock:
            self.state.auto_trade_positions = restored_positions
            self.state.auto_trade_last_action_ts = self._float_map(
                payload.get("auto_trade_last_action_ts"),
                uppercase_keys=True,
            )
            self.state.auto_trade_cooldown_until = self._float_map(
                payload.get("auto_trade_cooldown_until"),
                uppercase_keys=True,
            )
            self.state.auto_trade_daily_pnl = self._float_map(
                payload.get("auto_trade_daily_pnl"),
                uppercase_keys=False,
            )
            self.state.auto_trade_halt_reason_by_day = {
                str(key): str(value)
                for key, value in (payload.get("auto_trade_halt_reason_by_day") or {}).items()
                if str(key).strip()
            }
            self.state.auto_trade_halt_day = (
                str(payload.get("auto_trade_halt_day")).strip()
                if payload.get("auto_trade_halt_day") is not None
                else None
            ) or None
            self.state.auto_trade_halt_until = float(safe_float(payload.get("auto_trade_halt_until")) or 0.0)
            halt_reason = payload.get("auto_trade_halt_reason")
            self.state.auto_trade_halt_reason = str(halt_reason).strip() if halt_reason else None
            self.state.auto_trade_last_convert_at = float(
                safe_float(payload.get("auto_trade_last_convert_at")) or 0.0
            )
            self.state.auto_trade_consecutive_losses = max(
                0,
                int(safe_float(payload.get("auto_trade_consecutive_losses")) or 0),
            )
            stats_by_symbol = payload.get("auto_trade_stats_by_symbol")
            if isinstance(stats_by_symbol, dict):
                self.state.auto_trade_stats_by_symbol = {
                    str(symbol).upper(): dict(stats)
                    for symbol, stats in stats_by_symbol.items()
                    if str(symbol).strip() and isinstance(stats, dict)
                }
            journal_rows = payload.get("auto_trade_journal")
            if isinstance(journal_rows, list):
                self.state.auto_trade_journal.clear()
                for row in journal_rows[-self.state.auto_trade_journal.maxlen :]:
                    if isinstance(row, dict):
                        self.state.auto_trade_journal.append(dict(row))

        with self.state.wallet_lock:
            paper_wallet = payload.get("paper_wallet")
            if isinstance(paper_wallet, dict):
                initialized = bool(paper_wallet.get("initialized", False))
                if restored_positions:
                    initialized = True
                self.state.paper_wallet_initialized = initialized
                self.state.paper_wallet_free_usdt = max(
                    0.0,
                    float(safe_float(paper_wallet.get("free_usdt")) or 0.0),
                )
                self.state.paper_wallet_used_usdt = max(
                    0.0,
                    float(safe_float(paper_wallet.get("used_usdt")) or 0.0),
                )
                self.state.paper_wallet_realized_pnl_usdt = float(
                    safe_float(paper_wallet.get("realized_pnl_usdt")) or 0.0
                )
                day_key = str(paper_wallet.get("day_key") or "").strip()
                self.state.wallet_day_key = day_key or None
                day_start_total = safe_float(paper_wallet.get("day_start_total_usdt"))
                self.state.wallet_day_start_total_usdt = (
                    float(day_start_total)
                    if day_start_total is not None and day_start_total >= 0
                    else None
                )
                self.state.wallet_cache["updated_at"] = 0.0

        self.logger.info(
            "Loaded persisted auto-trade state from %s (positions=%s, journal=%s)",
            state_path,
            len(restored_positions),
            len(self.state.auto_trade_journal),
        )

    def _build_runtime_state_payload(self) -> dict[str, Any]:
        with self.state.auto_trade_lock:
            payload: dict[str, Any] = {
                "version": 1,
                "saved_at": int(time.time()),
                "auto_trade_positions": {
                    str(symbol): dict(position)
                    for symbol, position in self.state.auto_trade_positions.items()
                    if isinstance(position, dict)
                },
                "auto_trade_last_action_ts": {
                    str(symbol): float(value)
                    for symbol, value in self.state.auto_trade_last_action_ts.items()
                },
                "auto_trade_cooldown_until": {
                    str(symbol): float(value)
                    for symbol, value in self.state.auto_trade_cooldown_until.items()
                },
                "auto_trade_daily_pnl": {
                    str(day_key): float(value)
                    for day_key, value in self.state.auto_trade_daily_pnl.items()
                },
                "auto_trade_halt_day": self.state.auto_trade_halt_day,
                "auto_trade_halt_until": float(self.state.auto_trade_halt_until),
                "auto_trade_halt_reason": self.state.auto_trade_halt_reason,
                "auto_trade_halt_reason_by_day": dict(self.state.auto_trade_halt_reason_by_day),
                "auto_trade_last_convert_at": float(self.state.auto_trade_last_convert_at),
                "auto_trade_consecutive_losses": int(self.state.auto_trade_consecutive_losses),
                "auto_trade_stats_by_symbol": {
                    str(symbol): dict(stats)
                    for symbol, stats in self.state.auto_trade_stats_by_symbol.items()
                    if isinstance(stats, dict)
                },
                "auto_trade_journal": list(self.state.auto_trade_journal),
            }

        with self.state.wallet_lock:
            payload["paper_wallet"] = {
                "initialized": bool(self.state.paper_wallet_initialized),
                "free_usdt": float(self.state.paper_wallet_free_usdt),
                "used_usdt": float(self.state.paper_wallet_used_usdt),
                "realized_pnl_usdt": float(self.state.paper_wallet_realized_pnl_usdt),
                "day_key": self.state.wallet_day_key,
                "day_start_total_usdt": self.state.wallet_day_start_total_usdt,
            }

        return payload

    def persist_runtime_state(self, force: bool = False) -> None:
        if not self._state_persistence_enabled():
            return

        now = time.time()
        if not force and now - self._last_state_save_at < self._state_save_interval_seconds:
            return

        payload = self._build_runtime_state_payload()
        state_path = self._state_file_path
        tmp_path = state_path.with_name(f"{state_path.name}.tmp")
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True)
            tmp_path.replace(state_path)
            self._last_state_save_at = now
        except Exception as exc:  # noqa: BLE001
            with suppress(Exception):
                tmp_path.unlink()
            self.logger.warning("Failed to persist auto-trade runtime state to %s: %s", state_path, exc)

    @staticmethod
    def utc_day_key() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    @staticmethod
    def utc_day_key_from_timestamp(ts: float) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(ts))

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _parse_session_windows(raw_value: str) -> list[tuple[int, int]]:
        windows: list[tuple[int, int]] = []
        for part in (raw_value or "").split(","):
            chunk = part.strip()
            if not chunk:
                continue
            if "-" not in chunk:
                try:
                    hour = int(chunk)
                except ValueError:
                    continue
                hour = hour % 24
                windows.append((hour, hour))
                continue

            left, right = chunk.split("-", 1)
            try:
                start = int(left.strip()) % 24
                end = int(right.strip()) % 24
            except ValueError:
                continue
            windows.append((start, end))

        if not windows:
            return [(0, 23)]
        return windows

    def _session_allows_entry(self, now_ts: float) -> bool:
        if not self.settings.auto_trade_session_filter_enabled:
            return True
        hour = int(time.gmtime(now_ts).tm_hour)
        for start, end in self._session_windows:
            if start <= end:
                if start <= hour <= end:
                    return True
            else:
                if hour >= start or hour <= end:
                    return True
        return False

    def _ensure_symbol_whitelist(self) -> None:
        if self._symbol_validation_done:
            return
        self._symbol_validation_done = True
        if not self.settings.auto_trade_symbol_validation_enabled:
            return

        try:
            self.exchange.call("load_markets")
            valid: list[str] = []
            invalid: list[str] = []
            for symbol in self.settings.auto_trade_symbols:
                try:
                    self.exchange.call("market", symbol)
                    valid.append(symbol)
                except Exception:  # noqa: BLE001
                    invalid.append(symbol)

            if valid:
                self._runtime_auto_trade_symbols = valid
                self._runtime_auto_trade_symbols_set = set(valid)
            else:
                self._runtime_auto_trade_symbols = [self.settings.default_symbol]
                self._runtime_auto_trade_symbols_set = {self.settings.default_symbol}

            if invalid:
                self.alerts.emit_alert(
                    symbol=self.settings.default_symbol,
                    alert_type="auto_trade_symbol_filtered",
                    title="Auto-trade symbol whitelist filtered",
                    message=f"Unsupported on exchange: {', '.join(invalid[:6])}",
                    severity="medium",
                    meta={
                        "event": "SYMBOL_FILTER",
                        "reason": "Unsupported symbols removed",
                    },
                )
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Symbol whitelist validation skipped: %s", exc)

    @staticmethod
    def _pnl_pct(entry_price: float, current_price: float, position_side: str) -> float:
        if entry_price <= 0:
            return 0.0
        if position_side == "SHORT":
            return ((entry_price - current_price) / entry_price) * 100
        return ((current_price - entry_price) / entry_price) * 100

    @staticmethod
    def _fmt_usdt(value: float, *, signed: bool = False) -> str:
        number = float(value)
        abs_number = abs(number)
        if abs_number < 1e-9:
            decimals = 2
        elif abs_number >= 1:
            decimals = 2
        elif abs_number >= 0.01:
            decimals = 4
        else:
            decimals = 6
        sign_flag = "+" if signed else ""
        return f"{number:{sign_flag}.{decimals}f}"

    def _record_journal(
        self,
        *,
        symbol: str,
        event_type: str,
        side: str,
        reason: str,
        pnl_usdt: float | None,
        pnl_pct: float | None,
        notional_usdt: float | None,
        price: float | None,
        amount: float | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.state.auto_trade_journal.append(
            {
                "timestamp": int(time.time()),
                "symbol": symbol,
                "event_type": event_type,
                "side": side,
                "reason": reason,
                "pnl_usdt": round(pnl_usdt, 4) if pnl_usdt is not None else None,
                "pnl_pct": round(pnl_pct, 4) if pnl_pct is not None else None,
                "notional_usdt": round(notional_usdt, 4) if notional_usdt is not None else None,
                "price": round(price, 6) if price is not None else None,
                "amount": round(amount, 8) if amount is not None else None,
                "metadata": metadata or {},
            }
        )

    def _update_symbol_stats(self, symbol: str, pnl_usdt: float) -> None:
        stats = self.state.auto_trade_stats_by_symbol.setdefault(
            symbol,
            {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl_usdt": 0.0,
            },
        )
        stats["trades"] += 1
        stats["pnl_usdt"] += pnl_usdt
        if pnl_usdt >= 0:
            stats["wins"] += 1
            self.state.auto_trade_consecutive_losses = 0
        else:
            stats["losses"] += 1
            self.state.auto_trade_consecutive_losses += 1

    def _copy_trade_is_enabled(self) -> bool:
        return bool(self.settings.copy_trade_enabled and self._copy_followers)

    def _copy_trade_event(
        self,
        *,
        follower: str,
        symbol: str,
        event_type: str,
        side: str,
        amount: float,
        price: float,
        pnl_usdt: float | None = None,
        reason: str = "",
    ) -> None:
        self.state.copy_trade_counter += 1
        self.state.copy_trade_events.append(
            {
                "id": self.state.copy_trade_counter,
                "timestamp": int(time.time()),
                "follower": follower,
                "symbol": symbol,
                "event_type": event_type,
                "side": side,
                "amount": round(amount, 8),
                "price": round(price, 6),
                "pnl_usdt": round(pnl_usdt, 4) if pnl_usdt is not None else None,
                "reason": reason,
            }
        )

    def _copy_trade_price_with_slippage(self, price: float, order_side: str) -> float:
        slip = max(0.0, self.settings.copy_trade_slippage_bps) / 10000.0
        side = str(order_side or "").lower()
        if side == "buy":
            return price * (1 + slip)
        if side == "sell":
            return price * (1 - slip)
        return price

    def _copy_trade_on_entry(
        self,
        *,
        symbol: str,
        position_side: str,
        order_side: str,
        entry_price: float,
        amount: float,
        notional_usdt: float,
    ) -> None:
        if not self._copy_trade_is_enabled():
            return

        for follower in self._copy_followers:
            follower_name = str(follower["name"])
            multiplier = float(follower["multiplier"])
            follower_amount = amount * multiplier
            follower_price = self._copy_trade_price_with_slippage(entry_price, order_side)
            follower_notional = notional_usdt * multiplier

            follower_positions = self.state.copy_trade_positions.setdefault(follower_name, {})
            follower_positions[symbol] = {
                "side": position_side,
                "entry_price": follower_price,
                "amount": follower_amount,
                "notional_usdt": follower_notional,
                "opened_at": int(time.time()),
            }
            self._copy_trade_event(
                follower=follower_name,
                symbol=symbol,
                event_type="COPY_ENTRY",
                side=position_side,
                amount=follower_amount,
                price=follower_price,
                reason="Mirrored master entry",
            )

    def _copy_trade_on_exit(
        self,
        *,
        symbol: str,
        position_side: str,
        order_side: str,
        exit_price: float,
        master_amount: float,
        reason: str,
        partial: bool = False,
    ) -> None:
        if not self._copy_trade_is_enabled():
            return

        for follower in self._copy_followers:
            follower_name = str(follower["name"])
            multiplier = float(follower["multiplier"])
            follower_positions = self.state.copy_trade_positions.get(follower_name, {})
            follower_position = follower_positions.get(symbol)
            if not follower_position:
                continue

            open_amount = safe_float(follower_position.get("amount")) or 0.0
            close_amount = min(open_amount, master_amount * multiplier)
            if close_amount <= 0:
                continue

            follower_entry = safe_float(follower_position.get("entry_price")) or exit_price
            follower_exit = self._copy_trade_price_with_slippage(exit_price, order_side)
            follower_pnl = self._pnl_usdt(position_side, follower_entry, follower_exit, close_amount)

            remaining_amount = max(0.0, open_amount - close_amount)
            if remaining_amount <= 0:
                follower_positions.pop(symbol, None)
            else:
                follower_position["amount"] = remaining_amount

            stats = self.state.copy_trade_stats.setdefault(
                follower_name,
                {"trades": 0, "wins": 0, "losses": 0, "pnl_usdt": 0.0},
            )
            stats["pnl_usdt"] += follower_pnl
            if not partial or remaining_amount <= 0:
                stats["trades"] += 1
                if follower_pnl >= 0:
                    stats["wins"] += 1
                else:
                    stats["losses"] += 1

            self._copy_trade_event(
                follower=follower_name,
                symbol=symbol,
                event_type=("COPY_PARTIAL_EXIT" if partial else "COPY_EXIT"),
                side=position_side,
                amount=close_amount,
                price=follower_exit,
                pnl_usdt=follower_pnl,
                reason=reason,
            )

    def _derive_auto_adapt_profile(
        self,
        market_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        base_conf = self.settings.ai_filter_min_confidence
        if not self.settings.auto_trade_auto_adapt_enabled:
            profile = {
                "name": "MIDDLE",
                "reason": "Auto-adapt disabled",
                "risk_mult": 1.0,
                "ai_min_confidence": base_conf,
                "cooldown_mult": 1.0,
            }
            self.state.auto_trade_adaptive_profile = profile["name"]
            self.state.auto_trade_adaptive_reason = profile["reason"]
            self.state.auto_trade_adaptive_ai_min_confidence = profile["ai_min_confidence"]
            self.state.auto_trade_adaptive_risk_multiplier = profile["risk_mult"]
            self.state.auto_trade_adaptive_cooldown_multiplier = profile["cooldown_mult"]
            return profile

        scoped_rows = [
            row
            for row in market_rows
            if str(row.get("symbol") or "") in self._runtime_auto_trade_symbols_set
            and not row.get("error")
        ]

        atr_values = sorted(
            [
                value
                for value in (
                    safe_float(row.get("atr_pct"))
                    for row in scoped_rows
                )
                if value is not None and value > 0
            ]
        )
        ai_conf_values = [
            value
            for value in (
                safe_float(row.get("ai_confidence"))
                for row in scoped_rows
            )
            if value is not None
        ]

        median_atr = atr_values[len(atr_values) // 2] if atr_values else 0.0
        avg_ai_conf = sum(ai_conf_values) / len(ai_conf_values) if ai_conf_values else 0.0
        loss_streak = self.state.auto_trade_consecutive_losses

        profile_name = "MIDDLE"
        risk_mult = 1.0
        ai_min_conf = base_conf
        cooldown_mult = 1.0

        if (
            median_atr >= self.settings.auto_trade_auto_adapt_high_atr_pct
            or loss_streak >= max(2, self.settings.auto_trade_max_consecutive_losses - 1)
        ):
            profile_name = "WEAK"
            risk_mult = self.settings.auto_trade_auto_adapt_risk_off_mult
            ai_min_conf = min(100, base_conf + self.settings.auto_trade_auto_adapt_conf_step)
            cooldown_mult = self.settings.auto_trade_auto_adapt_cooldown_off_mult
        elif (
            median_atr <= self.settings.auto_trade_auto_adapt_low_atr_pct
            and avg_ai_conf >= max(40, base_conf)
            and loss_streak == 0
        ):
            profile_name = "STRONG"
            risk_mult = self.settings.auto_trade_auto_adapt_risk_on_mult
            ai_min_conf = max(0, base_conf - self.settings.auto_trade_auto_adapt_conf_step)
            cooldown_mult = self.settings.auto_trade_auto_adapt_cooldown_on_mult

        reason = (
            f"{profile_name}: ATR~{median_atr:.2f}% • AI~{avg_ai_conf:.0f}% • "
            f"loss streak {loss_streak}"
        )
        profile = {
            "name": profile_name,
            "reason": reason,
            "risk_mult": risk_mult,
            "ai_min_confidence": ai_min_conf,
            "cooldown_mult": cooldown_mult,
        }
        self.state.auto_trade_adaptive_profile = profile_name
        self.state.auto_trade_adaptive_reason = reason
        self.state.auto_trade_adaptive_ai_min_confidence = ai_min_conf
        self.state.auto_trade_adaptive_risk_multiplier = risk_mult
        self.state.auto_trade_adaptive_cooldown_multiplier = cooldown_mult
        return profile

    def _recent_realized_trades(self, now_ts: float, lookback_seconds: int = 7 * 86400) -> list[dict[str, Any]]:
        minimum_ts = int(now_ts) - lookback_seconds
        return [
            row
            for row in self.state.auto_trade_journal
            if row.get("event_type") in {"EXIT", "PARTIAL_EXIT"}
            and int(row.get("timestamp") or 0) >= minimum_ts
            and row.get("pnl_usdt") is not None
        ]

    def _realized_trades_for_day(self, day_key: str) -> list[dict[str, Any]]:
        return [
            row
            for row in self.state.auto_trade_journal
            if row.get("event_type") in {"EXIT", "PARTIAL_EXIT"}
            and self.utc_day_key_from_timestamp(int(row.get("timestamp") or 0)) == day_key
            and row.get("pnl_usdt") is not None
        ]

    def _maybe_send_daily_recap(self, now_ts: float, current_day_key: str) -> None:
        recap_day = self.utc_day_key_from_timestamp(now_ts - 86400)
        if self.state.auto_trade_last_daily_recap_day == recap_day:
            return

        realized = self._realized_trades_for_day(recap_day)
        pnl_values = [safe_float(row.get("pnl_usdt")) or 0.0 for row in realized]
        trade_count = len(pnl_values)
        wins = sum(1 for pnl in pnl_values if pnl >= 0)
        losses = trade_count - wins
        win_rate_pct = ((wins / trade_count) * 100.0) if trade_count > 0 else 0.0
        avg_win = (
            sum(pnl for pnl in pnl_values if pnl > 0) / max(1, sum(1 for pnl in pnl_values if pnl > 0))
        ) if trade_count > 0 else 0.0
        avg_loss = (
            abs(
                sum(pnl for pnl in pnl_values if pnl < 0)
                / max(1, sum(1 for pnl in pnl_values if pnl < 0))
            )
        ) if trade_count > 0 else 0.0

        daily_pnl = safe_float(self.state.auto_trade_daily_pnl.get(recap_day))
        if daily_pnl is None:
            daily_pnl = sum(pnl_values)

        pnl_by_symbol: dict[str, float] = {}
        for row in realized:
            symbol = str(row.get("symbol") or "-")
            pnl_by_symbol[symbol] = pnl_by_symbol.get(symbol, 0.0) + (safe_float(row.get("pnl_usdt")) or 0.0)

        if pnl_by_symbol:
            top_symbol, top_symbol_pnl = max(pnl_by_symbol.items(), key=lambda item: item[1])
        else:
            top_symbol, top_symbol_pnl = "-", 0.0

        halt_reason = str(self.state.auto_trade_halt_reason_by_day.get(recap_day) or "").strip()
        halt_text = halt_reason if halt_reason else "None"

        if trade_count <= 0 and abs(daily_pnl) < 1e-9 and halt_text == "None":
            self.state.auto_trade_last_daily_recap_day = recap_day
            return

        message = (
            f"UTC {recap_day} • PnL {self._fmt_usdt(daily_pnl, signed=True)} USDT • "
            f"Trades {trade_count} • Win {win_rate_pct:.1f}% ({wins}W/{losses}L) • "
            f"Avg Win {self._fmt_usdt(avg_win)} • Avg Loss {self._fmt_usdt(avg_loss)} • "
            f"Top {top_symbol} ({self._fmt_usdt(top_symbol_pnl, signed=True)}) • Halt: {halt_text}"
        )

        self.alerts.emit_alert(
            symbol=self.settings.default_symbol,
            alert_type="auto_trade_daily_recap",
            title=f"Auto-trade daily recap {recap_day}",
            message=message,
            severity="low",
            meta={
                "event": "DAILY_RECAP",
                "day_key": recap_day,
                "pnl_usdt": round(daily_pnl, 4),
                "trades": trade_count,
                "wins": wins,
                "losses": losses,
                "win_rate_pct": round(win_rate_pct, 2),
                "avg_win_usdt": round(avg_win, 4),
                "avg_loss_usdt": round(avg_loss, 4),
                "top_symbol": top_symbol,
                "top_symbol_pnl_usdt": round(top_symbol_pnl, 4),
                "halt_reason": halt_text,
                "generated_day_key": current_day_key,
            },
        )
        self.state.auto_trade_last_daily_recap_day = recap_day

    def _compute_forward_guardrail(self, now_ts: float) -> tuple[float, str]:
        if not self.settings.auto_trade_forward_guardrail_enabled:
            self.state.auto_trade_guardrail_active = False
            self.state.auto_trade_guardrail_reason = ""
            return 1.0, ""

        realized = self._recent_realized_trades(now_ts)
        if len(realized) < self.settings.auto_trade_forward_guardrail_min_trades:
            self.state.auto_trade_guardrail_active = False
            self.state.auto_trade_guardrail_reason = ""
            return 1.0, ""

        wins = sum(1 for row in realized if (safe_float(row.get("pnl_usdt")) or 0.0) >= 0)
        trade_count = len(realized)
        win_rate = wins / trade_count if trade_count > 0 else 0.0
        avg_pnl = sum(safe_float(row.get("pnl_usdt")) or 0.0 for row in realized) / trade_count

        baseline_win_rate = self.settings.auto_trade_forward_baseline_win_rate
        baseline_avg_pnl = self.settings.auto_trade_forward_baseline_avg_pnl_usdt
        underperform = (
            win_rate < baseline_win_rate
            or avg_pnl < baseline_avg_pnl
        )
        severe = (
            win_rate < self.settings.auto_trade_forward_guardrail_severe_win_rate
            and trade_count >= max(5, self.settings.auto_trade_forward_guardrail_min_trades // 2)
        )

        if severe and self.settings.auto_trade_forward_guardrail_halt_enabled:
            self.state.auto_trade_halt_until = max(
                self.state.auto_trade_halt_until,
                now_ts + self.settings.auto_trade_kill_switch_pause_seconds,
            )
            self.state.auto_trade_halt_reason = (
                f"Forward guardrail severe underperformance (win rate {win_rate:.0%})"
            )
            self.state.auto_trade_halt_reason_by_day[self.utc_day_key_from_timestamp(now_ts)] = (
                str(self.state.auto_trade_halt_reason)
            )

        if underperform:
            reason = (
                f"Forward guardrail active: win {win_rate:.0%} / avg pnl {avg_pnl:.2f}"
            )
            self.state.auto_trade_guardrail_active = True
            self.state.auto_trade_guardrail_reason = reason
            return self.settings.auto_trade_forward_guardrail_risk_mult, reason

        self.state.auto_trade_guardrail_active = False
        self.state.auto_trade_guardrail_reason = ""
        return 1.0, ""

    def _volatility_size_multiplier(self, row: dict[str, Any]) -> float:
        if not self.settings.auto_trade_volatility_sizing_enabled:
            return 1.0

        atr_pct = safe_float(row.get("atr_pct"))
        if atr_pct is None or atr_pct <= 0:
            return 1.0

        raw = self.settings.auto_trade_target_atr_pct / atr_pct
        return self._clamp(
            raw,
            self.settings.auto_trade_volatility_size_min_mult,
            self.settings.auto_trade_volatility_size_max_mult,
        )

    def _is_extreme_volatility(self, row: dict[str, Any]) -> tuple[bool, str]:
        if not self.settings.auto_trade_extreme_volatility_block_enabled:
            return False, ""

        atr_pct = safe_float(row.get("atr_pct"))
        if atr_pct is not None and atr_pct > self.settings.auto_trade_max_atr_pct:
            return True, f"ATR {atr_pct:.2f}% > {self.settings.auto_trade_max_atr_pct:.2f}%"

        change_24h = safe_float(row.get("change_24h"))
        if (
            change_24h is not None
            and abs(change_24h) > self.settings.auto_trade_max_abs_change_24h_pct
        ):
            return (
                True,
                (
                    f"24h change {change_24h:.2f}% exceeds "
                    f"{self.settings.auto_trade_max_abs_change_24h_pct:.2f}%"
                ),
            )
        return False, ""

    def _execution_cost_gate_allows(self, row: dict[str, Any], side: str) -> tuple[bool, str]:
        if not self.settings.auto_trade_execution_cost_gate_enabled:
            return True, ""

        spread_pct = safe_float(row.get("spread_pct"))
        if (
            spread_pct is not None
            and spread_pct > self.settings.auto_trade_max_spread_pct
        ):
            return (
                False,
                (
                    f"spread {spread_pct:.3f}% > "
                    f"{self.settings.auto_trade_max_spread_pct:.3f}%"
                ),
            )

        _, take_profit_pct, _ = self._side_stop_take_trail(side)
        total_cost_pct = (
            max(0.0, spread_pct or 0.0)
            + self.settings.auto_trade_estimated_fee_pct
            + self.settings.auto_trade_estimated_slippage_pct
        )
        expected_edge_pct = take_profit_pct - total_cost_pct
        if expected_edge_pct < self.settings.auto_trade_min_edge_pct:
            return (
                False,
                (
                    f"edge {expected_edge_pct:.3f}% < "
                    f"{self.settings.auto_trade_min_edge_pct:.3f}%"
                ),
            )

        return True, ""

    def _entry_rank_score(self, row: dict[str, Any], side: str) -> float:
        ai_confidence = safe_float(row.get("ai_confidence")) or 0.0
        ai_score_abs = abs(safe_float(row.get("ai_score")) or 0.0)
        strength_confidence = safe_float(row.get("strength_confidence")) or 0.0
        volume_ratio = safe_float(row.get("volume_ratio")) or 0.0
        atr_pct = safe_float(row.get("atr_pct")) or 0.0
        spread_pct = safe_float(row.get("spread_pct"))
        rsi = safe_float(row.get("rsi"))
        macd = safe_float(row.get("macd"))
        macd_signal = safe_float(row.get("macd_signal"))

        score = 0.0
        score += ai_confidence * 0.42
        score += strength_confidence * 0.28
        score += self._clamp(volume_ratio, 0.0, 2.0) * 20.0
        score += self._clamp(ai_score_abs, 0.0, 5.0) * 6.0

        if atr_pct > 0:
            atr_target = max(0.05, self.settings.auto_trade_target_atr_pct)
            atr_fit = 1.0 - min(abs(atr_pct - atr_target) / atr_target, 1.0)
            score += atr_fit * 10.0

        if spread_pct is not None:
            score -= max(0.0, spread_pct) * 120.0

        if rsi is not None:
            if side == "SHORT":
                band_min = self.settings.short_rsi_min
                band_max = self.settings.short_rsi_max
            else:
                band_min = self.settings.long_rsi_min
                band_max = self.settings.long_rsi_max
            band_mid = (band_min + band_max) / 2.0
            band_half = max(1.0, (band_max - band_min) / 2.0)
            rsi_fit = max(0.0, 1.0 - abs(rsi - band_mid) / band_half)
            score += rsi_fit * 8.0

        if macd is not None and macd_signal is not None:
            macd_spread = macd - macd_signal
            directional_spread = macd_spread if side == "LONG" else -macd_spread
            score += self._clamp(directional_spread * 40.0, -6.0, 6.0)

        return round(score, 4)

    def _adaptive_cooldown_multiplier(self, row: dict[str, Any]) -> float:
        if not self.settings.auto_trade_cooldown_adaptive_enabled:
            return 1.0

        loss_mult = 1.0 + (
            self.state.auto_trade_consecutive_losses
            * self.settings.auto_trade_cooldown_loss_step_mult
        )

        atr_pct = safe_float(row.get("atr_pct"))
        if atr_pct is None or atr_pct <= 0:
            vol_mult = 1.0
        else:
            vol_mult = atr_pct / self.settings.auto_trade_cooldown_volatility_target_pct
            vol_mult = self._clamp(vol_mult, 0.8, self.settings.auto_trade_cooldown_volatility_max_mult)

        return self._clamp(loss_mult * vol_mult, 0.8, self.settings.auto_trade_cooldown_volatility_max_mult)

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
        ema20 = safe_float(row.get("ema20"))
        ema50 = safe_float(row.get("ema50"))
        rsi = safe_float(row.get("rsi"))
        macd = safe_float(row.get("macd"))
        macd_signal = safe_float(row.get("macd_signal"))
        volume_ratio = safe_float(row.get("volume_ratio"))
        strength_confidence = int(safe_float(row.get("strength_confidence")) or 0)

        if price is None or ema50 is None or ema20 is None or rsi is None:
            return False, "waiting price/EMA20/EMA50/RSI"

        if strength_confidence < self.settings.auto_trade_min_strength_confidence:
            return (
                False,
                (
                    f"strength confidence {strength_confidence}% < "
                    f"{self.settings.auto_trade_min_strength_confidence}%"
                ),
            )

        if (
            self.settings.auto_trade_min_volume_ratio > 0
            and volume_ratio is not None
            and volume_ratio < self.settings.auto_trade_min_volume_ratio
        ):
            return (
                False,
                (
                    f"volume ratio {volume_ratio:.2f} < "
                    f"{self.settings.auto_trade_min_volume_ratio:.2f}"
                ),
            )

        if side == "LONG":
            if price <= ema50:
                return False, "LONG rule: price must be > MA50"
            if self.settings.auto_trade_entry_confirm_ema_stack and ema20 <= ema50:
                return False, "LONG rule: EMA20 must be > EMA50"
            if self.settings.auto_trade_entry_confirm_macd:
                if (
                    self.settings.auto_trade_entry_require_macd_data
                    and (macd is None or macd_signal is None)
                ):
                    return False, "LONG rule: waiting MACD/signal data"
                if (
                    macd is not None
                    and macd_signal is not None
                    and macd < macd_signal
                ):
                    return False, "LONG rule: MACD must be above signal"
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
        if self.settings.auto_trade_entry_confirm_ema_stack and ema20 >= ema50:
            return False, "SHORT rule: EMA20 must be < EMA50"
        if self.settings.auto_trade_entry_confirm_macd:
            if (
                self.settings.auto_trade_entry_require_macd_data
                and (macd is None or macd_signal is None)
            ):
                return False, "SHORT rule: waiting MACD/signal data"
            if (
                macd is not None
                and macd_signal is not None
                and macd > macd_signal
            ):
                return False, "SHORT rule: MACD must be below signal"
        if rsi < self.settings.short_rsi_min or rsi > self.settings.short_rsi_max:
            return (
                False,
                (
                    f"SHORT rule: RSI {rsi:.2f} outside "
                    f"{self.settings.short_rsi_min:.0f}-{self.settings.short_rsi_max:.0f}"
                ),
            )
        return True, ""

    def _ai_filter_allows(
        self,
        row: dict[str, Any],
        side: str,
        *,
        min_confidence_override: int | None = None,
    ) -> tuple[bool, str]:
        if not self.settings.ai_filter_enabled:
            return True, ""

        ai_bias = str(row.get("ai_bias") or "HOLD").upper()
        ai_confidence = int(safe_float(row.get("ai_confidence")) or 0)
        ai_score = float(safe_float(row.get("ai_score")) or 0.0)
        min_confidence = (
            int(min_confidence_override)
            if min_confidence_override is not None
            else self.settings.ai_filter_min_confidence
        )

        if ai_confidence < min_confidence:
            return (
                False,
                (
                    f"AI confidence {ai_confidence}% < "
                    f"{min_confidence}%"
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

        if (
            self.settings.auto_trade_break_even_enabled
            and pnl_pct >= self.settings.auto_trade_break_even_trigger_pct
            and not bool(position.get("break_even_armed"))
        ):
            position["break_even_armed"] = True

        if pnl_pct <= -stop_loss_pct:
            return "STOP LOSS", pnl_pct
        tp_run_min_pnl_pct = max(0.0, self.settings.auto_trade_take_profit_run_min_pnl_pct)
        if pnl_pct >= take_profit_pct:
            return "TAKE PROFIT", pnl_pct
        if (
            self.settings.auto_trade_take_profit_run_enabled
            and pnl_pct > 0
            and pnl_pct >= tp_run_min_pnl_pct
            and pnl_pct < take_profit_pct
        ):
            return "TAKE PROFIT AND RUN", pnl_pct
        if (
            self.settings.auto_trade_break_even_enabled
            and bool(position.get("break_even_armed"))
            and pnl_pct <= self.settings.auto_trade_break_even_buffer_pct
        ):
            return "BREAK EVEN EXIT", pnl_pct

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

        if self.settings.auto_trade_time_stop_enabled:
            opened_at = safe_float(position.get("opened_at")) or 0.0
            if opened_at > 0:
                elapsed_minutes = (time.time() - opened_at) / 60.0
                if (
                    elapsed_minutes >= self.settings.auto_trade_time_stop_minutes
                    and pnl_pct <= self.settings.auto_trade_time_stop_min_pnl_pct
                ):
                    return "TIME STOP", pnl_pct

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

    @staticmethod
    def _base_asset(symbol: str) -> str:
        return str(symbol or "").split("/", 1)[0].upper()

    def _invalidate_wallet_cache(self) -> None:
        with self.state.wallet_lock:
            self.state.wallet_cache["updated_at"] = 0.0

    def _paper_wallet_enabled(self) -> bool:
        return bool(self.settings.paper_trading and self.settings.paper_wallet_enabled)

    def _ensure_paper_wallet_locked(self) -> None:
        if self.state.paper_wallet_initialized:
            return
        self.state.paper_wallet_initialized = True
        self.state.paper_wallet_free_usdt = float(self.settings.paper_wallet_start_usdt)
        self.state.paper_wallet_used_usdt = 0.0
        self.state.paper_wallet_realized_pnl_usdt = 0.0

    def _paper_wallet_on_entry(self, notional_usdt: float) -> bool:
        if not self._paper_wallet_enabled():
            return True
        reserve = max(0.0, float(notional_usdt))
        with self.state.wallet_lock:
            self._ensure_paper_wallet_locked()
            free = max(0.0, float(self.state.paper_wallet_free_usdt))
            if free + 1e-9 < reserve:
                return False
            self.state.paper_wallet_free_usdt = free - reserve
            self.state.paper_wallet_used_usdt = (
                max(0.0, float(self.state.paper_wallet_used_usdt)) + reserve
            )
            self.state.wallet_cache["updated_at"] = 0.0
        return True

    def _paper_wallet_on_close(
        self,
        *,
        position: dict[str, Any],
        closed_amount: float,
        open_amount_before_close: float,
        pnl_usdt: float,
    ) -> None:
        if not self._paper_wallet_enabled():
            return
        if open_amount_before_close <= 0 or closed_amount <= 0:
            return

        close_ratio = self._clamp(closed_amount / open_amount_before_close, 0.0, 1.0)
        current_notional = max(0.0, safe_float(position.get("notional_usdt")) or 0.0)
        reserved_release = current_notional * close_ratio
        position["notional_usdt"] = max(0.0, current_notional - reserved_release)

        with self.state.wallet_lock:
            self._ensure_paper_wallet_locked()
            self.state.paper_wallet_used_usdt = max(
                0.0,
                float(self.state.paper_wallet_used_usdt) - reserved_release,
            )
            self.state.paper_wallet_free_usdt = (
                float(self.state.paper_wallet_free_usdt)
                + reserved_release
                + float(pnl_usdt)
            )
            self.state.paper_wallet_realized_pnl_usdt = (
                float(self.state.paper_wallet_realized_pnl_usdt) + float(pnl_usdt)
            )
            self.state.wallet_cache["updated_at"] = 0.0

    def _auto_convert_wallet_assets_to_usdt(
        self,
        wallet_payload: dict[str, Any] | None,
        rows_by_symbol: dict[str, dict[str, Any]],
        now_ts: float,
    ) -> int:
        if not self.settings.auto_trade_auto_convert_to_usdt:
            return 0
        if self.settings.paper_trading:
            return 0

        if (
            now_ts - self.state.auto_trade_last_convert_at
            < self.settings.auto_trade_auto_convert_interval_seconds
        ):
            return 0
        self.state.auto_trade_last_convert_at = now_ts

        assets = (wallet_payload or {}).get("assets")
        if not isinstance(assets, list) or not assets:
            return 0

        open_base_assets = {
            self._base_asset(symbol)
            for symbol in self.state.auto_trade_positions.keys()
        }
        converted_count = 0

        for entry in assets:
            asset = str((entry or {}).get("asset") or "").upper().strip()
            if not asset or asset == "USDT" or asset in open_base_assets:
                continue

            symbol = f"{asset}/USDT"
            if symbol not in self._runtime_auto_trade_symbols_set:
                continue

            free_amount = safe_float((entry or {}).get("free")) or 0.0
            if free_amount <= 0:
                continue

            market_row = rows_by_symbol.get(symbol, {})
            price = safe_float(market_row.get("price"))
            if price is None or price <= 0:
                price = safe_float((entry or {}).get("price_usdt"))
            if price is None or price <= 0:
                continue

            estimated_usdt = free_amount * price
            if estimated_usdt < self.settings.auto_trade_auto_convert_min_usdt:
                continue

            sell_amount = free_amount
            with suppress(Exception):
                sell_amount = float(
                    self.exchange.call("amount_to_precision", symbol, free_amount)
                )
            if sell_amount <= 0:
                continue

            try:
                order = self.exchange.call(
                    "create_order",
                    symbol,
                    "market",
                    "sell",
                    sell_amount,
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(
                    "Auto-convert to USDT failed for %s amount=%s: %s",
                    symbol,
                    sell_amount,
                    exc,
                )
                continue

            filled_amount = safe_float(order.get("filled")) or sell_amount
            average_price = safe_float(order.get("average")) or price
            notional_usdt = filled_amount * average_price
            converted_count += 1

            self.push_auto_trade_event(
                symbol,
                "AUTO_CONVERT",
                "SELL (AUTO->USDT)",
                f"Auto-converted {asset} to USDT ({notional_usdt:.2f} USDT)",
                price=average_price,
                amount=filled_amount,
                mode="live",
            )
            self.alerts.emit_alert(
                symbol=symbol,
                alert_type="auto_trade_auto_convert",
                title=f"{symbol} auto convert to USDT",
                message=f"Converted {filled_amount:.8f} {asset} to USDT",
                severity="medium",
                meta={
                    "event": "AUTO_CONVERT",
                    "position_side": "LONG",
                    "order_side": "SELL",
                    "price": average_price,
                    "amount": filled_amount,
                    "reason": "AUTO_CONVERT_TO_USDT",
                    "mode": "LIVE",
                },
            )
            self._record_journal(
                symbol=symbol,
                event_type="AUTO_CONVERT",
                side="LONG",
                reason="AUTO_CONVERT_TO_USDT",
                pnl_usdt=None,
                pnl_pct=None,
                notional_usdt=notional_usdt,
                price=average_price,
                amount=filled_amount,
                metadata={"asset": asset},
            )

        if converted_count > 0:
            self._invalidate_wallet_cache()
        return converted_count

    def _next_cooldown_seconds(
        self,
        row: dict[str, Any] | None = None,
        *,
        extra_multiplier: float = 1.0,
    ) -> int:
        low = max(1, self.settings.cooldown_min_seconds)
        high = max(low, self.settings.cooldown_max_seconds)
        if low == high:
            base_value = low
        else:
            base_value = random.randint(low, high)

        multiplier = 1.0
        if row is not None:
            multiplier = self._adaptive_cooldown_multiplier(row)
        multiplier *= max(0.2, extra_multiplier)
        return max(1, int(round(base_value * multiplier)))

    def _base_notional_usdt(
        self,
        available_usdt: float | None,
        row: dict[str, Any],
        risk_multiplier: float = 1.0,
    ) -> float:
        notional_usdt = self.settings.trade_size_usdt

        if (
            self.settings.trade_size_percent > 0
            and available_usdt is not None
            and available_usdt > 0
        ):
            notional_usdt = available_usdt * (self.settings.trade_size_percent / 100)

        notional_usdt *= self._volatility_size_multiplier(row)
        notional_usdt *= max(0.05, risk_multiplier)
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
        self._ensure_symbol_whitelist()

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

            self._maybe_send_daily_recap(now, day_key)

            rows_by_symbol = {
                str(row.get("symbol")): row for row in market_rows if row.get("symbol")
            }
            adaptive_profile = self._derive_auto_adapt_profile(market_rows)
            forward_risk_multiplier, forward_reason = self._compute_forward_guardrail(now)
            effective_risk_multiplier = (
                forward_risk_multiplier * float(adaptive_profile["risk_mult"])
            )
            self.state.auto_trade_last_risk_multiplier = effective_risk_multiplier

            for symbol, position in list(self.state.auto_trade_positions.items()):
                row = rows_by_symbol.get(symbol)
                if not row:
                    continue

                amount = safe_float(position.get("amount")) or 0.0
                current_price = safe_float(row.get("price"))
                position_side = self._normalize_side(position.get("side"))
                entry_price = safe_float(position.get("entry_price")) or 0.0

                if amount <= 0 or current_price is None:
                    continue

                pnl_pct_live = self._pnl_pct(entry_price, current_price, position_side)
                if (
                    self.settings.auto_trade_partial_take_profit_enabled
                    and not self.settings.auto_trade_take_profit_run_enabled
                    and not bool(position.get("partial_tp_done"))
                    and pnl_pct_live >= self.settings.auto_trade_partial_take_profit_pct
                    and amount > 0
                ):
                    partial_amount = amount * self.settings.auto_trade_partial_take_profit_ratio
                    exit_side = self._exit_order_side(position_side)
                    partial_result = self.execute_auto_trade_order(
                        symbol,
                        exit_side,
                        partial_amount,
                        current_price,
                    )
                    if partial_result.get("ok"):
                        realized_amount = safe_float(partial_result.get("filled")) or partial_amount
                        realized_price = safe_float(partial_result.get("average")) or current_price
                        realized_pnl_pct = self._pnl_pct(entry_price, realized_price, position_side)
                        realized_pnl = self._pnl_usdt(
                            position_side,
                            entry_price,
                            realized_price,
                            realized_amount,
                        )
                        notional_before_close = max(
                            0.0,
                            safe_float(position.get("notional_usdt")) or (entry_price * amount),
                        )
                        close_ratio = self._clamp(realized_amount / amount, 0.0, 1.0)
                        closed_notional = notional_before_close * close_ratio
                        self.state.auto_trade_daily_pnl[day_key] += realized_pnl
                        self._paper_wallet_on_close(
                            position=position,
                            closed_amount=realized_amount,
                            open_amount_before_close=amount,
                            pnl_usdt=realized_pnl,
                        )
                        position["amount"] = max(0.0, amount - realized_amount)
                        position["partial_tp_done"] = True
                        self.state.auto_trade_last_action_ts[symbol] = now
                        self.state.auto_trade_cooldown_until[symbol] = (
                            now + self._next_cooldown_seconds(
                                row,
                                extra_multiplier=float(adaptive_profile["cooldown_mult"]),
                            )
                        )
                        self._record_journal(
                            symbol=symbol,
                            event_type="PARTIAL_EXIT",
                            side=position_side,
                            reason="PARTIAL TAKE PROFIT",
                            pnl_usdt=realized_pnl,
                            pnl_pct=realized_pnl_pct,
                            notional_usdt=closed_notional,
                            price=realized_price,
                            amount=realized_amount,
                            metadata={
                                "remaining_amount": round(position["amount"], 8),
                                "remaining_notional_usdt": round(
                                    max(0.0, notional_before_close - closed_notional),
                                    4,
                                ),
                            },
                        )
                        self._copy_trade_on_exit(
                            symbol=symbol,
                            position_side=position_side,
                            order_side=exit_side,
                            exit_price=realized_price,
                            master_amount=realized_amount,
                            reason="PARTIAL TAKE PROFIT",
                            partial=True,
                        )
                        self.push_auto_trade_event(
                            symbol,
                            "PARTIAL_EXIT",
                            f"{exit_side.upper()} ({position_side} PARTIAL)",
                            f"Partial TP hit • PnL {self._fmt_usdt(realized_pnl)} USDT",
                            price=realized_price,
                            amount=realized_amount,
                            pnl_usdt=realized_pnl,
                            mode=str(partial_result.get("mode") or "live"),
                        )
                        self.alerts.emit_alert(
                            symbol=symbol,
                            alert_type="auto_trade_partial_exit",
                            title=f"{symbol} auto {position_side} PARTIAL EXIT",
                            message=f"Partial TP • PnL {self._fmt_usdt(realized_pnl)} USDT",
                            severity="medium",
                            meta={
                                "event": "PARTIAL_EXIT",
                                "position_side": position_side,
                                "order_side": exit_side.upper(),
                                "price": realized_price,
                                "amount": realized_amount,
                                "pnl_usdt": realized_pnl,
                                "reason": "PARTIAL TAKE PROFIT",
                                "mode": str(partial_result.get("mode") or "live").upper(),
                            },
                        )
                        status_reason = f"{symbol}: partial TP executed"
                        if position["amount"] <= 0:
                            self.state.auto_trade_positions.pop(symbol, None)
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
                        meta={
                            "event": "EXIT_FAILED",
                            "position_side": position_side,
                            "order_side": exit_side.upper(),
                            "price": current_price,
                            "amount": amount,
                        },
                    )
                    continue

                entry_price = safe_float(position.get("entry_price")) or current_price
                filled_amount = safe_float(order_result.get("filled")) or amount
                exit_price = safe_float(order_result.get("average")) or current_price
                pnl_usdt = self._pnl_usdt(position_side, entry_price, exit_price, filled_amount)
                notional_before_close = max(
                    0.0,
                    safe_float(position.get("notional_usdt")) or (entry_price * amount),
                )
                close_ratio = self._clamp(filled_amount / amount, 0.0, 1.0)
                closed_notional = notional_before_close * close_ratio
                self.state.auto_trade_daily_pnl[day_key] += pnl_usdt
                self._paper_wallet_on_close(
                    position=position,
                    closed_amount=filled_amount,
                    open_amount_before_close=amount,
                    pnl_usdt=pnl_usdt,
                )
                self.state.auto_trade_last_action_ts[symbol] = now
                self.state.auto_trade_cooldown_until[symbol] = now + self._next_cooldown_seconds(
                    row,
                    extra_multiplier=float(adaptive_profile["cooldown_mult"]),
                )
                self.state.auto_trade_positions.pop(symbol, None)
                status_reason = f"{symbol}: {position_side} closed ({reason})"
                self._update_symbol_stats(symbol, pnl_usdt)
                self._record_journal(
                    symbol=symbol,
                    event_type="EXIT",
                    side=position_side,
                    reason=reason,
                    pnl_usdt=pnl_usdt,
                    pnl_pct=self._pnl_pct(entry_price, exit_price, position_side),
                    notional_usdt=closed_notional,
                    price=exit_price,
                    amount=filled_amount,
                )
                self._copy_trade_on_exit(
                    symbol=symbol,
                    position_side=position_side,
                    order_side=exit_side,
                    exit_price=exit_price,
                    master_amount=filled_amount,
                    reason=reason,
                    partial=False,
                )

                self.push_auto_trade_event(
                    symbol,
                    "EXIT",
                    f"{exit_side.upper()} ({position_side} EXIT)",
                    f"{reason} • PnL {self._fmt_usdt(pnl_usdt)} USDT",
                    price=exit_price,
                    amount=filled_amount,
                    pnl_usdt=pnl_usdt,
                    mode=str(order_result.get("mode") or "live"),
                )
                self.alerts.emit_alert(
                    symbol=symbol,
                    alert_type="auto_trade_exit",
                    title=f"{symbol} auto {position_side} EXIT executed",
                    message=f"{reason} • PnL {self._fmt_usdt(pnl_usdt)} USDT",
                    severity=("medium" if pnl_usdt >= 0 else "high"),
                    meta={
                        "event": "EXIT",
                        "position_side": position_side,
                        "order_side": exit_side.upper(),
                        "price": exit_price,
                        "amount": filled_amount,
                        "pnl_usdt": pnl_usdt,
                        "reason": reason,
                        "mode": str(order_result.get("mode") or "live").upper(),
                    },
                )

                if (
                    pnl_usdt < 0
                    and self.state.auto_trade_consecutive_losses
                    >= self.settings.auto_trade_max_consecutive_losses
                ):
                    self.state.auto_trade_halt_until = max(
                        self.state.auto_trade_halt_until,
                        now + self.settings.auto_trade_kill_switch_pause_seconds,
                    )
                    self.state.auto_trade_halt_reason = (
                        f"Consecutive losses reached {self.state.auto_trade_consecutive_losses}"
                    )
                    self.state.auto_trade_halt_reason_by_day[day_key] = str(
                        self.state.auto_trade_halt_reason
                    )
                    self.alerts.emit_alert(
                        symbol=symbol,
                        alert_type="auto_trade_kill_switch",
                        title="Auto-trade kill switch activated",
                        message=(
                            f"Paused for {self.settings.auto_trade_kill_switch_pause_seconds}s "
                            f"after {self.state.auto_trade_consecutive_losses} losses"
                        ),
                        severity="high",
                        meta={
                            "event": "KILL_SWITCH",
                            "reason": (
                                f"{self.state.auto_trade_consecutive_losses} consecutive losses"
                            ),
                        },
                    )

            converted_assets = self._auto_convert_wallet_assets_to_usdt(
                wallet_payload,
                rows_by_symbol,
                now,
            )
            if converted_assets > 0:
                status_reason = (
                    f"{status_reason} • auto-convert {converted_assets} asset(s) to USDT"
                )

            daily_pnl = self.state.auto_trade_daily_pnl.get(day_key, 0.0)
            risk_halted = daily_pnl <= -self.settings.max_daily_loss_usdt

            if risk_halted:
                if self.state.auto_trade_halt_day != day_key:
                    self.state.auto_trade_halt_day = day_key
                    self.state.auto_trade_halt_reason_by_day[day_key] = (
                        f"Daily risk limit hit ({daily_pnl:.2f} USDT)"
                    )
                    self.alerts.emit_alert(
                        symbol=self.settings.default_symbol,
                        alert_type="auto_trade_halt",
                        title="Auto-trade halted by daily risk limit",
                        message=(
                            f"Daily PnL reached {daily_pnl:.2f} USDT "
                            f"(limit -{self.settings.max_daily_loss_usdt:.2f})"
                        ),
                        severity="high",
                        meta={
                            "event": "HALT",
                            "pnl_usdt": daily_pnl,
                            "reason": "Daily risk limit hit",
                        },
                    )
                self.state.auto_trade_last_reason = (
                    f"Risk halt active: daily PnL {daily_pnl:.2f} USDT "
                    f"(limit -{self.settings.max_daily_loss_usdt:.2f})"
                )
                return

            self.state.auto_trade_halt_day = None
            if self.state.auto_trade_halt_until > now:
                remaining = int(self.state.auto_trade_halt_until - now)
                halt_reason = str(self.state.auto_trade_halt_reason or "cooldown risk pause")
                if self.state.auto_trade_positions:
                    self.state.auto_trade_last_reason = (
                        f"Risk halt ({max(0, remaining)}s): {halt_reason} • "
                        "entries paused, monitoring open positions"
                    )
                else:
                    self.state.auto_trade_last_reason = (
                        f"Risk halt ({max(0, remaining)}s): {halt_reason}"
                    )
                return

            if not self._session_allows_entry(now):
                self.state.auto_trade_last_reason = (
                    "Session filter active: outside allowed UTC windows "
                    f"({self.settings.auto_trade_session_utc})"
                )
                return

            entry_candidates: list[dict[str, Any]] = []
            for row in market_rows:
                symbol = str(row.get("symbol") or "")
                if not symbol or symbol not in self._runtime_auto_trade_symbols_set:
                    continue

                if row.get("error"):
                    status_reason = f"{symbol}: market data error"
                    continue

                blocked, blocked_reason = self._is_extreme_volatility(row)
                if blocked:
                    status_reason = f"{symbol}: volatility filter ({blocked_reason})"
                    continue

                if symbol in self.state.auto_trade_positions:
                    status_reason = f"{symbol}: position already open"
                    continue

                if (
                    self.settings.auto_trade_max_open_positions > 0
                    and len(self.state.auto_trade_positions)
                    >= self.settings.auto_trade_max_open_positions
                ):
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

                cost_ok, cost_reason = self._execution_cost_gate_allows(row, entry_side)
                if not cost_ok:
                    status_reason = f"{symbol}: execution cost gate ({cost_reason})"
                    continue

                ai_ok, ai_reason = self._ai_filter_allows(
                    row,
                    entry_side,
                    min_confidence_override=int(adaptive_profile["ai_min_confidence"]),
                )
                if not ai_ok:
                    status_reason = f"{symbol}: AI filter blocked ({ai_reason})"
                    continue

                if entry_side == "SHORT" and not self.settings.paper_trading:
                    status_reason = (
                        f"{symbol}: short entry blocked in LIVE spot mode "
                        "(use PAPER or derivatives integration)"
                    )
                    continue

                entry_candidates.append(
                    {
                        "symbol": symbol,
                        "row": row,
                        "entry_side": entry_side,
                        "reason_text": reason_text,
                        "rank_score": self._entry_rank_score(row, entry_side),
                    }
                )

            if self.settings.auto_trade_symbol_rank_enabled and entry_candidates:
                entry_candidates.sort(
                    key=lambda candidate: float(candidate.get("rank_score") or 0.0),
                    reverse=True,
                )
                top_n = max(1, self.settings.auto_trade_symbol_rank_top_n)
                if len(entry_candidates) > top_n:
                    entry_candidates = entry_candidates[:top_n]

            for candidate in entry_candidates:
                symbol = str(candidate.get("symbol") or "")
                row = candidate.get("row") if isinstance(candidate.get("row"), dict) else {}
                entry_side = str(candidate.get("entry_side") or "LONG")
                reason_text = str(candidate.get("reason_text") or "")
                rank_score = float(candidate.get("rank_score") or 0.0)

                if not symbol or not isinstance(row, dict):
                    continue
                if symbol in self.state.auto_trade_positions:
                    status_reason = f"{symbol}: position already open"
                    continue
                if (
                    self.settings.auto_trade_max_open_positions > 0
                    and len(self.state.auto_trade_positions)
                    >= self.settings.auto_trade_max_open_positions
                ):
                    status_reason = (
                        "max open positions reached "
                        f"({len(self.state.auto_trade_positions)}/"
                        f"{self.settings.auto_trade_max_open_positions})"
                    )
                    break

                current_price = safe_float(row.get("price"))
                if current_price is None or current_price <= 0:
                    status_reason = f"{symbol}: waiting valid price"
                    continue

                notional_usdt = self._base_notional_usdt(
                    available_usdt,
                    row,
                    risk_multiplier=effective_risk_multiplier,
                )

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
                        meta={
                            "event": "ENTRY_FAILED",
                            "position_side": entry_side,
                            "order_side": exchange_side.upper(),
                            "price": current_price,
                            "amount": amount,
                        },
                    )
                    self._record_journal(
                        symbol=symbol,
                        event_type="ENTRY_FAILED",
                        side=entry_side,
                        reason=error_message[:180],
                        pnl_usdt=None,
                        pnl_pct=None,
                        notional_usdt=notional_usdt,
                        price=current_price,
                        amount=amount,
                    )
                    continue

                filled_amount = safe_float(order_result.get("filled")) or amount
                entry_price = safe_float(order_result.get("average")) or current_price
                if not self._paper_wallet_on_entry(notional_usdt):
                    status_reason = (
                        f"{symbol}: insufficient PAPER wallet USDT "
                        f"({notional_usdt:.2f} required)"
                    )
                    self.push_auto_trade_event(
                        symbol,
                        "ENTRY_FAILED",
                        f"{exchange_side.upper()} ({entry_side})",
                        "Insufficient PAPER wallet USDT",
                        price=current_price,
                        amount=amount,
                        success=False,
                    )
                    continue

                self.state.auto_trade_positions[symbol] = {
                    "side": entry_side,
                    "entry_price": entry_price,
                    "amount": filled_amount,
                    "initial_amount": filled_amount,
                    "notional_usdt": notional_usdt,
                    "opened_at": int(now),
                    "mode": str(order_result.get("mode") or "live"),
                    "highest_price": entry_price,
                    "lowest_price": entry_price,
                    "partial_tp_done": False,
                    "break_even_armed": False,
                }
                self._copy_trade_on_entry(
                    symbol=symbol,
                    position_side=entry_side,
                    order_side=exchange_side,
                    entry_price=entry_price,
                    amount=filled_amount,
                    notional_usdt=notional_usdt,
                )
                self.state.auto_trade_last_action_ts[symbol] = now
                self.state.auto_trade_cooldown_until[symbol] = now + self._next_cooldown_seconds(
                    row,
                    extra_multiplier=float(adaptive_profile["cooldown_mult"]),
                )
                if available_usdt is not None:
                    available_usdt = max(0.0, available_usdt - notional_usdt)
                status_reason = (
                    f"{symbol}: {entry_side} opened at {entry_price:.6f} • "
                    f"rank {rank_score:.1f} • profile {adaptive_profile['name']}"
                )
                if forward_reason:
                    status_reason = f"{status_reason} • {forward_reason}"

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
                    meta={
                        "event": "ENTRY",
                        "position_side": entry_side,
                        "order_side": exchange_side.upper(),
                        "price": entry_price,
                        "amount": filled_amount,
                        "mode": str(order_result.get("mode") or "live").upper(),
                    },
                )
                self._record_journal(
                    symbol=symbol,
                    event_type="ENTRY",
                    side=entry_side,
                    reason=reason_text or "Rule entry passed",
                    pnl_usdt=None,
                    pnl_pct=None,
                    notional_usdt=notional_usdt,
                    price=entry_price,
                    amount=filled_amount,
                    metadata={
                        "rsi": row.get("rsi"),
                        "atr_pct": row.get("atr_pct"),
                        "spread_pct": row.get("spread_pct"),
                        "volume_ratio": row.get("volume_ratio"),
                        "rank_score": rank_score,
                        "guardrail_mult": round(forward_risk_multiplier, 4),
                        "adaptive_profile": adaptive_profile["name"],
                        "adaptive_mult": round(float(adaptive_profile["risk_mult"]), 4),
                    },
                )

            if forward_reason and "guardrail" not in status_reason.lower():
                status_reason = f"{status_reason} • {forward_reason}"
            if "profile" not in status_reason.lower():
                status_reason = f"{status_reason} • profile {adaptive_profile['name']}"
            self.state.auto_trade_last_reason = status_reason

    def build_auto_trade_status(self, selected_symbol: str) -> dict[str, Any]:
        with self.state.auto_trade_lock:
            now = time.time()
            day_key = self.utc_day_key()
            daily_pnl = self.state.auto_trade_daily_pnl.get(day_key, 0.0)
            position = self.state.auto_trade_positions.get(selected_symbol)

            return {
                "enabled": self.settings.auto_trade_enabled,
                "paper_trading": self.settings.paper_trading,
                "exchange": self.settings.exchange_name,
                "symbols": self._runtime_auto_trade_symbols,
                "trade_size_usdt": self.settings.trade_size_usdt,
                "trade_size_usdt_min": round(self.settings.trade_size_usdt_min, 4),
                "trade_size_usdt_max": round(self.settings.trade_size_usdt_max, 4),
                "trade_size_percent": round(self.settings.trade_size_percent, 4),
                "risk_multiplier": round(self.state.auto_trade_last_risk_multiplier, 4),
                "guardrail_active": self.state.auto_trade_guardrail_active,
                "guardrail_reason": self.state.auto_trade_guardrail_reason,
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
                "take_profit_run_enabled": self.settings.auto_trade_take_profit_run_enabled,
                "take_profit_run_min_pnl_pct": round(
                    self.settings.auto_trade_take_profit_run_min_pnl_pct,
                    4,
                ),
                "ai_filter_enabled": self.settings.ai_filter_enabled,
                "ai_filter_min_confidence": self.settings.ai_filter_min_confidence,
                "ai_filter_min_score_abs": round(self.settings.ai_filter_min_score_abs, 2),
                "entry_confirm_ema_stack": self.settings.auto_trade_entry_confirm_ema_stack,
                "entry_confirm_macd": self.settings.auto_trade_entry_confirm_macd,
                "entry_require_macd_data": self.settings.auto_trade_entry_require_macd_data,
                "min_volume_ratio": round(self.settings.auto_trade_min_volume_ratio, 4),
                "min_strength_confidence": self.settings.auto_trade_min_strength_confidence,
                "session_filter_enabled": self.settings.auto_trade_session_filter_enabled,
                "session_utc": self.settings.auto_trade_session_utc,
                "extreme_volatility_block_enabled": (
                    self.settings.auto_trade_extreme_volatility_block_enabled
                ),
                "max_atr_pct": round(self.settings.auto_trade_max_atr_pct, 4),
                "target_atr_pct": round(self.settings.auto_trade_target_atr_pct, 4),
                "max_abs_change_24h_pct": round(self.settings.auto_trade_max_abs_change_24h_pct, 4),
                "execution_cost_gate_enabled": self.settings.auto_trade_execution_cost_gate_enabled,
                "max_spread_pct": round(self.settings.auto_trade_max_spread_pct, 4),
                "estimated_fee_pct": round(self.settings.auto_trade_estimated_fee_pct, 4),
                "estimated_slippage_pct": round(
                    self.settings.auto_trade_estimated_slippage_pct,
                    4,
                ),
                "min_edge_pct": round(self.settings.auto_trade_min_edge_pct, 4),
                "symbol_rank_enabled": self.settings.auto_trade_symbol_rank_enabled,
                "symbol_rank_top_n": self.settings.auto_trade_symbol_rank_top_n,
                "partial_take_profit_enabled": self.settings.auto_trade_partial_take_profit_enabled,
                "partial_take_profit_pct": round(self.settings.auto_trade_partial_take_profit_pct, 4),
                "partial_take_profit_ratio": round(
                    self.settings.auto_trade_partial_take_profit_ratio,
                    4,
                ),
                "break_even_enabled": self.settings.auto_trade_break_even_enabled,
                "break_even_trigger_pct": round(
                    self.settings.auto_trade_break_even_trigger_pct,
                    4,
                ),
                "break_even_buffer_pct": round(self.settings.auto_trade_break_even_buffer_pct, 4),
                "time_stop_enabled": self.settings.auto_trade_time_stop_enabled,
                "time_stop_minutes": self.settings.auto_trade_time_stop_minutes,
                "time_stop_min_pnl_pct": round(self.settings.auto_trade_time_stop_min_pnl_pct, 4),
                "adaptive_enabled": self.settings.auto_trade_auto_adapt_enabled,
                "adaptive_profile": self.state.auto_trade_adaptive_profile,
                "adaptive_reason": self.state.auto_trade_adaptive_reason,
                "adaptive_ai_min_confidence": self.state.auto_trade_adaptive_ai_min_confidence,
                "adaptive_risk_multiplier": round(
                    self.state.auto_trade_adaptive_risk_multiplier,
                    4,
                ),
                "adaptive_cooldown_multiplier": round(
                    self.state.auto_trade_adaptive_cooldown_multiplier,
                    4,
                ),
                "auto_convert_to_usdt": self.settings.auto_trade_auto_convert_to_usdt,
                "auto_convert_min_usdt": round(self.settings.auto_trade_auto_convert_min_usdt, 4),
                "auto_convert_interval_seconds": (
                    self.settings.auto_trade_auto_convert_interval_seconds
                ),
                "cooldown_seconds": self.settings.cooldown_seconds,
                "cooldown_min_seconds": self.settings.cooldown_min_seconds,
                "cooldown_max_seconds": self.settings.cooldown_max_seconds,
                "daily_pnl_usdt": round(daily_pnl, 4),
                "daily_loss_limit_usdt": round(self.settings.max_daily_loss_usdt, 4),
                "halted": (
                    self.state.auto_trade_halt_day == day_key
                    or self.state.auto_trade_halt_until > now
                ),
                "halt_until": int(self.state.auto_trade_halt_until),
                "halt_reason": self.state.auto_trade_halt_reason,
                "consecutive_losses": self.state.auto_trade_consecutive_losses,
                "open_positions": len(self.state.auto_trade_positions),
                "selected_position": position,
                "recent_events": list(self.state.auto_trade_events)[-12:],
                "recent_journal": list(self.state.auto_trade_journal)[-120:],
                "journal_count": len(self.state.auto_trade_journal),
                "daily_recap_last_day": self.state.auto_trade_last_daily_recap_day,
                "stats_by_symbol": self.state.auto_trade_stats_by_symbol,
                "copy_trade_enabled": self._copy_trade_is_enabled(),
                "copy_trade_followers": [
                    {
                        "name": str(follower["name"]),
                        "multiplier": round(float(follower["multiplier"]), 4),
                    }
                    for follower in self._copy_followers
                ],
                "copy_trade_slippage_bps": round(self.settings.copy_trade_slippage_bps, 2),
                "copy_trade_recent_events": list(self.state.copy_trade_events)[-20:],
                "copy_trade_stats": self.state.copy_trade_stats,
                "copy_trade_open_positions": {
                    follower: len(positions)
                    for follower, positions in self.state.copy_trade_positions.items()
                },
                "last_reason": self.state.auto_trade_last_reason,
            }
