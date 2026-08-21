"""Central policy for incomplete required OHLCV rows."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import pandas as pd

from core.exceptions import DataValidationError
from core.logging import log_warn


DEFAULT_INCOMPLETE_OHLCV_POLICY = "drop_warn"
INCOMPLETE_OHLCV_POLICIES = frozenset({"drop_warn", "reject"})
INCOMPLETE_OHLCV_DIAGNOSTIC_ATTR = "incomplete_ohlcv_diagnostic"
REQUIRED_OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")
DEFAULT_ERROR_SAMPLE_LIMIT = 5


def normalize_incomplete_ohlcv_policy(value: object) -> str:
    policy = str(value or DEFAULT_INCOMPLETE_OHLCV_POLICY).strip().casefold()
    if policy not in INCOMPLETE_OHLCV_POLICIES:
        allowed = sorted(INCOMPLETE_OHLCV_POLICIES, key=str.casefold)
        raise ValueError(f"incomplete OHLCV policy must be one of {allowed}, got {value!r}")
    return policy


def configured_incomplete_ohlcv_policy(cfg: object | None) -> str:
    panel = getattr(cfg, "panel", None)
    value = getattr(panel, "incomplete_ohlcv_policy", DEFAULT_INCOMPLETE_OHLCV_POLICY)
    return normalize_incomplete_ohlcv_policy(value)


def _sample_sort_key(sample: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        str(sample.get("stock_code") or "").casefold(),
        str(sample.get("trade_date") or ""),
        ",".join(str(value) for value in sample.get("invalid_columns", ())),
    )


def _render_sample_value(value: object) -> object:
    if value is None or bool(pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):
            pass
    return value if isinstance(value, (str, int, float, bool)) else str(value)


@dataclass(frozen=True, slots=True)
class IncompleteOhlcvDiagnostic:
    policy: str
    source: str
    outcome: str
    input_rows: int
    kept_rows: int
    dropped_rows: int
    required_columns: tuple[str, ...]
    error_samples: tuple[Mapping[str, object], ...] = ()

    @property
    def all_rows_invalid(self) -> bool:
        return self.input_rows > 0 and self.kept_rows == 0 and self.dropped_rows == self.input_rows

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "policy": self.policy,
            "source": self.source,
            "outcome": self.outcome,
            "input_rows": self.input_rows,
            "kept_rows": self.kept_rows,
            "dropped_rows": self.dropped_rows,
            "required_columns": list(self.required_columns),
            "all_rows_invalid": self.all_rows_invalid,
            "error_samples": [dict(sample) for sample in self.error_samples],
        }

    @classmethod
    def from_value(cls, value: IncompleteOhlcvDiagnostic | Mapping[str, object]) -> IncompleteOhlcvDiagnostic:
        if isinstance(value, cls):
            return value
        samples = value.get("error_samples") or ()
        return cls(
            policy=normalize_incomplete_ohlcv_policy(value.get("policy")),
            source=str(value.get("source") or "unknown"),
            outcome=str(value.get("outcome") or "clean"),
            input_rows=int(value.get("input_rows") or 0),
            kept_rows=int(value.get("kept_rows") or 0),
            dropped_rows=int(value.get("dropped_rows") or 0),
            required_columns=tuple(str(name) for name in value.get("required_columns") or ()),
            error_samples=tuple(dict(sample) for sample in samples if isinstance(sample, Mapping)),
        )


class IncompleteOhlcvDataError(DataValidationError):
    def __init__(self, diagnostic: IncompleteOhlcvDiagnostic) -> None:
        self.diagnostic = diagnostic
        label = "all required OHLCV rows are incomplete" if diagnostic.all_rows_invalid else "incomplete OHLCV rows rejected"
        payload = json.dumps(diagnostic.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        super().__init__(f"{label}: {payload}")


def _required_columns(frame: pd.DataFrame, requested: Sequence[str] | None) -> tuple[str, ...]:
    names = REQUIRED_OHLCV_COLUMNS if requested is None else tuple(str(name) for name in requested)
    present = {str(name).casefold(): str(name) for name in frame.columns}
    return tuple(present[name.casefold()] for name in names if name.casefold() in present)


def _error_samples(
    frame: pd.DataFrame,
    invalid_mask: pd.Series,
    required_columns: tuple[str, ...],
    *,
    sample_limit: int,
) -> tuple[Mapping[str, object], ...]:
    samples: list[dict[str, object]] = []
    for position in invalid_mask.to_numpy().nonzero()[0]:
        row = frame.iloc[int(position)]
        invalid_columns = [name for name in required_columns if pd.isna(row[name])]
        samples.append(
            {
                "stock_code": _render_sample_value(row.get("stock_code")),
                "trade_date": _render_sample_value(row.get("trade_date")),
                "invalid_columns": sorted(invalid_columns, key=str.casefold),
            }
        )
    samples.sort(key=_sample_sort_key)
    return tuple(samples[:sample_limit])


def attach_incomplete_ohlcv_diagnostic(
    frame: pd.DataFrame,
    diagnostic: IncompleteOhlcvDiagnostic,
) -> pd.DataFrame:
    frame.attrs[INCOMPLETE_OHLCV_DIAGNOSTIC_ATTR] = diagnostic.to_dict()
    return frame


def apply_incomplete_ohlcv_policy(
    frame: pd.DataFrame,
    *,
    policy: object = DEFAULT_INCOMPLETE_OHLCV_POLICY,
    required_columns: Sequence[str] | None = None,
    source: str,
    sample_limit: int = DEFAULT_ERROR_SAMPLE_LIMIT,
    emit_warning: bool = True,
) -> tuple[pd.DataFrame, IncompleteOhlcvDiagnostic]:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("incomplete OHLCV policy requires a pandas.DataFrame")
    resolved_policy = normalize_incomplete_ohlcv_policy(policy)
    if isinstance(sample_limit, bool) or int(sample_limit) <= 0:
        raise ValueError("sample_limit must be a positive integer")
    required = _required_columns(frame, required_columns)
    out = frame.copy()
    for column in required:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    invalid_mask = (
        out.loc[:, list(required)].isna().any(axis=1)
        if required
        else pd.Series(False, index=out.index, dtype=bool)
    )
    dropped_rows = int(invalid_mask.sum())
    input_rows = int(len(out))
    kept_rows = input_rows - dropped_rows
    if dropped_rows == 0:
        outcome = "clean"
    elif kept_rows == 0:
        outcome = "all_invalid"
    elif resolved_policy == "reject":
        outcome = "rejected"
    else:
        outcome = "dropped"
    diagnostic = IncompleteOhlcvDiagnostic(
        policy=resolved_policy,
        source=str(source or "unknown"),
        outcome=outcome,
        input_rows=input_rows,
        kept_rows=kept_rows,
        dropped_rows=dropped_rows,
        required_columns=required,
        error_samples=_error_samples(
            out,
            invalid_mask,
            required,
            sample_limit=int(sample_limit),
        ),
    )
    if dropped_rows and (resolved_policy == "reject" or diagnostic.all_rows_invalid):
        raise IncompleteOhlcvDataError(diagnostic)
    if dropped_rows:
        payload = json.dumps(diagnostic.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        if emit_warning:
            log_warn(f"[OHLCV_INCOMPLETE] {payload}")
        out = out.loc[~invalid_mask].copy().reset_index(drop=True)
    return attach_incomplete_ohlcv_diagnostic(out, diagnostic), diagnostic


@dataclass(slots=True)
class IncompleteOhlcvAuditAccumulator:
    policy: str
    source: str
    sample_limit: int = DEFAULT_ERROR_SAMPLE_LIMIT
    input_rows: int = 0
    kept_rows: int = 0
    dropped_rows: int = 0
    required_columns: set[str] = field(default_factory=set)
    error_samples: list[Mapping[str, object]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.policy = normalize_incomplete_ohlcv_policy(self.policy)

    def add(self, value: IncompleteOhlcvDiagnostic | Mapping[str, object]) -> None:
        diagnostic = IncompleteOhlcvDiagnostic.from_value(value)
        if diagnostic.policy != self.policy:
            raise ValueError("cannot combine incomplete OHLCV diagnostics with different policies")
        self.input_rows += diagnostic.input_rows
        self.kept_rows += diagnostic.kept_rows
        self.dropped_rows += diagnostic.dropped_rows
        self.required_columns.update(diagnostic.required_columns)
        candidates = [*self.error_samples, *diagnostic.error_samples]
        self.error_samples = sorted(candidates, key=_sample_sort_key)[: self.sample_limit]

    def snapshot(self) -> IncompleteOhlcvDiagnostic:
        return IncompleteOhlcvDiagnostic(
            policy=self.policy,
            source=self.source,
            outcome="dropped" if self.dropped_rows else "clean",
            input_rows=self.input_rows,
            kept_rows=self.kept_rows,
            dropped_rows=self.dropped_rows,
            required_columns=tuple(
                name for name in REQUIRED_OHLCV_COLUMNS if name in self.required_columns
            ),
            error_samples=tuple(self.error_samples),
        )


__all__ = [
    "DEFAULT_INCOMPLETE_OHLCV_POLICY",
    "INCOMPLETE_OHLCV_DIAGNOSTIC_ATTR",
    "INCOMPLETE_OHLCV_POLICIES",
    "IncompleteOhlcvAuditAccumulator",
    "IncompleteOhlcvDataError",
    "IncompleteOhlcvDiagnostic",
    "REQUIRED_OHLCV_COLUMNS",
    "apply_incomplete_ohlcv_policy",
    "attach_incomplete_ohlcv_diagnostic",
    "configured_incomplete_ohlcv_policy",
    "normalize_incomplete_ohlcv_policy",
]
