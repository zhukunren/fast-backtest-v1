"""Fail-closed contracts for future external validation evidence."""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from core.exceptions import ValidationError


EvidenceStatus = Literal[
    "completed_offline_engineering_evidence",
    "deferred_external_data",
    "not_production_capital_certification",
]
EngineId = Literal["legacy", "slot", "slot_broker", "broker", "fixed_rebalance"]
EXPECTED_ENGINE_IDS = frozenset({"legacy", "slot", "slot_broker", "broker", "fixed_rebalance"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT_RE = re.compile(r"[0-9a-f]{40,64}\Z")


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class MarketSnapshotIdentity(_EvidenceModel):
    provider: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    market: str = Field(min_length=1)
    date_start: date
    date_end: date
    adjustment: str = Field(min_length=1)
    timezone: str = Field(min_length=1)
    schema_id: str = Field(min_length=1)
    sha256: str
    size_bytes: int = Field(gt=0)
    row_count: int = Field(gt=0)
    trusted_external: bool

    @field_validator("sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def _validate_date_range(self) -> MarketSnapshotIdentity:
        if self.date_start > self.date_end:
            raise ValueError("market snapshot date_start must not be after date_end")
        return self


class EngineComparison(_EvidenceModel):
    engine_id: EngineId
    baseline_engine_id: EngineId
    final_net_value: float = Field(gt=0)
    trade_count: int = Field(ge=0)
    net_value_max_abs_diff: float = Field(ge=0)
    fill_count_diff: int
    allowed_difference_reasons: tuple[str, ...] = ()

    @field_validator("final_net_value", "net_value_max_abs_diff")
    @classmethod
    def _validate_finite_number(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("engine comparison values must be finite")
        return value

    @field_validator("allowed_difference_reasons")
    @classmethod
    def _validate_difference_reasons(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("allowed difference reasons cannot be empty")
        return normalized


class BrokerReconciliation(_EvidenceModel):
    order_count: int = Field(ge=0)
    fill_count: int = Field(ge=0)
    rejected_order_count: int = Field(ge=0)
    cancelled_order_count: int = Field(ge=0)
    fee_total: float = Field(ge=0)
    fee_max_abs_diff: float = Field(ge=0)
    trading_calendar_basis: str = Field(min_length=1)
    price_basis: str = Field(min_length=1)

    @field_validator("fee_total", "fee_max_abs_diff")
    @classmethod
    def _validate_finite_fee(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("broker reconciliation fees must be finite")
        return value


class ResourceMeasurement(_EvidenceModel):
    engine_id: EngineId
    repetitions: int = Field(ge=2)
    elapsed_seconds: tuple[float, ...] = Field(min_length=2)
    peak_rss_mib: tuple[float, ...] = Field(min_length=2)
    aggregate: Literal["median", "mean", "min", "max"]

    @field_validator("elapsed_seconds", "peak_rss_mib")
    @classmethod
    def _validate_measurements(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("resource measurements must be finite and non-negative")
        return values

    @model_validator(mode="after")
    def _validate_repetition_count(self) -> ResourceMeasurement:
        if len(self.elapsed_seconds) != self.repetitions or len(self.peak_rss_mib) != self.repetitions:
            raise ValueError("resource measurement lengths must equal repetitions")
        return self


class ExternalValidationReport(_EvidenceModel):
    schema_version: Literal[1]
    status: EvidenceStatus
    status_detail: str = Field(min_length=1)
    candidate_commit: str
    config_sha256: str
    market_snapshot: MarketSnapshotIdentity
    engine_comparisons: tuple[EngineComparison, ...]
    broker_reconciliation: BrokerReconciliation
    resource_measurements: tuple[ResourceMeasurement, ...]

    @field_validator("candidate_commit")
    @classmethod
    def _validate_candidate_commit(cls, value: str) -> str:
        if not _COMMIT_RE.fullmatch(value):
            raise ValueError("candidate_commit must be a 40-64 character lowercase hexadecimal identity")
        return value

    @field_validator("config_sha256")
    @classmethod
    def _validate_config_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("config_sha256 must be 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def _validate_report_matrix_and_status(self) -> ExternalValidationReport:
        engine_ids = [item.engine_id for item in self.engine_comparisons]
        resource_ids = [item.engine_id for item in self.resource_measurements]
        if len(engine_ids) != len(set(engine_ids)) or set(engine_ids) != EXPECTED_ENGINE_IDS:
            raise ValueError("engine comparisons must contain each built-in engine exactly once")
        if len(resource_ids) != len(set(resource_ids)) or set(resource_ids) != EXPECTED_ENGINE_IDS:
            raise ValueError("resource measurements must contain each built-in engine exactly once")
        if (
            not self.market_snapshot.trusted_external
            and self.status
            not in {"deferred_external_data", "not_production_capital_certification"}
        ):
            raise ValueError("untrusted or synthetic input cannot complete external validation")
        return self


def validate_external_validation_report(payload: Any) -> ExternalValidationReport:
    """Validate a report and translate Pydantic diagnostics to the project error type."""
    try:
        return cast(ExternalValidationReport, ExternalValidationReport.model_validate(payload))
    except PydanticValidationError as exc:
        raise ValidationError(
            f"external validation report is invalid: {exc}",
            errors=exc.errors(),
        ) from exc


def external_validation_json_schema() -> dict[str, Any]:
    return cast(dict[str, Any], ExternalValidationReport.model_json_schema())


__all__ = [
    "EXPECTED_ENGINE_IDS",
    "BrokerReconciliation",
    "EngineComparison",
    "EvidenceStatus",
    "ExternalValidationReport",
    "MarketSnapshotIdentity",
    "ResourceMeasurement",
    "external_validation_json_schema",
    "validate_external_validation_report",
]
