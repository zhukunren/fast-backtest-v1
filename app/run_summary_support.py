from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from backtest.reporting.benchmark import build_benchmark_curves_from_net_value_payload
from backtest.reporting.market_state import compute_market_state_from_net_value_payload, summarize_market_state_payload
from backtest.result import CurvePayload, RunResult

_UNIVERSE_PREFIX_LABELS = {
    "300": "创业板",
    "301": "创业板",
    "688": "科创板",
    "689": "科创板",
    "8": "北交所",
    "4": "北交所",
}


@dataclass(frozen=True, slots=True)
class AssessSummaryMetrics:
    stock_count: int
    trade_count: int
    avg_rate: float
    win_rate: float
    unweighted_avg: float | None
    stock_equal_avg: float | None
    topn_trade_count: int
    topn_avg: float
    topn_win_rate: float


def _sum_numeric_column(summary_df: pd.DataFrame, name: str) -> float:
    if name not in summary_df.columns:
        return 0.0
    return float(pd.to_numeric(summary_df[name], errors="coerce").fillna(0).sum())


def _weighted_average(summary_df: pd.DataFrame, avg_col: str, count_col: str) -> float | None:
    if avg_col not in summary_df.columns or count_col not in summary_df.columns:
        return None
    avg_series = pd.to_numeric(summary_df[avg_col], errors="coerce").fillna(0.0)
    count_series = pd.to_numeric(summary_df[count_col], errors="coerce").fillna(0.0)
    denom = float(count_series.sum())
    if denom <= 0:
        return 0.0
    return float((avg_series * count_series).sum() / denom)


def build_assess_summary_metrics(summary_df: pd.DataFrame | None) -> AssessSummaryMetrics | None:
    if summary_df is None or summary_df.empty:
        return None

    stock_count = int(_sum_numeric_column(summary_df, "评估股票数")) if "评估股票数" in summary_df.columns else len(summary_df)
    trade_count = _sum_numeric_column(summary_df, "交易笔数")
    positive_trade_count = _sum_numeric_column(summary_df, "收益率为正的交易笔数")
    trade_mul = _sum_numeric_column(summary_df, "单笔平均收益率*交易笔数")
    topn_trade_count = _sum_numeric_column(summary_df, "TopN后交易段交易笔数")

    avg_rate = trade_mul / trade_count if trade_count else 0.0
    win_rate = positive_trade_count / trade_count if trade_count else 0.0

    return AssessSummaryMetrics(
        stock_count=stock_count,
        trade_count=int(trade_count),
        avg_rate=avg_rate,
        win_rate=win_rate,
        unweighted_avg=_weighted_average(summary_df, "未加权平均每笔收益率", "未加权交易笔数"),
        stock_equal_avg=_weighted_average(summary_df, "按股票等权平均单笔收益率", "评估股票数"),
        topn_trade_count=int(topn_trade_count),
        topn_avg=_sum_numeric_column(summary_df, "TopN后交易段平均每笔收益率"),
        topn_win_rate=_sum_numeric_column(summary_df, "TopN后交易段胜率"),
    )


def build_assess_summary_lines(summary_df: pd.DataFrame | None) -> list[str]:
    metrics = build_assess_summary_metrics(summary_df)
    if metrics is None:
        return []

    lines = [
        f"评估股票数: {metrics.stock_count}",
        f"总交易笔数: {metrics.trade_count}",
        f"平均单笔收益率: {metrics.avg_rate * 100:.2f}%",
    ]
    if metrics.unweighted_avg is not None:
        lines.append(f"未加权平均单笔收益率: {metrics.unweighted_avg * 100:.2f}%")
    if metrics.stock_equal_avg is not None:
        lines.append(f"按股票等权平均单笔收益率: {metrics.stock_equal_avg * 100:.2f}%")
    lines.append(f"胜率: {metrics.win_rate * 100:.2f}%")
    if metrics.topn_trade_count > 0:
        lines.extend(
            [
                f"TopN后交易段交易笔数: {metrics.topn_trade_count}",
                f"TopN后交易段平均每笔收益率: {metrics.topn_avg * 100:.2f}%",
                f"TopN后交易段胜率: {metrics.topn_win_rate * 100:.2f}%",
            ]
        )
    return lines


