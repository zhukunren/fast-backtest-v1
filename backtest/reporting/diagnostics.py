"""
Structured backtest diagnostics for AI iteration and research analysis.
"""
from __future__ import annotations

from collections.abc import Callable
import math
from typing import Any

import numpy as np
import pandas as pd

from core.invariants import ensure_trade_date_normalized

from backtest.decisions import SignalDecisionArtifacts
from backtest.result import ExecutionArtifacts, LedgerExecutionArtifacts, VectorizedExecutionArtifacts
from .capacity import build_capacity_diagnostics


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        out = float(value)
    except Exception:
        return float(default)
    if not math.isfinite(out):
        return float(default)
    return out


def _compute_max_drawdown(series: pd.Series) -> float:
    if series.empty:
        return 0.0
    roll_max = series.cummax()
    drawdown = (series - roll_max) / roll_max
    return float(abs(drawdown.min()))


def _normalize_dates(df: pd.DataFrame, *, column: str = "trade_date") -> pd.DataFrame:
    if df is None or df.empty or column not in df.columns:
        return df
    out = df.copy()
    out = ensure_trade_date_normalized(out, col=column)
    if not out[column].is_monotonic_increasing:
        out = out.sort_values(column, kind="mergesort").reset_index(drop=True)
    elif not isinstance(out.index, pd.RangeIndex):
        out = out.reset_index(drop=True)
    return out


def _metrics_from_equity(merged_df: pd.DataFrame) -> dict[str, float]:
    if merged_df is None or merged_df.empty or "sum_net_value" not in merged_df.columns:
        return {}
    series = pd.to_numeric(merged_df["sum_net_value"], errors="coerce").dropna()
    if len(series) < 2:
        return {}

    final_net = float(series.iloc[-1])
    start_net = float(series.iloc[0])
    cum_return = (final_net / start_net - 1.0) if start_net > 0 else 0.0
    max_dd = _compute_max_drawdown(series)
    daily_returns = series.pct_change().dropna()
    num_days = len(series)
    annualized_return = (1.0 + cum_return) ** (252.0 / float(num_days)) - 1.0 if num_days > 1 else 0.0
    annualized_vol = float(daily_returns.std() * np.sqrt(252.0)) if len(daily_returns) > 1 else 0.0
    sharpe_ratio = annualized_return / annualized_vol if annualized_vol > 0 else 0.0
    return {
        "cum_return": float(cum_return),
        "annualized_return": float(annualized_return),
        "max_drawdown": float(max_dd),
        "sharpe_ratio": float(sharpe_ratio),
        "num_days": float(num_days),
    }


def _summary_row(df: pd.DataFrame | None) -> dict[str, Any] | None:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return None
    row = df.iloc[0].to_dict()
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (np.floating, float)):
            out[str(key)] = float(value)
        elif isinstance(value, (np.integer, int)):
            out[str(key)] = int(value)
        elif isinstance(value, pd.Timestamp):
            out[str(key)] = value.strftime("%Y-%m-%d")
        else:
            out[str(key)] = value
    return out


def _head_records(df: pd.DataFrame | None, *, limit: int = 5) -> list[dict[str, Any]]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    sample = df.head(limit).copy()
    for col in sample.columns:
        if pd.api.types.is_datetime64_any_dtype(sample[col]):
            sample[col] = pd.to_datetime(sample[col], errors="coerce").dt.strftime("%Y-%m-%d")
    return sample.to_dict(orient="records")


def _build_trade_stats(trades_df: pd.DataFrame | None) -> dict[str, float]:
    if not isinstance(trades_df, pd.DataFrame) or trades_df.empty or "盈亏" not in trades_df.columns:
        return {
            "trade_count": 0.0,
            "win_rate": 0.0,
            "avg_trade_return": 0.0,
            "stock_equal_avg_trade_return": 0.0,
        }
    returns = pd.to_numeric(trades_df["盈亏"], errors="coerce").fillna(0.0)
    trade_count = int(len(returns))
    pos_cnt = int((returns > 1e-8).sum())
    stock_equal_avg = 0.0
    if "stock_code" in trades_df.columns:
        code_ser = trades_df["stock_code"].astype(str).str.strip()
        valid_mask = code_ser.ne("") & code_ser.str.lower().ne("nan")
        if bool(valid_mask.any()):
            stock_equal_avg = float(returns.loc[valid_mask].groupby(code_ser.loc[valid_mask], sort=False).mean().mean())
    return {
        "trade_count": float(trade_count),
        "win_rate": float(pos_cnt / trade_count) if trade_count > 0 else 0.0,
        "avg_trade_return": float(returns.mean()) if trade_count > 0 else 0.0,
        "stock_equal_avg_trade_return": float(stock_equal_avg),
    }


