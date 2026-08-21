from __future__ import annotations

from typing import Any


def build_diagnostic_snapshot(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    concentration = report.get("concentration") or {}
    multi_start = report.get("multi_start") or {}
    capacity = report.get("capacity") or {}
    exposure = capacity.get("exposure") if isinstance(capacity, dict) else {}
    exposure = exposure if isinstance(exposure, dict) else {}
    issues = report.get("issues") or []
    return {
        "issue_count": int(len(issues)),
        "issue_types": [
            str(item.get("issue_type"))
            for item in issues
            if isinstance(item, dict) and str(item.get("issue_type") or "").strip()
        ],
        "feedback_lines": [str(item) for item in report.get("feedback_lines") or [] if str(item).strip()],
        "top1_slot_profit_share": concentration.get("top1_slot_profit_share"),
        "trade_return_hhi": concentration.get("trade_return_hhi"),
        "avg_trade_return": concentration.get("avg_trade_return"),
        "stock_equal_avg_trade_return": concentration.get("stock_equal_avg_trade_return"),
        "robust_score": multi_start.get("robust_score") if isinstance(multi_start, dict) else None,
        "oos_gap": multi_start.get("oos_gap") if isinstance(multi_start, dict) else None,
        "worst_cum_return": multi_start.get("worst_cum_return") if isinstance(multi_start, dict) else None,
        "p95_participation_rate": capacity.get("p95_participation_rate") if isinstance(capacity, dict) else None,
        "impact_cost_bps_estimate": capacity.get("impact_cost_bps_estimate") if isinstance(capacity, dict) else None,
        "max_single_name_weight": exposure.get("max_single_name_weight"),
        "max_gross_exposure": exposure.get("max_gross_exposure"),
    }


__all__ = ["build_diagnostic_snapshot"]