def build_curve_summary_lines(
    *,
    result: RunResult,
    diagnostic_path,
    diagnostic_report: dict[str, Any] | None,
) -> list[str]:
    payload = result.payload
    if not isinstance(payload, CurvePayload) or payload.metrics is None:
        return []
    metrics = payload.metrics

    lines: list[str] = []
    if result.request.signal_stock_count > 0:
        lines.append(f"出现买入信号的股票数量: {result.request.signal_stock_count}")
    lines.extend(
        [
            f"累计收益率: {(metrics.final_net_value - 1) * 100:.2f}%",
            f"最大回撤: {metrics.max_drawdown * 100:.2f}%",
        ]
    )
    if metrics.actual_segment_trade_count > 0:
        lines.extend(
            [
                f"实际交易段交易笔数: {metrics.actual_segment_trade_count}",
                f"实际交易段平均每笔收益率: {metrics.actual_segment_avg_trade_return * 100:.2f}%",
                f"实际交易段胜率: {metrics.actual_segment_win_rate * 100:.2f}%",
            ]
        )

    report = diagnostic_report or {}
    if diagnostic_path is not None:
        lines.append(f"诊断文件: {diagnostic_path}")
        issue_count = int(len(report.get("issues") or []))
        lines.append(f"诊断问题数: {issue_count}")
        for line in list(report.get("feedback_lines") or [])[:3]:
            lines.append(f"诊断提示: {line}")
    return lines


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def build_universe_summary_line(cfg) -> str:
    backtest_cfg = getattr(cfg, "backtest", None)
    prefixes = tuple(getattr(backtest_cfg, "universe_prefixes", ()) or ())
    labels = []
    for raw_prefix in prefixes:
        prefix = str(raw_prefix or "").strip()
        if not prefix:
            continue
        labels.append(_UNIVERSE_PREFIX_LABELS.get(prefix, f"{prefix}*"))
    scope = "、".join(_dedupe_preserve_order(labels)) if labels else "全市场"

    filters = []
    if bool(getattr(backtest_cfg, "exclude_bj", False)):
        filters.append("排除北交所")
    if bool(getattr(backtest_cfg, "exclude_st", False)):
        filters.append("排除ST")
    suffix = f"（{'、'.join(filters)}）" if filters else ""
    return f"回测范围: {scope}{suffix}"


def build_market_state_summary_line(result: RunResult, *, cfg=None) -> str | None:
    payload = result.payload
    df = payload.equity_curve if isinstance(payload, CurvePayload) else None
    if not isinstance(df, pd.DataFrame) or "trade_date" not in df.columns or "sum_net_value" not in df.columns:
        return None

    work = df.loc[:, ["trade_date", "sum_net_value"]].copy()
    work["trade_date"] = pd.to_datetime(work["trade_date"], errors="coerce")
    work["sum_net_value"] = pd.to_numeric(work["sum_net_value"], errors="coerce")
    work = (
        work.dropna(subset=["trade_date", "sum_net_value"])
        .sort_values("trade_date", kind="mergesort")
        .drop_duplicates(subset=["trade_date"], keep="last")
        .reset_index(drop=True)
    )
    if len(work) < 3:
        return None
    payload_input = {
        "dates": work["trade_date"].dt.strftime("%Y-%m-%d").tolist(),
        "net_value": [round(float(value), 6) for value in work["sum_net_value"].tolist()],
    }
    benchmark_payload = build_benchmark_curves_from_net_value_payload(payload_input, cfg=cfg)
    if benchmark_payload:
        payload_input = {**payload_input, **benchmark_payload}
    payload = compute_market_state_from_net_value_payload(payload_input)
    return summarize_market_state_payload(payload) if isinstance(payload, dict) else None


def build_result_meta_payload(result: RunResult) -> dict[str, Any] | None:
    payload = result.payload
    if not isinstance(payload, CurvePayload) or payload.metrics is None:
        return None
    metrics = payload.metrics
    return {
        "final_net_value": float(metrics.final_net_value),
        "cum_return": float(metrics.final_net_value - 1),
        "max_drawdown": float(metrics.max_drawdown),
        "signal_stock_count": int(result.request.signal_stock_count),
        "actual_segment_trade_count": int(metrics.actual_segment_trade_count),
        "actual_segment_avg_trade_return": float(metrics.actual_segment_avg_trade_return),
        "actual_segment_win_rate": float(metrics.actual_segment_win_rate),
    }


__all__ = [
    "AssessSummaryMetrics",
    "build_market_state_summary_line",
    "build_universe_summary_line",
    "build_assess_summary_lines",
    "build_assess_summary_metrics",
    "build_curve_summary_lines",
    "build_result_meta_payload",
]