def _build_fill_stats(fills_df: pd.DataFrame | None) -> dict[str, float]:
    if not isinstance(fills_df, pd.DataFrame) or fills_df.empty:
        return {
            "fill_count": 0.0,
            "buy_fill_count": 0.0,
            "sell_fill_count": 0.0,
            "traded_stock_count": 0.0,
        }
    side = fills_df["side"].astype(str).str.strip().str.lower() if "side" in fills_df.columns else pd.Series("", index=fills_df.index)
    stock_count = 0
    if "stock_code" in fills_df.columns:
        codes = fills_df["stock_code"].astype(str).str.strip()
        stock_count = int(codes.loc[codes.ne("") & codes.str.lower().ne("nan")].nunique())
    return {
        "fill_count": float(len(fills_df)),
        "buy_fill_count": float(side.eq("buy").sum()),
        "sell_fill_count": float(side.eq("sell").sum()),
        "traded_stock_count": float(stock_count),
    }


def _build_trade_diagnostics(
    trades_df: pd.DataFrame | None,
    fills_df: pd.DataFrame | None,
) -> dict[str, float]:
    trade_stats = _build_trade_stats(trades_df)
    fill_stats = _build_fill_stats(fills_df)
    if trade_stats["trade_count"] <= 0 and fill_stats["fill_count"] > 0:
        trade_stats["trade_count"] = fill_stats["fill_count"]
    return {**trade_stats, **fill_stats}


def _build_fill_concentration(fills_df: pd.DataFrame | None) -> dict[str, Any]:
    if not isinstance(fills_df, pd.DataFrame) or fills_df.empty or "stock_code" not in fills_df.columns:
        return {"fill_stock_contributions": []}
    codes = fills_df["stock_code"].astype(str).str.strip()
    valid = codes.ne("") & codes.str.lower().ne("nan")
    if not bool(valid.any()):
        return {"fill_stock_contributions": []}
    rows: list[dict[str, Any]] = []
    counts = codes.loc[valid].value_counts(sort=True)
    total = float(counts.sum())
    for code, count in counts.items():
        rows.append(
            {
                "stock_code": str(code),
                "fill_count": int(count),
                "fill_count_share": float(count / total) if total > 0 else 0.0,
            }
        )
    return {"fill_stock_contributions": rows[:20]}


def _align_slot_net_values(
    processed_dfs: dict[str, pd.DataFrame] | None,
    *,
    merged_df: pd.DataFrame | None,
) -> tuple[pd.DatetimeIndex | None, dict[str, pd.Series]]:
    if not processed_dfs:
        return None, {}
    if isinstance(merged_df, pd.DataFrame) and not merged_df.empty and "trade_date" in merged_df.columns:
        days_idx = pd.DatetimeIndex(ensure_trade_date_normalized(merged_df, col="trade_date")["trade_date"])
    else:
        all_dates: list[pd.Timestamp] = []
        for df in processed_dfs.values():
            if not isinstance(df, pd.DataFrame) or df.empty or "trade_date" not in df.columns:
                continue
            all_dates.extend(ensure_trade_date_normalized(df, col="trade_date")["trade_date"].tolist())
        if not all_dates:
            return None, {}
        days_idx = pd.DatetimeIndex(sorted(pd.unique(pd.Index(all_dates))))

    aligned: dict[str, pd.Series] = {}
    for name, df in sorted(processed_dfs.items(), key=lambda kv: str(kv[0]).casefold()):
        if not isinstance(df, pd.DataFrame) or df.empty or "trade_date" not in df.columns or "net_value" not in df.columns:
            continue
        temp = _normalize_dates(df, column="trade_date")
        net_val = pd.to_numeric(temp["net_value"], errors="coerce")
        ser = pd.Series(net_val.to_numpy(dtype="float64"), index=pd.DatetimeIndex(temp["trade_date"]), name=str(name))
        ser = ser[~ser.index.duplicated(keep="last")]
        ser = ser.reindex(days_idx).ffill().fillna(1.0)
        aligned[str(name)] = ser.astype("float64")
    return days_idx, aligned


