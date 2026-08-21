"""Typed artifact schema registry for schema-v2 generation publication."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Iterable, Literal, Mapping

from core.artifact_schema import ARTIFACT_SCHEMA_VERSION


PathRuleKind = Literal["exact", "template"]
ArtifactCardinality = Literal["one", "optional_one", "many"]

_GENERATION_RULES = frozenset({"immutable_copy", "external_append_only"})
_MEDIA_TYPES = frozenset({"CSV", "JSON", "JSONL", "JSON/JSONL", "TEXT"})
_CARDINALITIES = frozenset({"one", "optional_one", "many"})
_CATALOG_KEY_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_TEMPLATE_TOKEN_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_TEMPLATE_TOKENS: Mapping[str, str] = MappingProxyType(
    {
        "file_name": r"[A-Za-z0-9][A-Za-z0-9._-]*",
        "frame_name": r"[A-Za-z0-9][A-Za-z0-9._-]*",
        "run_id": r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}",
        "stock_code": r"[A-Za-z0-9][A-Za-z0-9._-]*",
    }
)


def _normalized_catalog_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"invalid artifact catalog path: {value!r}")
    return path.as_posix()


@lru_cache(maxsize=None)
def _compile_template(pattern: str) -> re.Pattern[str]:
    normalized = _normalized_catalog_path(pattern)
    chunks: list[str] = []
    cursor = 0
    matches = tuple(_TEMPLATE_TOKEN_RE.finditer(normalized))
    if not matches:
        raise ValueError(f"artifact template path has no token: {pattern!r}")
    for match in matches:
        literal = normalized[cursor : match.start()]
        if "{" in literal or "}" in literal:
            raise ValueError(f"artifact template path has invalid braces: {pattern!r}")
        token = match.group(1)
        try:
            token_pattern = _TEMPLATE_TOKENS[token]
        except KeyError as exc:
            raise ValueError(f"artifact template path has unsupported token: {token!r}") from exc
        chunks.append(re.escape(literal))
        chunks.append(f"(?:{token_pattern})")
        cursor = match.end()
    remainder = normalized[cursor:]
    if "{" in remainder or "}" in remainder:
        raise ValueError(f"artifact template path has invalid braces: {pattern!r}")
    chunks.append(re.escape(remainder))
    return re.compile("".join(chunks) + r"\Z")


@dataclass(frozen=True, slots=True)
class ArtifactPathRule:
    kind: PathRuleKind
    patterns: tuple[str, ...]
    excluded_suffixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"exact", "template"}:
            raise ValueError(f"invalid artifact path rule kind: {self.kind!r}")
        if not self.patterns:
            raise ValueError("artifact path rule must declare at least one pattern")
        normalized = tuple(_normalized_catalog_path(pattern) for pattern in self.patterns)
        if len(set(normalized)) != len(normalized):
            raise ValueError("artifact path rule contains duplicate patterns")
        if self.kind == "template":
            for pattern in normalized:
                _compile_template(pattern)
        elif any("{" in pattern or "}" in pattern for pattern in normalized):
            raise ValueError("exact artifact path rules cannot contain template tokens")
        excluded = tuple(str(suffix) for suffix in self.excluded_suffixes)
        if any(not suffix for suffix in excluded):
            raise ValueError("artifact path rule contains an empty excluded suffix")
        object.__setattr__(self, "patterns", normalized)
        object.__setattr__(self, "excluded_suffixes", excluded)

    def matches(self, relative_path: str) -> bool:
        try:
            normalized = _normalized_catalog_path(relative_path)
        except ValueError:
            return False
        if any(normalized.endswith(suffix) for suffix in self.excluded_suffixes):
            return False
        if self.kind == "exact":
            return normalized in self.patterns
        return any(_compile_template(pattern).fullmatch(normalized) for pattern in self.patterns)


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    catalog_key: str
    owner: str
    schema_version: int
    writer: str
    reader: str
    generation_rule: str
    media_type: str
    path_rule: ArtifactPathRule
    cardinality: ArtifactCardinality

    def __post_init__(self) -> None:
        if not _CATALOG_KEY_RE.fullmatch(self.catalog_key):
            raise ValueError(f"invalid artifact catalog key: {self.catalog_key!r}")
        for field_name in ("owner", "writer", "reader"):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"artifact spec {self.catalog_key!r} has an empty {field_name}")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ValueError(f"artifact spec {self.catalog_key!r} has an invalid schema version")
        if self.schema_version <= 0:
            raise ValueError(f"artifact spec {self.catalog_key!r} has a non-positive schema version")
        if self.generation_rule not in _GENERATION_RULES:
            raise ValueError(f"artifact spec {self.catalog_key!r} has an invalid generation rule")
        if self.media_type not in _MEDIA_TYPES:
            raise ValueError(f"artifact spec {self.catalog_key!r} has an invalid media type")
        if self.cardinality not in _CARDINALITIES:
            raise ValueError(f"artifact spec {self.catalog_key!r} has an invalid cardinality")
        if not isinstance(self.path_rule, ArtifactPathRule):
            raise ValueError(f"artifact spec {self.catalog_key!r} has no valid path rule")

    def allows_path(self, relative_path: str) -> bool:
        return self.path_rule.matches(relative_path)


def build_artifact_catalog(specs: Iterable[ArtifactSpec]) -> Mapping[str, ArtifactSpec]:
    catalog: dict[str, ArtifactSpec] = {}
    for spec in specs:
        if not isinstance(spec, ArtifactSpec):
            raise ValueError(f"artifact registry contains an invalid spec: {spec!r}")
        if spec.catalog_key in catalog:
            raise ValueError(f"duplicate artifact catalog key: {spec.catalog_key!r}")
        catalog[spec.catalog_key] = spec
    if not catalog:
        raise ValueError("artifact registry cannot be empty")
    return MappingProxyType(catalog)


def _exact(*patterns: str) -> ArtifactPathRule:
    return ArtifactPathRule("exact", tuple(patterns))


def _template(*patterns: str, excluded_suffixes: tuple[str, ...] = ()) -> ArtifactPathRule:
    return ArtifactPathRule("template", tuple(patterns), excluded_suffixes)


_REPORTING_WRITER = "backtest.reporting"
_GENERATION_READER = "backtest.artifacts.resolve_current_generation"
_PROTOCOL_WRITER = "app.run_protocol_artifacts"
_WEB_CACHE_WRITER = "web.library_runtime"
_WEB_CACHE_READER = "web.cache_generation"

_SPECS = (
    ArtifactSpec("artifact_manifest", "app.session_metadata", ARTIFACT_SCHEMA_VERSION, _PROTOCOL_WRITER, _GENERATION_READER, "immutable_copy", "JSON", _template("run_meta/{run_id}_artifact_manifest.json"), "one"),
    ArtifactSpec("backtest_summary", "backtest.reporting", ARTIFACT_SCHEMA_VERSION, _REPORTING_WRITER, _GENERATION_READER, "immutable_copy", "CSV", _exact("backtest_summary.csv"), "optional_one"),
    ArtifactSpec("compiled_plan", "app.session_metadata", ARTIFACT_SCHEMA_VERSION, _PROTOCOL_WRITER, _GENERATION_READER, "immutable_copy", "JSON", _template("run_meta/{run_id}_compiled_plan.json"), "one"),
    ArtifactSpec(
        "decision_artifacts", "backtest.decisions", ARTIFACT_SCHEMA_VERSION, _REPORTING_WRITER, _GENERATION_READER, "immutable_copy", "CSV",
        _exact(
            "signal_data/all_trade_dates.csv",
            "signal_data/all_trade_dates_report.csv",
            "signal_data/merged_signals.csv",
            "signal_data/merged_signals_candidates.csv",
            "signal_data/signal_daily_summary.csv",
            "signal_data/signal_input_report.csv",
            "signal_data/topn_trade_segments.csv",
            "signal_data/topn_trade_segments_summary.csv",
        ),
        "many",
    ),
    ArtifactSpec("diagnostics", "app.diagnostics_service", ARTIFACT_SCHEMA_VERSION, "app.diagnostics_service", _GENERATION_READER, "immutable_copy", "JSON", _template("run_meta/{run_id}_diagnostics.json"), "optional_one"),
    ArtifactSpec("experiments", "core.experiment_registry", ARTIFACT_SCHEMA_VERSION, "core.experiment_registry", _GENERATION_READER, "immutable_copy", "JSON/JSONL", _exact("experiments/best_candidates.json", "experiments/experiments.jsonl"), "many"),
    ArtifactSpec("execution_ledger", "backtest.result", ARTIFACT_SCHEMA_VERSION, _REPORTING_WRITER, _GENERATION_READER, "immutable_copy", "CSV", _exact("net_value_unique_date/portfolio_daily_state.csv", "net_value_unique_date/portfolio_fills.csv", "net_value_unique_date/portfolio_orders.csv", "net_value_unique_date/portfolio_rejections.csv"), "many"),
    ArtifactSpec("execution_summary", "backtest.result", ARTIFACT_SCHEMA_VERSION, _REPORTING_WRITER, _GENERATION_READER, "immutable_copy", "CSV", _exact("net_value_unique_date/risk_control_events.csv", "net_value_unique_date/risk_control_summary.csv", "net_value_unique_date/trade_segments_summary.csv"), "many"),
    ArtifactSpec("market_state", "backtest.reporting", ARTIFACT_SCHEMA_VERSION, _REPORTING_WRITER, _GENERATION_READER, "immutable_copy", "JSON", _exact("market_state.json"), "optional_one"),
    ArtifactSpec("net_value", "backtest.result", ARTIFACT_SCHEMA_VERSION, _REPORTING_WRITER, _GENERATION_READER, "immutable_copy", "CSV", _exact("net_value_unique_date/merged_net_value_with_sum.csv"), "optional_one"),
    ArtifactSpec("processed_frames", "backtest.result", ARTIFACT_SCHEMA_VERSION, _REPORTING_WRITER, _GENERATION_READER, "immutable_copy", "CSV", _template("return_date_with_returns/{frame_name}_with_returns.csv"), "many"),
    ArtifactSpec("research_audit", "app.session_metadata", ARTIFACT_SCHEMA_VERSION, _PROTOCOL_WRITER, _GENERATION_READER, "immutable_copy", "JSON", _template("run_meta/{run_id}_audit_research.json"), "one"),
    ArtifactSpec("robustness_report", "app.diagnostics_service", ARTIFACT_SCHEMA_VERSION, _PROTOCOL_WRITER, _GENERATION_READER, "immutable_copy", "JSON", _template("run_meta/{run_id}_robustness.json"), "one"),
    ArtifactSpec("run_events", "core.run_events", ARTIFACT_SCHEMA_VERSION, "core.run_events", "core.run_events", "external_append_only", "JSONL", _template("run_meta/run_events_{run_id}.jsonl"), "optional_one"),
    ArtifactSpec("run_log", "core.logging", ARTIFACT_SCHEMA_VERSION, "core.logging", "core.logging", "external_append_only", "TEXT", _template("logs/{file_name}"), "many"),
    ArtifactSpec(
        "run_metadata", "app.session_metadata", ARTIFACT_SCHEMA_VERSION, "core.run_metadata", _GENERATION_READER, "immutable_copy", "JSON",
        _template(
            "run_meta/{run_id}.json",
            excluded_suffixes=("_artifact_manifest.json", "_audit_research.json", "_compiled_plan.json", "_diagnostics.json", "_robustness.json"),
        ),
        "one",
    ),
    ArtifactSpec("single_price", "backtest.reporting", ARTIFACT_SCHEMA_VERSION, "backtest.reporting.single_stock_outputs", _GENERATION_READER, "immutable_copy", "CSV", _template("stock_data/{stock_code}_data.csv"), "optional_one"),
    ArtifactSpec("single_signal", "backtest.reporting", ARTIFACT_SCHEMA_VERSION, "backtest.reporting.single_stock_outputs", _GENERATION_READER, "immutable_copy", "CSV", _template("signal_data/{stock_code}_signal.csv"), "optional_one"),
    ArtifactSpec("single_trades", "backtest.reporting", ARTIFACT_SCHEMA_VERSION, "backtest.reporting.single_stock_outputs", _GENERATION_READER, "immutable_copy", "CSV", _template("trade_data/{stock_code}_trades.csv"), "optional_one"),
    ArtifactSpec("strategy_spec", "strategies.spec", ARTIFACT_SCHEMA_VERSION, "strategies.spec", _GENERATION_READER, "immutable_copy", "JSON", _exact("signal_data/strategy_spec.json"), "optional_one"),
    ArtifactSpec("trading_frames", "backtest.result", ARTIFACT_SCHEMA_VERSION, _REPORTING_WRITER, _GENERATION_READER, "immutable_copy", "CSV", _template("return_date/{frame_name}.csv"), "many"),
    ArtifactSpec("web_cache_daily_holdings", "web.strategy_library", ARTIFACT_SCHEMA_VERSION, _WEB_CACHE_WRITER, _WEB_CACHE_READER, "immutable_copy", "JSON", _exact("cache/daily_holdings.json"), "optional_one"),
    ArtifactSpec("web_cache_market_state", "web.strategy_library", ARTIFACT_SCHEMA_VERSION, _WEB_CACHE_WRITER, _WEB_CACHE_READER, "immutable_copy", "JSON", _exact("cache/market_state.json"), "optional_one"),
    ArtifactSpec("web_cache_meta", "web.strategy_library", ARTIFACT_SCHEMA_VERSION, _WEB_CACHE_WRITER, _WEB_CACHE_READER, "immutable_copy", "JSON", _exact("cache/meta.json"), "one"),
    ArtifactSpec("web_cache_net_value", "web.strategy_library", ARTIFACT_SCHEMA_VERSION, _WEB_CACHE_WRITER, _WEB_CACHE_READER, "immutable_copy", "JSON", _exact("cache/net_value.json"), "optional_one"),
    ArtifactSpec("web_cache_position_changes", "web.strategy_library", ARTIFACT_SCHEMA_VERSION, _WEB_CACHE_WRITER, _WEB_CACHE_READER, "immutable_copy", "JSON", _exact("cache/position_changes.json"), "optional_one"),
    ArtifactSpec("web_cache_positions", "web.strategy_library", ARTIFACT_SCHEMA_VERSION, _WEB_CACHE_WRITER, _WEB_CACHE_READER, "immutable_copy", "JSON", _exact("cache/positions.json"), "optional_one"),
    ArtifactSpec("web_cache_trades", "web.strategy_library", ARTIFACT_SCHEMA_VERSION, _WEB_CACHE_WRITER, _WEB_CACHE_READER, "immutable_copy", "JSON", _exact("cache/trades.json"), "optional_one"),
)

ARTIFACT_CATALOG = build_artifact_catalog(_SPECS)


def catalog_entry(key: str) -> ArtifactSpec:
    try:
        return ARTIFACT_CATALOG[str(key)]
    except KeyError as exc:
        raise ValueError(f"artifact catalog key is not registered: {key!r}") from exc


def validate_catalog_path(catalog_key: str, relative_path: str) -> ArtifactSpec:
    spec = catalog_entry(catalog_key)
    if not spec.allows_path(relative_path):
        raise ValueError(
            f"artifact path is not allowed for catalog key {catalog_key!r}: {relative_path!r}"
        )
    return spec


__all__ = [
    "ARTIFACT_CATALOG",
    "ArtifactCardinality",
    "ArtifactPathRule",
    "ArtifactSpec",
    "build_artifact_catalog",
    "catalog_entry",
    "validate_catalog_path",
]
