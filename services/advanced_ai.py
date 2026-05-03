from __future__ import annotations

import math
from typing import Any

from services.indicators import safe_float
from services.state import RuntimeState


MODEL_NAMES = (
    "xgboost",
    "lightgbm",
    "random_forest",
    "extra_trees",
    "logistic_regression",
    "bayesian_regime",
)


def _empty_stats() -> dict[str, Any]:
    return {
        "predictions": 0,
        "correct": 0,
        "wrong": 0,
        "pnl_usdt": 0.0,
        "weight": 1.0,
    }


def ensure_model_stats(state: RuntimeState) -> dict[str, dict[str, Any]]:
    for name in MODEL_NAMES:
        state.advanced_ai_model_stats.setdefault(name, _empty_stats())
    return state.advanced_ai_model_stats


class AdvancedDecisionEngine:
    def __init__(
        self,
        state: RuntimeState,
        *,
        enabled: bool,
        quantum_enabled: bool,
    ) -> None:
        self.state = state
        self.enabled = enabled
        self.quantum_enabled = quantum_enabled
        ensure_model_stats(state)

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _tanh_score(value: float) -> float:
        return math.tanh(value) * 5.0

    @staticmethod
    def _bias_from_score(score: float) -> str:
        if score >= 1.0:
            return "BUY"
        if score <= -1.0:
            return "SELL"
        return "HOLD"

    def _adaptive_weights(self) -> dict[str, float]:
        stats_map = ensure_model_stats(self.state)
        raw_weights: dict[str, float] = {}
        for name in MODEL_NAMES:
            stats = stats_map.get(name, {})
            predictions = int(safe_float(stats.get("predictions")) or 0)
            correct = int(safe_float(stats.get("correct")) or 0)
            pnl = float(safe_float(stats.get("pnl_usdt")) or 0.0)
            win_rate = (correct / predictions) if predictions > 0 else 0.5
            sample_factor = min(1.0, predictions / 20.0)
            pnl_factor = self._clamp(pnl / max(1.0, predictions * 2.0), -0.35, 0.35)
            raw = 1.0 + ((win_rate - 0.5) * sample_factor) + pnl_factor
            raw_weights[name] = self._clamp(raw, 0.35, 1.85)

        total = sum(raw_weights.values()) or 1.0
        weights = {name: value / total for name, value in raw_weights.items()}
        for name, weight in weights.items():
            stats_map[name]["weight"] = round(weight, 4)
        return weights

    def _feature_state(
        self,
        row: dict[str, Any],
        microstructure: dict[str, Any],
        sentiment: dict[str, Any],
    ) -> dict[str, float]:
        price = safe_float(row.get("price")) or safe_float(row.get("last_close")) or 0.0
        ema20 = safe_float(row.get("ema20")) or price
        ema50 = safe_float(row.get("ema50")) or ema20
        rsi = safe_float(row.get("rsi")) or 50.0
        macd = safe_float(row.get("macd")) or 0.0
        macd_signal = safe_float(row.get("macd_signal")) or 0.0
        strength_score = safe_float(row.get("strength_score")) or 0.0
        change_24h = safe_float(row.get("change_24h")) or 0.0
        atr_pct = safe_float(row.get("atr_pct")) or 0.0
        volume_ratio = safe_float(row.get("volume_ratio")) or 1.0
        spread_pct = safe_float(row.get("spread_pct")) or 0.0

        vortex_diff = safe_float(row.get("vortex_diff")) or 0.0
        stoch_rsi_k = safe_float(row.get("stoch_rsi_k")) or 50.0
        ultimate = safe_float(row.get("ultimate_oscillator")) or 50.0
        keltner_position = safe_float(row.get("keltner_position")) or 50.0
        hurst = safe_float(row.get("hurst_exponent")) or 0.5
        fourier_strength = safe_float(row.get("fourier_cycle_strength")) or 0.0

        micro_pressure = safe_float(microstructure.get("pressure_score")) or 0.0
        depth_imbalance = safe_float(microstructure.get("depth_imbalance_pct")) or 0.0
        trade_imbalance = safe_float(microstructure.get("trade_imbalance_pct")) or 0.0
        sentiment_score = safe_float(sentiment.get("score")) or 0.0

        price_scale = max(abs(price), 1e-9)
        trend = self._clamp(((price - ema50) / price_scale) * 50.0, -1.0, 1.0)
        ema_stack = 1.0 if ema20 >= ema50 else -1.0
        rsi_momentum = self._clamp((rsi - 50.0) / 25.0, -1.0, 1.0)
        rsi_reversion = self._clamp((50.0 - rsi) / 30.0, -1.0, 1.0)
        macd_edge = self._clamp(((macd - macd_signal) / price_scale) * 5000.0, -1.0, 1.0)
        atr_fit = 1.0 - self._clamp(abs(atr_pct - 0.9) / 2.5, 0.0, 1.0)
        spread_penalty = self._clamp(spread_pct / 0.2, 0.0, 1.0)

        return {
            "trend": trend,
            "ema_stack": ema_stack,
            "rsi_momentum": rsi_momentum,
            "rsi_reversion": rsi_reversion,
            "macd_edge": macd_edge,
            "strength": self._clamp(strength_score / 5.0, -1.0, 1.0),
            "change": self._clamp(change_24h / 10.0, -1.0, 1.0),
            "atr_fit": atr_fit,
            "volume": self._clamp((volume_ratio - 1.0) / 1.5, -1.0, 1.0),
            "spread_penalty": spread_penalty,
            "vortex": self._clamp(vortex_diff * 3.0, -1.0, 1.0),
            "stoch": self._clamp((stoch_rsi_k - 50.0) / 50.0, -1.0, 1.0),
            "ultimate": self._clamp((ultimate - 50.0) / 50.0, -1.0, 1.0),
            "keltner": self._clamp((keltner_position - 50.0) / 50.0, -1.0, 1.0),
            "hurst_trend": self._clamp((hurst - 0.5) * 4.0, -1.0, 1.0),
            "cycle_strength": self._clamp(fourier_strength / 100.0, 0.0, 1.0),
            "micro": self._clamp(micro_pressure / 50.0, -1.0, 1.0),
            "depth": self._clamp(depth_imbalance / 50.0, -1.0, 1.0),
            "tape": self._clamp(trade_imbalance / 50.0, -1.0, 1.0),
            "sentiment": self._clamp(sentiment_score / 100.0, -1.0, 1.0),
        }

    def _model_scores(self, features: dict[str, float]) -> dict[str, float]:
        trend_stack = features["trend"] + (features["ema_stack"] * 0.35)
        momentum = (
            features["rsi_momentum"]
            + features["macd_edge"]
            + features["ultimate"]
            + features["vortex"]
        ) / 4.0
        micro = (features["micro"] + features["depth"] + features["tape"]) / 3.0
        regime_trend = 1.0 + max(0.0, features["hurst_trend"]) * 0.35
        regime_revert = 1.0 + max(0.0, -features["hurst_trend"]) * 0.35
        penalty = features["spread_penalty"] * 0.45

        lightgbm_vote = 0.0
        lightgbm_vote += 0.9 if features["trend"] > 0.15 else -0.9 if features["trend"] < -0.15 else 0.0
        lightgbm_vote += 0.7 if features["macd_edge"] > 0 else -0.7
        lightgbm_vote += 0.45 if features["vortex"] > 0 else -0.45
        lightgbm_vote += 0.35 if features["volume"] > 0 else -0.1
        lightgbm_vote += 0.35 if micro > 0.12 else -0.35 if micro < -0.12 else 0.0
        lightgbm_vote += features["sentiment"] * 0.35
        lightgbm_vote -= penalty

        forest_votes = [
            trend_stack,
            momentum,
            features["strength"],
            features["keltner"],
            micro,
            features["sentiment"],
            -features["rsi_reversion"],
        ]
        random_forest = sum(1 if vote > 0 else -1 if vote < 0 else 0 for vote in forest_votes) / len(forest_votes)
        extra_trees = (
            (features["change"] * 0.55)
            + (features["stoch"] * 0.35)
            + (features["cycle_strength"] * features["trend"] * 0.25)
            + (features["tape"] * 0.45)
            + (features["sentiment"] * 0.25)
            - penalty
        )
        logistic = (
            (trend_stack * 0.95)
            + (momentum * 0.9)
            + (features["strength"] * 0.7)
            + (features["volume"] * 0.35)
            + (micro * 0.45)
            + (features["sentiment"] * 0.35)
            - penalty
        )
        bayesian = (
            (trend_stack * regime_trend * 0.7)
            + (features["rsi_reversion"] * regime_revert * 0.35)
            + (features["atr_fit"] * momentum * 0.4)
            + (features["sentiment"] * 0.3)
            - penalty
        )

        return {
            "xgboost": self._tanh_score(
                (trend_stack * 1.05)
                + (momentum * 0.9)
                + (features["keltner"] * 0.45)
                + (micro * 0.55)
                + (features["sentiment"] * 0.35)
                - penalty
            ),
            "lightgbm": self._tanh_score(lightgbm_vote / 2.8),
            "random_forest": random_forest * 5.0,
            "extra_trees": self._tanh_score(extra_trees),
            "logistic_regression": self._tanh_score(logistic / 2.0),
            "bayesian_regime": self._tanh_score(bayesian / 1.6),
        }

    def _quantum_state(
        self,
        ensemble_score: float,
        features: dict[str, float],
        row: dict[str, Any],
    ) -> dict[str, Any]:
        cycle_period = safe_float(row.get("fourier_cycle_period")) or 0.0
        cycle_strength = safe_float(row.get("fourier_cycle_strength")) or 0.0
        phase = (cycle_period % 12.0) / 12.0 * math.tau if cycle_period > 0 else 0.0
        interference = math.sin(phase) * self._clamp(cycle_strength / 100.0, 0.0, 1.0) * 0.35

        buy_logit = ensemble_score + features["micro"] + features["sentiment"] + interference
        sell_logit = -ensemble_score - features["micro"] - features["sentiment"] - interference
        hold_logit = 1.25 - abs(ensemble_score) * 0.42 - abs(features["micro"]) * 0.25
        logits = {"BUY": buy_logit, "SELL": sell_logit, "HOLD": hold_logit}
        max_logit = max(logits.values())
        exps = {key: math.exp(value - max_logit) for key, value in logits.items()}
        total = sum(exps.values()) or 1.0
        probabilities = {key: value / total for key, value in exps.items()}
        entropy = -sum(value * math.log(max(value, 1e-12)) for value in probabilities.values())
        coherence = (1.0 - entropy / math.log(3.0)) * 100.0
        action = max(probabilities.items(), key=lambda item: item[1])[0]
        confidence = int(round(probabilities[action] * 100.0))

        return {
            "state_vector": {
                key.lower(): round(math.sqrt(value), 4)
                for key, value in probabilities.items()
            },
            "probabilities": {
                key.lower(): round(value * 100.0, 2)
                for key, value in probabilities.items()
            },
            "phase": round(phase, 4),
            "interference": round(interference, 4),
            "coherence": round(max(0.0, coherence), 2),
            "action": action,
            "confidence": confidence,
            "score": round((probabilities["BUY"] - probabilities["SELL"]) * 5.0, 3),
        }

    def evaluate(
        self,
        *,
        row: dict[str, Any],
        microstructure: dict[str, Any],
        sentiment: dict[str, Any],
        legacy_ai: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.enabled:
            score = float(safe_float(legacy_ai.get("score")) or 0.0)
            confidence = int(safe_float(legacy_ai.get("confidence")) or 0)
            bias = str(legacy_ai.get("bias") or self._bias_from_score(score)).upper()
            return {
                "enabled": False,
                "score": round(score, 2),
                "confidence": confidence,
                "bias": bias,
                "ensemble": {"models": [], "weights": {}},
                "quantum": None,
            }

        features = self._feature_state(row, microstructure, sentiment)
        model_scores = self._model_scores(features)
        weights = self._adaptive_weights()
        ensemble_score = sum(model_scores[name] * weights[name] for name in MODEL_NAMES)
        agreement = abs(
            sum(
                (1.0 if model_scores[name] >= 0 else -1.0) * weights[name]
                for name in MODEL_NAMES
                if abs(model_scores[name]) >= 0.75
            )
        )
        ensemble_confidence = min(
            100,
            int(round((abs(ensemble_score) / 5.0 * 72.0) + (agreement * 28.0))),
        )
        quantum = self._quantum_state(ensemble_score, features, row) if self.quantum_enabled else None
        final_score = ensemble_score
        if quantum:
            final_score = (ensemble_score * 0.72) + (float(quantum["score"]) * 0.28)

        confidence = ensemble_confidence
        if quantum:
            confidence = min(
                100,
                int(round((ensemble_confidence * 0.72) + (int(quantum["confidence"]) * 0.28))),
            )

        models = [
            {
                "name": name,
                "score": round(model_scores[name], 3),
                "bias": self._bias_from_score(model_scores[name]),
                "weight": round(weights[name], 4),
            }
            for name in MODEL_NAMES
        ]

        return {
            "enabled": True,
            "score": round(final_score, 2),
            "confidence": confidence,
            "bias": self._bias_from_score(final_score),
            "ensemble": {
                "score": round(ensemble_score, 3),
                "confidence": ensemble_confidence,
                "models": models,
                "weights": {name: round(weights[name], 4) for name in MODEL_NAMES},
            },
            "quantum": quantum,
            "features": {key: round(value, 4) for key, value in features.items()},
        }


def update_advanced_model_stats(
    state: RuntimeState,
    decision_payload: dict[str, Any] | None,
    *,
    position_side: str,
    pnl_usdt: float,
) -> None:
    if not isinstance(decision_payload, dict):
        return
    ensemble = decision_payload.get("ensemble")
    models = ensemble.get("models") if isinstance(ensemble, dict) else None
    if not isinstance(models, list):
        return

    stats_map = ensure_model_stats(state)
    side = str(position_side or "LONG").upper()
    actual_bias = "BUY" if (side == "LONG") == (pnl_usdt >= 0) else "SELL"
    pnl_abs = abs(float(pnl_usdt))

    for model in models:
        if not isinstance(model, dict):
            continue
        name = str(model.get("name") or "").strip()
        if name not in MODEL_NAMES:
            continue
        bias = str(model.get("bias") or "HOLD").upper()
        if bias not in {"BUY", "SELL"}:
            continue

        stats = stats_map.setdefault(name, _empty_stats())
        stats["predictions"] = int(safe_float(stats.get("predictions")) or 0) + 1
        correct = bias == actual_bias
        if correct:
            stats["correct"] = int(safe_float(stats.get("correct")) or 0) + 1
            stats["pnl_usdt"] = float(safe_float(stats.get("pnl_usdt")) or 0.0) + pnl_abs
        else:
            stats["wrong"] = int(safe_float(stats.get("wrong")) or 0) + 1
            stats["pnl_usdt"] = float(safe_float(stats.get("pnl_usdt")) or 0.0) - pnl_abs


def build_time_series_cv_report(
    journal_rows: list[dict[str, Any]],
    *,
    folds: int = 4,
) -> dict[str, Any]:
    realized = [
        row
        for row in journal_rows
        if row.get("event_type") in {"EXIT", "PARTIAL_EXIT"}
        and safe_float(row.get("pnl_usdt")) is not None
    ]
    realized.sort(key=lambda row: int(row.get("timestamp") or 0))
    if not realized:
        return {"folds": [], "trade_count": 0, "win_rate": 0.0, "pnl_usdt": 0.0}

    fold_count = max(1, min(int(folds), len(realized)))
    fold_size = max(1, math.ceil(len(realized) / fold_count))
    report_folds: list[dict[str, Any]] = []
    total_wins = 0
    total_pnl = 0.0

    for index in range(fold_count):
        rows = realized[index * fold_size : (index + 1) * fold_size]
        if not rows:
            continue
        pnl_values = [float(safe_float(row.get("pnl_usdt")) or 0.0) for row in rows]
        wins = sum(1 for pnl in pnl_values if pnl >= 0)
        total_wins += wins
        total_pnl += sum(pnl_values)
        report_folds.append(
            {
                "fold": index + 1,
                "start_ts": int(rows[0].get("timestamp") or 0),
                "end_ts": int(rows[-1].get("timestamp") or 0),
                "trades": len(rows),
                "win_rate": round(wins / len(rows), 4),
                "pnl_usdt": round(sum(pnl_values), 4),
            }
        )

    return {
        "folds": report_folds,
        "trade_count": len(realized),
        "win_rate": round(total_wins / len(realized), 4),
        "pnl_usdt": round(total_pnl, 4),
    }