def _build_slot_concentration(
    processed_dfs: dict[str, pd.DataFrame] | None,
    *,
    merged_df: pd.DataFrame | None,
) -> dict[str, Any]:
    days_idx, aligned = _align_slot_net_values(processed_dfs, merged_df=merged_df)
    if not aligned:
        return {
            "slot_count": 0,
            "top1_slot_profit_share": 0.0,
            "top3_slot_profit_share": 0.0,
            "slot_profit_hhi": 0.0,
            "effective_slot_count": 0.0,
            "drop_top1_cum_return": 0.0,
            "drop_top1_sharpe_ratio": 0.0,
            "slots": [],
        }

    rows: list[dict[str, Any]] = []
    positive_profit = 0.0
    for name, ser in aligned.items():
        final_net = _safe_float(ser.iloc[-1], 1.0)
        profit = final_net - 1.0
        positive = max(profit, 0.0)
        positive_profit += positive
        rows.append(
            {
                "slot": name,
                "final_net_value": float(final_net),
                "profit": float(profit),
                "positive_profit": float(positive),
            }
        )

    rows = sorted(rows, key=lambda item: (float(item["positive_profit"]), float(item["profit"])), reverse=True)
    top1_share = 0.0
    top3_share = 0.0
    hhi = 0.0
    if positive_profit > 0:
        weights = [float(row["positive_profit"]) / positive_profit for row in rows]
        for row, weight in zip(rows, weights):
            row["positive_profit_share"] = float(weight)
        top1_share = float(weights[0]) if weights else 0.0
        top3_share = float(sum(weights[:3]))
        hhi = float(sum(weight * weight for weight in weights))
    else:
        for row in rows:
            row["positive_profit_share"] = 0.0

    drop_metrics: dict[str, float] = {}
    if days_idx is not None and rows:
        drop_slot = str(rows[0]["slot"])
        panel = []
        for name, ser in aligned.items():
            if name == drop_slot:
                panel.append(pd.Series(1.0, index=days_idx, dtype="float64", name=name))
            else:
                panel.append(ser)
        merged = pd.concat(panel, axis=1, sort=True).sort_index()
        counterfactual = pd.DataFrame(
            {
                "trade_date": days_idx,
                "sum_net_value": merged.mean(axis=1, skipna=False).to_numpy(dtype="float64"),
            }
        )
        drop_metrics = _metrics_from_equity(counterfactual)

    return {
        "slot_count": len(rows),
        "top1_slot_profit_share": float(top1_share),
        "top3_slot_profit_share": float(top3_share),
        "slot_profit_hhi": float(hhi),
        "effective_slot_count": float(1.0 / hhi) if hhi > 0 else 0.0,
        "drop_top1_cum_return": float(drop_metrics.get("cum_return", 0.0)),
        "drop_top1_sharpe_ratio": float(drop_metrics.get("sharpe_ratio", 0.0)),
        "slots": rows,
    }


