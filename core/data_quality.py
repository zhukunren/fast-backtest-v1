"""Lightweight data quality checks for price DataFrames."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from core.constants import COL_STOCK_CODE, COL_TRADE_DATE
from core.panel_identity import PANEL_MANIFEST_SCHEMA_VERSION


DATA_QUALITY_REPORT_SCHEMA_VERSION = 1
PRICE_REQUIRED_COLUMNS = (COL_TRADE_DATE, COL_STOCK_CODE, "open", "high", "low", "close")
PANEL_MANIFEST_REQUIRED_KEYS = (
    "format",
    "schema_version",
    "created_at_utc",
    "source",
    "source_config",
    "output",
    "rows",
    "stocks",
    "min_trade_date",
    "max_trade_date",
    "duplicate_keys",
    "columns",
)


@dataclass(slots=True)
class DataQualityIssue:
    severity: str
    code: str
    message: str
    count: int = 0
    sample: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "count": int(self.count),
            "sample": self.sample,
        }


@dataclass(slots=True)
class DataQualityReport:
    dataset_type: str
    source: str
    row_count: int
    column_count: int
    issues: list[DataQualityIssue] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": "data_quality_report",
            "schema_version": DATA_QUALITY_REPORT_SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "dataset_type": self.dataset_type,
            "source": self.source,
            "ok": self.ok,
            "row_count": int(self.row_count),
            "column_count": int(self.column_count),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _safe_sample(df: pd.DataFrame, columns: Iterable[str], *, limit: int = 5) -> list[dict[str, Any]]:
    safe_columns = [col for col in columns if col in df.columns]
    if not safe_columns:
        safe_columns = list(df.columns[: min(len(df.columns), 6)])
    payload = df.loc[:, safe_columns].head(limit).copy()
    for col in payload.columns:
        if pd.api.types.is_datetime64_any_dtype(payload[col]):
            payload[col] = payload[col].dt.strftime("%Y-%m-%d")
    return payload.astype(object).where(pd.notna(payload), None).to_dict(orient="records")


def _add_issue(
    issues: list[DataQualityIssue],
    *,
    severity: str,
    code: str,
    message: str,
    count: int = 0,
    sample: list[dict[str, Any]] | None = None,
) -> None:
    issues.append(
        DataQualityIssue(
            severity=str(severity),
            code=str(code),
            message=str(message),
            count=int(count or 0),
            sample=list(sample or []),
        )
    )


def check_price_frame_quality(
    df: pd.DataFrame,
    *,
    source: str = "",
    stock_code: str | None = None,
    max_abs_daily_return: float = 0.5,
) -> DataQualityReport:
    issues: list[DataQualityIssue] = []
    report = DataQualityReport(
        dataset_type="price_frame",
        source=str(source or stock_code or "unknown"),
        row_count=int(len(df)) if isinstance(df, pd.DataFrame) else 0,
        column_count=int(len(df.columns)) if isinstance(df, pd.DataFrame) else 0,
        issues=issues,
    )
    if not isinstance(df, pd.DataFrame) or df.empty:
        _add_issue(issues, severity="error", code="empty_frame", message="price frame is empty")
        return report

    missing = [col for col in PRICE_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        _add_issue(
            issues,
            severity="error",
            code="missing_required_columns",
            message=f"missing required columns: {missing}",
            count=len(missing),
        )
        return report

    work = df.copy()
    trade_dates = pd.to_datetime(work[COL_TRADE_DATE], errors="coerce")
    bad_dates = trade_dates.isna()
    if bad_dates.any():
        bad_rows = work.loc[bad_dates]
        _add_issue(
            issues,
            severity="error",
            code="invalid_trade_date",
            message="trade_date contains invalid values",
            count=int(bad_dates.sum()),
            sample=_safe_sample(bad_rows, [COL_TRADE_DATE, COL_STOCK_CODE]),
        )
    if trade_dates.notna().any() and not trade_dates.dropna().is_monotonic_increasing:
        _add_issue(
            issues,
            severity="error",
            code="trade_date_not_sorted",
            message="trade_date must be sorted before shift or rolling operations",
            count=int(len(work)),
            sample=_safe_sample(work, [COL_TRADE_DATE, COL_STOCK_CODE]),
        )

    duplicate_mask = trade_dates.duplicated(keep=False)
    if duplicate_mask.any():
        duplicate_rows = work.loc[duplicate_mask]
        _add_issue(
            issues,
            severity="error",
            code="duplicate_trade_date",
            message="trade_date contains duplicate rows",
            count=int(duplicate_mask.sum()),
            sample=_safe_sample(duplicate_rows, [COL_TRADE_DATE, COL_STOCK_CODE]),
        )

    numeric_cols = ["open", "high", "low", "close"]
    for col in numeric_cols:
        numeric = pd.to_numeric(work[col], errors="coerce")
        missing_mask = numeric.isna()
        if missing_mask.any():
            _add_issue(
                issues,
                severity="error",
                code=f"{col}_missing_or_invalid",
                message=f"{col} contains missing or non-numeric values",
                count=int(missing_mask.sum()),
                sample=_safe_sample(work.loc[missing_mask], [COL_TRADE_DATE, COL_STOCK_CODE, col]),
            )
        non_positive = numeric <= 0
        if non_positive.any():
            _add_issue(
                issues,
                severity="error",
                code=f"{col}_non_positive",
                message=f"{col} contains non-positive values",
                count=int(non_positive.sum()),
                sample=_safe_sample(work.loc[non_positive], [COL_TRADE_DATE, COL_STOCK_CODE, col]),
            )
        work[col] = numeric

    ohlc_bad = (
        (work["high"] < work[["open", "close", "low"]].max(axis=1))
        | (work["low"] > work[["open", "close", "high"]].min(axis=1))
    )
    if ohlc_bad.any():
        _add_issue(
            issues,
            severity="error",
            code="ohlc_inconsistent",
            message="OHLC relationship is inconsistent",
            count=int(ohlc_bad.sum()),
            sample=_safe_sample(work.loc[ohlc_bad], [COL_TRADE_DATE, COL_STOCK_CODE, "open", "high", "low", "close"]),
        )

    if "volume" in work.columns:
        volume = pd.to_numeric(work["volume"], errors="coerce")
        bad_volume = volume.isna() | (volume < 0)
        if bad_volume.any():
            _add_issue(
                issues,
                severity="warning",
                code="volume_missing_or_negative",
                message="volume contains missing or negative values",
                count=int(bad_volume.sum()),
                sample=_safe_sample(work.loc[bad_volume], [COL_TRADE_DATE, COL_STOCK_CODE, "volume"]),
            )

    returns = work["close"].pct_change()
    extreme = returns.abs() > float(max_abs_daily_return)
    if extreme.any():
        _add_issue(
            issues,
            severity="warning",
            code="extreme_close_return",
            message=f"absolute close-to-close return exceeds {float(max_abs_daily_return):.2%}",
            count=int(extreme.sum()),
            sample=_safe_sample(work.loc[extreme], [COL_TRADE_DATE, COL_STOCK_CODE, "close"]),
        )
    return report


def _parse_date_series(values: pd.Series) -> pd.Series:
    text = values.astype(str).str.strip()
    numeric_like = text.str.fullmatch(r"\d{8}", na=False)
    parsed = pd.to_datetime(text.where(~numeric_like), errors="coerce")
    if bool(numeric_like.any()):
        parsed_numeric = pd.to_datetime(text.where(numeric_like), format="%Y%m%d", errors="coerce")
        parsed = parsed.where(~numeric_like, parsed_numeric)
    return parsed


def check_point_in_time_frame(
    df: pd.DataFrame,
    *,
    source: str = "",
    trade_date_col: str = COL_TRADE_DATE,
    available_at_col: str = "available_at",
) -> DataQualityReport:
    issues: list[DataQualityIssue] = []
    report = DataQualityReport(
        dataset_type="point_in_time_frame",
        source=str(source or "unknown"),
        row_count=int(len(df)) if isinstance(df, pd.DataFrame) else 0,
        column_count=int(len(df.columns)) if isinstance(df, pd.DataFrame) else 0,
        issues=issues,
    )
    if not isinstance(df, pd.DataFrame) or df.empty:
        _add_issue(issues, severity="error", code="empty_frame", message="point-in-time frame is empty")
        return report

    missing = [col for col in (trade_date_col, available_at_col) if col not in df.columns]
    if missing:
        _add_issue(
            issues,
            severity="error",
            code="missing_point_in_time_columns",
            message=f"missing point-in-time columns: {missing}",
            count=len(missing),
        )
        return report

    work = df.copy()
    trade_dates = _parse_date_series(work[trade_date_col])
    available_at = _parse_date_series(work[available_at_col])
    bad_trade_date = trade_dates.isna()
    bad_available_at = available_at.isna()
    if bad_trade_date.any():
        _add_issue(
            issues,
            severity="error",
            code="invalid_trade_date",
            message=f"{trade_date_col} contains invalid values",
            count=int(bad_trade_date.sum()),
            sample=_safe_sample(work.loc[bad_trade_date], [trade_date_col, available_at_col, COL_STOCK_CODE]),
        )
    if bad_available_at.any():
        _add_issue(
            issues,
            severity="error",
            code="invalid_available_at",
            message=f"{available_at_col} contains invalid values",
            count=int(bad_available_at.sum()),
            sample=_safe_sample(work.loc[bad_available_at], [trade_date_col, available_at_col, COL_STOCK_CODE]),
        )

    future_mask = available_at.notna() & trade_dates.notna() & available_at.gt(trade_dates)
    if future_mask.any():
        _add_issue(
            issues,
            severity="error",
            code="future_available_at",
            message=f"{available_at_col} must be <= {trade_date_col}",
            count=int(future_mask.sum()),
            sample=_safe_sample(work.loc[future_mask], [trade_date_col, available_at_col, COL_STOCK_CODE]),
        )
    return report


def audit_panel_manifest_payload(
    payload: dict[str, Any],
    *,
    source: str = "",
    required_columns: Iterable[str] = PRICE_REQUIRED_COLUMNS,
    min_rows: int = 1,
    min_stocks: int = 1,
) -> DataQualityReport:
    issues: list[DataQualityIssue] = []
    output_hint = payload.get("output") if isinstance(payload, dict) else ""
    report = DataQualityReport(
        dataset_type="panel_manifest",
        source=str(source or output_hint or "unknown"),
        row_count=int(payload.get("rows") or 0) if isinstance(payload, dict) else 0,
        column_count=len(payload.keys()) if isinstance(payload, dict) else 0,
        issues=issues,
    )
    if not isinstance(payload, dict) or not payload:
        _add_issue(issues, severity="error", code="empty_manifest", message="panel manifest is empty")
        return report

    missing_keys = [key for key in PANEL_MANIFEST_REQUIRED_KEYS if key not in payload]
    if missing_keys:
        _add_issue(
            issues,
            severity="error",
            code="missing_manifest_keys",
            message=f"panel manifest missing keys: {missing_keys}",
            count=len(missing_keys),
        )

    if payload.get("schema_version") != PANEL_MANIFEST_SCHEMA_VERSION:
        _add_issue(
            issues,
            severity="error",
            code="manifest_schema_unsupported",
            message=(
                "panel manifest schema is unsupported: "
                f"expected={PANEL_MANIFEST_SCHEMA_VERSION} actual={payload.get('schema_version')!r}"
            ),
        )
    source_config = payload.get("source_config")
    if not isinstance(source_config, dict) or not source_config:
        _add_issue(
            issues,
            severity="error",
            code="manifest_source_config_invalid",
            message="panel manifest source_config must be a non-empty object",
        )

    rows = int(payload.get("rows") or 0)
    stocks = int(payload.get("stocks") or 0)
    if rows < int(min_rows):
        _add_issue(
            issues,
            severity="error",
            code="manifest_rows_below_minimum",
            message=f"panel manifest rows below minimum: rows={rows} min_rows={min_rows}",
            count=rows,
        )
    if stocks < int(min_stocks):
        _add_issue(
            issues,
            severity="error",
            code="manifest_stocks_below_minimum",
            message=f"panel manifest stocks below minimum: stocks={stocks} min_stocks={min_stocks}",
            count=stocks,
        )

    duplicate_keys = int(payload.get("duplicate_keys") or 0)
    if duplicate_keys:
        _add_issue(
            issues,
            severity="error",
            code="manifest_duplicate_keys",
            message=f"panel manifest reports duplicate stock_code+trade_date keys: {duplicate_keys}",
            count=duplicate_keys,
        )

    columns = {str(col).strip().casefold() for col in payload.get("columns") or []}
    missing_columns = [col for col in required_columns if str(col).strip().casefold() not in columns]
    if missing_columns:
        _add_issue(
            issues,
            severity="error",
            code="manifest_missing_columns",
            message=f"panel manifest missing required columns: {missing_columns}",
            count=len(missing_columns),
        )

    min_date = pd.to_datetime(payload.get("min_trade_date"), errors="coerce")
    max_date = pd.to_datetime(payload.get("max_trade_date"), errors="coerce")
    if pd.isna(min_date) or pd.isna(max_date):
        _add_issue(
            issues,
            severity="error",
            code="manifest_invalid_date_range",
            message="panel manifest min_trade_date/max_trade_date must be valid dates",
        )
    elif min_date > max_date:
        _add_issue(
            issues,
            severity="error",
            code="manifest_reversed_date_range",
            message="panel manifest min_trade_date is after max_trade_date",
        )

    file_sha256 = str(payload.get("file_sha256") or "").strip()
    if not file_sha256:
        _add_issue(
            issues,
            severity="warning",
            code="manifest_missing_file_sha256",
            message="panel manifest does not include file_sha256",
        )
    source_config_hash = str(payload.get("source_config_hash") or "").strip()
    if not source_config_hash:
        _add_issue(
            issues,
            severity="warning",
            code="manifest_missing_source_config_hash",
            message="panel manifest does not include source_config_hash",
        )
    symbol_coverage = payload.get("symbol_coverage")
    if not isinstance(symbol_coverage, list) or not symbol_coverage:
        _add_issue(
            issues,
            severity="warning",
            code="manifest_missing_symbol_coverage",
            message="panel manifest does not include per-symbol coverage",
        )
    return report


def read_panel_manifest(path: Path | str) -> dict[str, Any]:
    manifest_path = Path(path)
    if manifest_path.suffix.lower() == ".parquet":
        manifest_path = manifest_path.with_suffix(".manifest.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"panel manifest must be a JSON object: {manifest_path}")
    return payload


def write_data_quality_report(path: Path | str, report: DataQualityReport) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return out_path


__all__ = [
    "DATA_QUALITY_REPORT_SCHEMA_VERSION",
    "PANEL_MANIFEST_REQUIRED_KEYS",
    "PRICE_REQUIRED_COLUMNS",
    "DataQualityIssue",
    "DataQualityReport",
    "audit_panel_manifest_payload",
    "check_price_frame_quality",
    "check_point_in_time_frame",
    "read_panel_manifest",
    "write_data_quality_report",
]
