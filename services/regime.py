from __future__ import annotations

from typing import Any

import numpy as np

from services.indicators import safe_float


def _feature(row: dict[str, Any]) -> list[float]:
    change = safe_float(row.get("change_24h")) or 0.0
    atr = safe_float(row.get("atr_pct")) or 0.0
    volume = safe_float(row.get("volume_ratio")) or 1.0
    hurst = safe_float(row.get("hurst_exponent")) or 0.5
    ai_score = safe_float(row.get("ai_score")) or 0.0
    micro = safe_float(row.get("microstructure_pressure")) or 0.0
    return [
        max(-1.0, min(1.0, change / 10.0)),
        max(0.0, min(1.0, atr / 5.0)),
        max(-1.0, min(1.0, (volume - 1.0) / 2.0)),
        max(-1.0, min(1.0, (hurst - 0.5) * 4.0)),
        max(-1.0, min(1.0, ai_score / 5.0)),
        max(-1.0, min(1.0, micro / 50.0)),
    ]


def _label_cluster(rows: list[dict[str, Any]]) -> tuple[str, str]:
    if not rows:
        return "SIDEWAYS", "neutral"

    avg_change = sum(safe_float(row.get("change_24h")) or 0.0 for row in rows) / len(rows)
    avg_atr = sum(safe_float(row.get("atr_pct")) or 0.0 for row in rows) / len(rows)
    avg_ai = sum(safe_float(row.get("ai_score")) or 0.0 for row in rows) / len(rows)
    avg_hurst = sum(safe_float(row.get("hurst_exponent")) or 0.5 for row in rows) / len(rows)

    if avg_atr >= 3.5:
        return "HIGH_VOL", "risk_off"
    if avg_change >= 1.2 and avg_ai >= 0.6:
        return "BULL", "trend"
    if avg_change <= -1.2 and avg_ai <= -0.6:
        return "BEAR", "trend"
    if avg_hurst <= 0.43:
        return "CHOP", "mean_revert"
    return "SIDEWAYS", "neutral"


class MarketRegimeDetector:
    def __init__(self, *, enabled: bool, clusters: int = 3, iterations: int = 8) -> None:
        self.enabled = enabled
        self.clusters = max(2, int(clusters))
        self.iterations = max(1, int(iterations))

    @staticmethod
    def _fallback(row: dict[str, Any]) -> tuple[str, str, float]:
        change = safe_float(row.get("change_24h")) or 0.0
        atr = safe_float(row.get("atr_pct")) or 0.0
        ai = safe_float(row.get("ai_score")) or 0.0
        hurst = safe_float(row.get("hurst_exponent")) or 0.5
        if atr >= 3.5:
            return "HIGH_VOL", "risk_off", min(100.0, 55.0 + atr * 8.0)
        if change >= 1.2 and ai >= 0.5:
            return "BULL", "trend", min(100.0, 55.0 + abs(change) * 5.0 + abs(ai) * 5.0)
        if change <= -1.2 and ai <= -0.5:
            return "BEAR", "trend", min(100.0, 55.0 + abs(change) * 5.0 + abs(ai) * 5.0)
        if hurst <= 0.43:
            return "CHOP", "mean_revert", min(100.0, 55.0 + (0.43 - hurst) * 100.0)
        return "SIDEWAYS", "neutral", 50.0

    def assign(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        clean_rows = [row for row in rows if not row.get("error") and safe_float(row.get("price"))]
        if not self.enabled or len(clean_rows) < self.clusters:
            counts: dict[str, int] = {}
            for row in rows:
                label, category, confidence = self._fallback(row)
                row["market_regime"] = label
                row["market_regime_category"] = category
                row["market_regime_confidence"] = round(confidence, 2)
                counts[label] = counts.get(label, 0) + 1
            dominant = max(counts.items(), key=lambda item: item[1])[0] if counts else "SIDEWAYS"
            return {"enabled": self.enabled, "dominant": dominant, "counts": counts, "clusters": []}

        matrix = np.array([_feature(row) for row in clean_rows], dtype=float)
        k = min(self.clusters, len(clean_rows))
        seed_indices = np.linspace(0, len(clean_rows) - 1, k, dtype=int)
        centers = matrix[seed_indices].copy()

        labels = np.zeros(len(clean_rows), dtype=int)
        for _ in range(self.iterations):
            distances = np.linalg.norm(matrix[:, None, :] - centers[None, :, :], axis=2)
            labels = np.argmin(distances, axis=1)
            for index in range(k):
                members = matrix[labels == index]
                if len(members) > 0:
                    centers[index] = members.mean(axis=0)

        cluster_meta: dict[int, dict[str, Any]] = {}
        for index in range(k):
            members = [
                row
                for member_index, row in enumerate(clean_rows)
                if int(labels[member_index]) == index
            ]
            label, category = _label_cluster(members)
            cluster_meta[index] = {"label": label, "category": category, "count": len(members)}

        counts: dict[str, int] = {}
        for member_index, row in enumerate(clean_rows):
            cluster_id = int(labels[member_index])
            meta = cluster_meta[cluster_id]
            distance = float(np.linalg.norm(matrix[member_index] - centers[cluster_id]))
            confidence = max(35.0, min(100.0, 100.0 - distance * 45.0))
            label = str(meta["label"])
            row["market_regime"] = label
            row["market_regime_category"] = meta["category"]
            row["market_regime_confidence"] = round(confidence, 2)
            row["market_regime_cluster"] = cluster_id
            counts[label] = counts.get(label, 0) + 1

        clean_ids = {id(row) for row in clean_rows}
        for row in rows:
            if id(row) in clean_ids:
                continue
            label, category, confidence = self._fallback(row)
            row["market_regime"] = label
            row["market_regime_category"] = category
            row["market_regime_confidence"] = round(confidence, 2)

        dominant = max(counts.items(), key=lambda item: item[1])[0] if counts else "SIDEWAYS"
        return {
            "enabled": True,
            "dominant": dominant,
            "counts": counts,
            "clusters": [
                {
                    "id": cluster_id,
                    "label": meta["label"],
                    "category": meta["category"],
                    "count": meta["count"],
                }
                for cluster_id, meta in sorted(cluster_meta.items())
            ],
        }