def _build_trade_concentration(trades_df: pd.DataFrame | None) -> dict[str, Any]:
    trade_stats = _build_trade_stats(trades_df)
    if not isinstance(trades_df, pd.DataFrame) or trades_df.empty or "盈亏" not in trades_df.columns:
        return {
            **trade_stats,
            "trade_return_hhi": 0.0,
            "top_decile_trimmed_avg_trade_return": 0.0,
            "top_trade_return_share": 0.0,
            "top3_trade_return_share": 0.0,
            "stock_contributions": [],
        }

    returns = pd.to_numeric(trades_df["盈亏"], errors="coerce").fillna(0.0)
    positive = returns.clip(lower=0.0)
    positive_sum = float(positive.sum())
    trade_hhi = 0.0
    top_trade_share = 0.0
    top3_trade_share = 0.0
    if positive_sum > 0:
        weights = (positive / positive_sum).astype("float64")
        weights_sorted = weights.sort_values(ascending=False)
        trade_hhi = float((weights * weights).sum())
        top_trade_share = float(weights_sorted.iloc[0]) if len(weights_sorted) > 0 else 0.0
        top3_trade_share = float(weights_sorted.iloc[:3].sum()) if len(weights_sorted) > 0 else 0.0

    trim_n = max(1, int(math.ceil(len(returns) * 0.1)))
    trimmed = returns.sort_values(ascending=False).iloc[trim_n:]
    trimmed_mean = float(trimmed.mean()) if len(trimmed) > 0 else 0.0

    stock_rows: list[dict[str, Any]] = []
    if "stock_code" in trades_df.columns:
        code_ser = trades_df["stock_code"].astype(str).str.strip()
        stock_sum = (
            returns.groupby(code_ser, sort=False)
            .sum()
            .sort_values(ascending=False)
        )
        total_positive_stock = float(stock_sum.clip(lower=0.0).sum())
        for code, value in stock_sum.items():
            if not code or str(code).lower() == "nan":
                continue
            positive_share = float(max(float(value), 0.0) / total_positive_stock) if total_positive_stock > 0 else 0.0
            stock_rows.append(
                {
                    "stock_code": str(code),
                    "return_sum": float(value),
                    "positive_return_share": float(positive_share),
                }
            )

    return {
        **trade_stats,
        "trade_return_hhi": float(trade_hhi),
        "top_trade_return_share": float(top_trade_share),
        "top3_trade_return_share": float(top3_trade_share),
        "top_decile_trimmed_avg_trade_return": float(trimmed_mean),
        "stock_contributions": stock_rows[:20],
    }


def _select_anchor_indices(
    count: int,
    *,
    step_days: int,
    window_days: int,
    max_starts: int,
) -> list[int]:
    if count <= 1:
        return [0] if count == 1 else []
    if count <= window_days:
        return [0]
    last_start = count - window_days
    anchors = list(range(0, last_start + 1, max(1, int(step_days))))
    if not anchors:
        anchors = [0]
    if len(anchors) <= max_starts:
        return anchors
    picked = np.linspace(0, len(anchors) - 1, num=max_starts, dtype=int)
    out = sorted({anchors[int(idx)] for idx in picked})
    return out


def _build_multi_start_summary(
    trade_dates: pd.DatetimeIndex,
    *,
    runner: Callable[[str, str], dict[str, float]] | None,
    enabled: bool,
    step_days: int,
    window_days: int,
    max_starts: int,
    robust_lambda: float,
    robust_gamma: float,
) -> dict[str, Any] | None:
    if not enabled or runner is None or len(trade_dates) < 2:
        return None
    anchors = _select_anchor_indices(
        len(trade_dates),
        step_days=step_days,
        window_days=window_days,
        max_starts=max_starts,
    )
    if not anchors:
        return None

    rows: list[dict[str, Any]] = []
    for start_idx in anchors:
        end_idx = min(len(trade_dates) - 1, start_idx + max(2, int(window_days)) - 1)
        start_ts = pd.Timestamp(trade_dates[start_idx]).normalize()
        end_ts = pd.Timestamp(trade_dates[end_idx]).normalize()
        try:
            metrics = runner(start_ts.strftime("%Y%m%d"), end_ts.strftime("%Y%m%d"))
        except Exception as exc:
            rows.append(
                {
                    "start_date": start_ts.strftime("%Y-%m-%d"),
                    "end_date": end_ts.strftime("%Y-%m-%d"),
                    "status": "error",
                    "error": str(exc),
                }
            )
            continue
        row = {
            "start_date": start_ts.strftime("%Y-%m-%d"),
            "end_date": end_ts.strftime("%Y-%m-%d"),
            "status": "ok",
        }
        for key in ("cum_return", "annualized_return", "max_drawdown", "sharpe_ratio", "num_days"):
            row[key] = float(metrics.get(key, 0.0))
        rows.append(row)

    ok_rows = [row for row in rows if row.get("status") == "ok"]
    if not ok_rows:
        return {
            "enabled": True,
            "window_count": 0,
            "step_days": int(step_days),
            "window_days": int(window_days),
            "rows": rows,
        }

    ok_df = pd.DataFrame(ok_rows).sort_values("start_date", kind="mergesort").reset_index(drop=True)
    split_idx = max(1, int(math.floor(len(ok_df) * 0.7)))
    train_df = ok_df.iloc[:split_idx]
    oos_df = ok_df.iloc[split_idx:] if split_idx < len(ok_df) else pd.DataFrame(columns=ok_df.columns)

    mean_train = float(pd.to_numeric(train_df["annualized_return"], errors="coerce").fillna(0.0).mean())
    std_train = float(pd.to_numeric(train_df["annualized_return"], errors="coerce").fillna(0.0).std(ddof=0))
    mean_oos = (
        float(pd.to_numeric(oos_df["annualized_return"], errors="coerce").fillna(0.0).mean())
        if not oos_df.empty
        else mean_train
    )
    oos_gap = max(0.0, mean_train - mean_oos)
    robust_score = mean_train - float(robust_lambda) * std_train - float(robust_gamma) * oos_gap
    worst_cum_return = float(pd.to_numeric(ok_df["cum_return"], errors="coerce").fillna(0.0).min())
    worst_sharpe = float(pd.to_numeric(ok_df["sharpe_ratio"], errors="coerce").fillna(0.0).min())

    return {
        "enabled": True,
        "window_count": int(len(ok_df)),
        "step_days": int(step_days),
        "window_days": int(window_days),
        "mean_train_annualized_return": float(mean_train),
        "std_train_annualized_return": float(std_train),
        "mean_oos_annualized_return": float(mean_oos),
        "oos_gap": float(oos_gap),
        "robust_score": float(robust_score),
        "worst_cum_return": float(worst_cum_return),
        "worst_sharpe_ratio": float(worst_sharpe),
        "rows": rows,
    }


