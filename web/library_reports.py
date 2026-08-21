"""Human-readable exports for the Web strategy library."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from web.insights.health import get_library_health
from web.insights.metrics import compute_derived_metrics
from web.insights.strategy_analysis import compute_library_strategy_analysis
from web.library_analysis import _coerce_finite_float
from web.library_catalog import get_library_catalog, get_library_detail


def build_library_export_payload() -> dict[str, Any]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "catalog": get_library_catalog(),
        "health": get_library_health(),
    }


def build_library_catalog_csv() -> str:
    columns = [
        "name",
        "title",
        "status",
        "status_label",
        "family",
        "module",
        "strategy_version",
        "version_status",
        "source_hash",
        "config_hash",
        "run_source_hash",
        "run_config_hash",
        "run_id",
        "start_time",
        "end_time",
        "cum_return",
        "max_drawdown",
        "win_rate",
        "trade_count",
        "rank",
        "cache_status",
        "cache_reason",
        "timestamp",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for item in get_library_catalog().get("items", []):
        if isinstance(item, dict):
            writer.writerow({key: item.get(key) for key in columns})
    return "\ufeff" + output.getvalue()


def _report_pct(value: Any, *, drawdown: bool = False) -> str:
    num = _coerce_finite_float(value)
    if num is None:
        return "-"
    sign = "-" if drawdown else ""
    return f"{sign}{abs(num) * 100:.2f}%" if drawdown else f"{num * 100:.2f}%"


def _report_number(value: Any, digits: int = 4) -> str:
    num = _coerce_finite_float(value)
    return "-" if num is None else f"{num:.{digits}f}"


def _find_catalog_entry(name: str) -> dict[str, Any] | None:
    catalog = get_library_catalog()
    for item in catalog.get("items", []):
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def build_strategy_markdown_report(name: str) -> str | None:
    entry = _find_catalog_entry(name)
    detail = get_library_detail(name)
    if entry is None and detail is None:
        return None

    entry = entry or {}
    result = detail.get("result", {}) if isinstance(detail, dict) else {}
    metrics = compute_derived_metrics(name) or {}
    analysis = compute_library_strategy_analysis(name)

    title = str(entry.get("title") or name)
    lines = [
        f"# {title}",
        "",
        f"- 策略名称：`{name}`",
        f"- 状态：{entry.get('status_label') or entry.get('status') or '-'}",
        f"- 模块：`{entry.get('module') or '-'}`",
        f"- 回测区间：{entry.get('start_time') or '-'} 至 {entry.get('end_time') or '-'}",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        "",
        "## 绩效摘要",
        "",
        "| 指标 | 数值 |",
        "| --- | ---: |",
        f"| 累计收益 | {_report_pct(entry.get('cum_return') if entry.get('cum_return') is not None else result.get('cum_return'))} |",
        f"| 年化收益 | {_report_pct(metrics.get('ann_return'))} |",
        f"| 最大回撤 | {_report_pct(entry.get('max_drawdown') if entry.get('max_drawdown') is not None else result.get('max_drawdown'), drawdown=True)} |",
        f"| 夏普比率 | {_report_number(metrics.get('sharpe'))} |",
        f"| Calmar | {_report_number(metrics.get('calmar'))} |",
        f"| 胜率 | {_report_pct(entry.get('win_rate') if entry.get('win_rate') is not None else result.get('actual_segment_win_rate'))} |",
        f"| 交易笔数 | {entry.get('trade_count') if entry.get('trade_count') is not None else result.get('actual_segment_trade_count', '-')} |",
        "",
    ]

    if analysis:
        score = analysis.get("score", {})
        quality = analysis.get("sample_quality", {})
        return_profile = analysis.get("return_profile", {})
        diagnostics = analysis.get("diagnostics", [])
        lines.extend([
            "## 研究诊断",
            "",
            f"- 研究评分：{_report_number(score.get('value'), 1)} / 100（{score.get('grade') or '-'}）",
            f"- 样本区间：{quality.get('start_date') or '-'} 至 {quality.get('end_date') or '-'}，共 {quality.get('date_count') or 0} 个净值点",
            f"- 每日持仓覆盖率：{_report_pct(quality.get('holding_coverage'))}",
            f"- 正收益交易日占比：{_report_pct(return_profile.get('positive_day_ratio'))}",
            "",
        ])
        if diagnostics:
            lines.extend(["### 诊断标签", ""])
            for item in diagnostics:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('label') or item.get('code')}: {item.get('detail') or ''}")
            lines.append("")
        yearly = analysis.get("period_stats", {}).get("yearly", []) if isinstance(analysis.get("period_stats"), dict) else []
        if yearly:
            lines.extend([
                "### 年度表现",
                "",
                "| 年度 | 收益 | 最大回撤 | 交易日 |",
                "| --- | ---: | ---: | ---: |",
            ])
            for item in yearly:
                if isinstance(item, dict):
                    lines.append(
                        f"| {item.get('period') or '-'} | "
                        f"{_report_pct(item.get('return'))} | "
                        f"{_report_pct(item.get('max_drawdown'), drawdown=True)} | "
                        f"{item.get('day_count') or 0} |"
                    )
            lines.append("")
    else:
        lines.extend([
            "## 研究诊断",
            "",
            "- 暂无有效净值缓存，无法生成研究诊断。",
            "",
        ])

    lines.extend([
        "## 缓存治理",
        "",
        f"- 缓存状态：{entry.get('cache_status') or '-'}",
        f"- 缓存原因：{entry.get('cache_reason') or '-'}",
        f"- 缓存区间：{entry.get('cache_actual_start_time') or '-'} 至 {entry.get('cache_actual_end_time') or '-'}",
    ])
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "build_library_catalog_csv",
    "build_library_export_payload",
    "build_strategy_markdown_report",
]