def _build_issues(report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    issues: list[dict[str, Any]] = []
    feedback: list[str] = []

    trade_diag = report.get("trade_diagnostics") or {}
    trade_count = _safe_float(trade_diag.get("trade_count"), 0.0)
    avg_trade_return = _safe_float(trade_diag.get("avg_trade_return"), 0.0)
    stock_equal_avg = _safe_float(trade_diag.get("stock_equal_avg_trade_return"), 0.0)
    if trade_count < 5:
        issues.append({"issue_type": "too_few_trades", "severity": 0.9, "evidence": {"trade_count": trade_count}})
        feedback.append(f"交易笔数过少: trade_count={trade_count:.0f}，需提高信号覆盖或降低过滤强度。")
    if trade_count > 0 and avg_trade_return <= 0:
        issues.append(
            {
                "issue_type": "non_positive_avg_trade_return",
                "severity": 0.8,
                "evidence": {"avg_trade_return": avg_trade_return},
            }
        )
        feedback.append(f"平均每笔收益非正: avg_trade_return={avg_trade_return:.4f}，需改善入场/出场质量。")
    if trade_count > 0 and stock_equal_avg <= 0:
        issues.append(
            {
                "issue_type": "stock_level_contribution_weak",
                "severity": 0.6,
                "evidence": {"stock_equal_avg_trade_return": stock_equal_avg},
            }
        )
        feedback.append(
            f"按股票等权平均单笔收益非正: stock_equal_avg_trade_return={stock_equal_avg:.4f}，可能依赖少数个股。"
        )

    conc = report.get("concentration") or {}
    top1_slot = _safe_float(conc.get("top1_slot_profit_share"), 0.0)
    trade_hhi = _safe_float(conc.get("trade_return_hhi"), 0.0)
    if top1_slot >= 0.8:
        issues.append(
            {
                "issue_type": "slot_profit_concentration_high",
                "severity": 0.9,
                "evidence": {"top1_slot_profit_share": top1_slot},
            }
        )
        feedback.append(f"收益过度集中于单一槽位: top1_slot_profit_share={top1_slot:.2%}。")
    if trade_hhi >= 0.2:
        issues.append(
            {
                "issue_type": "trade_profit_concentration_high",
                "severity": 0.7,
                "evidence": {"trade_return_hhi": trade_hhi},
            }
        )
        feedback.append(f"交易收益集中度偏高: trade_return_hhi={trade_hhi:.4f}。")

    multi = report.get("multi_start") or {}
    if multi:
        worst_cum_return = _safe_float(multi.get("worst_cum_return"), 0.0)
        robust_score = _safe_float(multi.get("robust_score"), 0.0)
        oos_gap = _safe_float(multi.get("oos_gap"), 0.0)
        if worst_cum_return < 0:
            issues.append(
                {
                    "issue_type": "worst_start_negative",
                    "severity": 0.8,
                    "evidence": {"worst_cum_return": worst_cum_return},
                }
            )
            feedback.append(f"多起始点最差窗口为负收益: worst_cum_return={worst_cum_return:.2%}。")
        if robust_score <= 0:
            issues.append(
                {
                    "issue_type": "multi_start_unstable",
                    "severity": 0.8,
                    "evidence": {"robust_score": robust_score, "oos_gap": oos_gap},
                }
            )
            feedback.append(
                f"多起始点稳健性较差: robust_score={robust_score:.4f}, oos_gap={oos_gap:.4f}。"
            )

    capacity = report.get("capacity") or {}
    if capacity:
        p95_participation = _safe_float(capacity.get("p95_participation_rate"), 0.0)
        unknown_liquidity = _safe_float(capacity.get("unknown_liquidity_fill_count"), 0.0)
        if p95_participation > 0.05:
            issues.append(
                {
                    "issue_type": "capacity_participation_high",
                    "severity": 0.8,
                    "evidence": {"p95_participation_rate": p95_participation},
                }
            )
            feedback.append(f"成交参与率偏高: p95_participation_rate={p95_participation:.2%}，需做容量/冲击成本复核。")
        if unknown_liquidity > 0:
            issues.append(
                {
                    "issue_type": "capacity_liquidity_unknown",
                    "severity": 0.5,
                    "evidence": {"unknown_liquidity_fill_count": unknown_liquidity},
                }
            )
            feedback.append(f"存在无法匹配成交量的成交记录: unknown_liquidity_fill_count={unknown_liquidity:.0f}。")

    return issues, feedback


def _extract_trade_dates(merged_df: pd.DataFrame | None) -> pd.DatetimeIndex | None:
    if not isinstance(merged_df, pd.DataFrame) or merged_df.empty or "trade_date" not in merged_df.columns:
        return None
    return pd.DatetimeIndex(ensure_trade_date_normalized(merged_df, col="trade_date")["trade_date"])


def _build_report_core(
    *,
    merged_df: pd.DataFrame,
    execution: ExecutionArtifacts | None,
    decision_artifacts: SignalDecisionArtifacts | None,
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    merged_df = _normalize_dates(merged_df, column="trade_date") if isinstance(merged_df, pd.DataFrame) else merged_df
    trades_df = None
    fills_df = None
    daily_state_df = None
    processed_dfs = None
    trade_summary_df = None
    risk_summary_df = None
    risk_events_df = None
    cost_summary_df = None
    if isinstance(execution, VectorizedExecutionArtifacts):
        trades_df = execution.trade_segments_df
        processed_dfs = execution.processed_frames
        trade_summary_df = execution.trade_segments_summary_df
        risk_summary_df = execution.risk_control_summary_df
        risk_events_df = execution.risk_control_events_df
        cost_summary_df = execution.cost_summary_df
    elif isinstance(execution, LedgerExecutionArtifacts):
        trades_df = execution.trade_segments_df
        processed_dfs = execution.processed_frames
        trade_summary_df = execution.trade_segments_summary_df
        risk_summary_df = execution.risk_control_summary_df
        risk_events_df = execution.risk_control_events_df
        cost_summary_df = execution.cost_summary_df
        daily_state_df = execution.daily_state_df
        fill_frames = [frame for frame in (execution.fills_df, execution.risk_fills_df) if not frame.empty]
        if fill_frames:
            fills_df = pd.concat(fill_frames, ignore_index=True) if len(fill_frames) > 1 else fill_frames[0]
    signal_artifacts = (
        decision_artifacts
        if isinstance(decision_artifacts, SignalDecisionArtifacts)
        else SignalDecisionArtifacts()
    )
    topn_summary_df = (
        signal_artifacts.topn_segments.topn_trade_segments_summary_df
        if signal_artifacts.topn_segments is not None
        else None
    )
    return {
        "window": {
            "start_time": str(start_time),
            "end_time": str(end_time),
            "num_days": int(len(merged_df)) if isinstance(merged_df, pd.DataFrame) else 0,
        },
        "metrics": _metrics_from_equity(merged_df),
        "trade_diagnostics": _build_trade_diagnostics(trades_df, fills_df),
        "concentration": {
            **_build_slot_concentration(processed_dfs, merged_df=merged_df),
            **_build_trade_concentration(trades_df),
            **_build_fill_concentration(fills_df),
        },
        "actual_trade_segments_summary": _summary_row(trade_summary_df),
        "topn_trade_segments_summary": _summary_row(topn_summary_df),
        "signal_daily_summary_head": _head_records(signal_artifacts.daily_summary_df),
        "risk_control_summary_head": _head_records(risk_summary_df),
        "risk_control_events_head": _head_records(risk_events_df),
        "cost_summary_head": _head_records(cost_summary_df),
        "capacity": build_capacity_diagnostics(
            fills_df=fills_df,
            processed_dfs=processed_dfs,
        ),
        "trade_segments_head": _head_records(trades_df),
        "portfolio_fills_head": _head_records(fills_df),
        "portfolio_daily_state_head": _head_records(daily_state_df),
        "multi_start": None,
    }


def build_backtest_single_run_report(
    *,
    merged_df: pd.DataFrame,
    execution: ExecutionArtifacts | None,
    decision_artifacts: SignalDecisionArtifacts | None,
    start_time: str,
    end_time: str,
) -> dict[str, Any]:
    report = _build_report_core(
        merged_df=merged_df,
        execution=execution,
        decision_artifacts=decision_artifacts,
        start_time=start_time,
        end_time=end_time,
    )
    issues, feedback = _build_issues(report)
    report["issues"] = issues
    report["feedback_lines"] = feedback
    return report


def build_backtest_multi_start_summary(
    *,
    merged_df: pd.DataFrame,
    multi_start_runner: Callable[[str, str], dict[str, float]] | None = None,
    multi_start_enabled: bool = False,
    multi_start_step_days: int = 20,
    multi_start_window_days: int = 250,
    multi_start_max_starts: int = 8,
    robust_lambda: float = 0.5,
    robust_gamma: float = 1.0,
) -> dict[str, Any] | None:
    trade_dates = _extract_trade_dates(
        _normalize_dates(merged_df, column="trade_date") if isinstance(merged_df, pd.DataFrame) else merged_df
    )
    if trade_dates is None:
        return None
    return _build_multi_start_summary(
        trade_dates,
        runner=multi_start_runner,
        enabled=bool(multi_start_enabled),
        step_days=int(max(1, multi_start_step_days)),
        window_days=int(max(2, multi_start_window_days)),
        max_starts=int(max(1, multi_start_max_starts)),
        robust_lambda=float(robust_lambda),
        robust_gamma=float(robust_gamma),
    )


def attach_multi_start_to_diagnostic_report(
    report: dict[str, Any],
    *,
    multi_start_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    out = dict(report)
    out["multi_start"] = multi_start_summary
    issues, feedback = _build_issues(out)
    out["issues"] = issues
    out["feedback_lines"] = feedback
    return out


def build_backtest_diagnostic_report(
    *,
    merged_df: pd.DataFrame,
    execution: ExecutionArtifacts | None,
    decision_artifacts: SignalDecisionArtifacts | None,
    start_time: str,
    end_time: str,
    multi_start_runner: Callable[[str, str], dict[str, float]] | None = None,
    multi_start_enabled: bool = False,
    multi_start_step_days: int = 20,
    multi_start_window_days: int = 250,
    multi_start_max_starts: int = 8,
    robust_lambda: float = 0.5,
    robust_gamma: float = 1.0,
) -> dict[str, Any]:
    report = build_backtest_single_run_report(
        merged_df=merged_df,
        execution=execution,
        decision_artifacts=decision_artifacts,
        start_time=start_time,
        end_time=end_time,
    )
    multi_start_summary = build_backtest_multi_start_summary(
        merged_df=merged_df,
        multi_start_runner=multi_start_runner,
        multi_start_enabled=multi_start_enabled,
        multi_start_step_days=multi_start_step_days,
        multi_start_window_days=multi_start_window_days,
        multi_start_max_starts=multi_start_max_starts,
        robust_lambda=robust_lambda,
        robust_gamma=robust_gamma,
    )
    if multi_start_summary is None:
        return report
    return attach_multi_start_to_diagnostic_report(
        report,
        multi_start_summary=multi_start_summary,
    )


__all__ = [
    "attach_multi_start_to_diagnostic_report",
    "build_backtest_diagnostic_report",
    "build_backtest_multi_start_summary",
    "build_backtest_single_run_report",
]
