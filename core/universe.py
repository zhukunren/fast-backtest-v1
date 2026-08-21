"""Declarative universe contracts for strategy runs."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]).casefold())}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _string_tuple(values: Iterable[object] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            out.append(text)
    return tuple(out)


def universe_code_hash(stock_code_list: Iterable[object]) -> str:
    codes = [str(code).strip() for code in stock_code_list if str(code).strip()]
    text = json.dumps(codes, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class UniverseSpec:
    kind: str
    source: str
    prefixes: tuple[str, ...] = ()
    exclude_bj: bool = False
    exclude_st: bool = False
    symbols: tuple[str, ...] = ()
    benchmark_code: str = ""
    benchmark_name: str = ""
    assumption: dict[str, Any] = field(default_factory=dict)
    locked: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "source": str(self.source),
            "prefixes": list(self.prefixes),
            "exclude_bj": bool(self.exclude_bj),
            "exclude_st": bool(self.exclude_st),
            "symbols": list(self.symbols),
            "benchmark_code": str(self.benchmark_code),
            "benchmark_name": str(self.benchmark_name),
            "assumption": _json_safe(self.assumption),
            "locked": bool(self.locked),
            "reason": str(self.reason),
        }


@dataclass(frozen=True)
class UniverseResolution:
    spec: UniverseSpec
    stock_code_list: tuple[str, ...]
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def stock_count(self) -> int:
        return len(self.stock_code_list)

    @property
    def stock_code_hash(self) -> str:
        return universe_code_hash(self.stock_code_list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "source": str(self.source),
            "stock_count": int(self.stock_count),
            "stock_code_hash": self.stock_code_hash,
            "stock_code_list": list(self.stock_code_list),
            "metadata": _json_safe(self.metadata),
            "prefixes": list(self.spec.prefixes),
            "exclude_bj": bool(self.spec.exclude_bj),
            "exclude_st": bool(self.spec.exclude_st),
            "benchmark_code": str(self.spec.benchmark_code),
            "benchmark_name": str(self.spec.benchmark_name),
        }


def _strategy_universe_assumption(strategy_spec: object | None) -> dict[str, Any]:
    assumption = getattr(strategy_spec, "universe_assumption", None)
    return dict(assumption) if isinstance(assumption, dict) else {}


def _assumption_locked(assumption: dict[str, Any]) -> bool:
    for key in ("universe_locked", "locked", "require_exact_universe"):
        if key in assumption:
            return bool(assumption.get(key))
    return False


def build_config_universe_spec(*, cfg: object, strategy_spec: object | None = None) -> UniverseSpec:
    backtest = getattr(cfg, "backtest", None)
    assumption = _strategy_universe_assumption(strategy_spec)
    kind = str(assumption.get("type") or "config_filters").strip() or "config_filters"
    return UniverseSpec(
        kind=kind,
        source="config.backtest",
        prefixes=_string_tuple(getattr(backtest, "universe_prefixes", ()) or ()),
        exclude_bj=bool(getattr(backtest, "exclude_bj", False)),
        exclude_st=bool(getattr(backtest, "exclude_st", False)),
        benchmark_code=str(getattr(backtest, "benchmark_code", "") or ""),
        benchmark_name=str(getattr(backtest, "benchmark_name", "") or ""),
        assumption=assumption,
        locked=_assumption_locked(assumption),
    )


def build_runtime_symbols_universe_spec(
    *,
    symbols: Iterable[object],
    cfg: object | None = None,
    strategy_spec: object | None = None,
    reason: str = "runtime stock_code_list override",
) -> UniverseSpec:
    backtest = getattr(cfg, "backtest", None) if cfg is not None else None
    assumption = _strategy_universe_assumption(strategy_spec)
    return UniverseSpec(
        kind="runtime_symbols",
        source="runtime.stock_code_list",
        prefixes=_string_tuple(getattr(backtest, "universe_prefixes", ()) or ()),
        exclude_bj=bool(getattr(backtest, "exclude_bj", False)),
        exclude_st=bool(getattr(backtest, "exclude_st", False)),
        symbols=_string_tuple(symbols),
        benchmark_code=str(getattr(backtest, "benchmark_code", "") or ""),
        benchmark_name=str(getattr(backtest, "benchmark_name", "") or ""),
        assumption=assumption,
        locked=_assumption_locked(assumption),
        reason=reason,
    )


def build_fixed_rebalance_universe_resolution(*, cfg: object) -> UniverseResolution:
    fixed_cfg = getattr(cfg, "fixed_rebalance", None)
    spec = UniverseSpec(
        kind="fixed_rebalance_targets",
        source="config.fixed_rebalance",
        assumption={
            "symbol_type": getattr(fixed_cfg, "symbol_type", ""),
            "target_weights": getattr(fixed_cfg, "target_weights", ""),
            "rebalance_frequency": getattr(fixed_cfg, "rebalance_frequency", ""),
        },
        reason="fixed_rebalance mode resolves tradable symbols from target weights",
    )
    return UniverseResolution(
        spec=spec,
        stock_code_list=(),
        source="config.fixed_rebalance",
        metadata={"resolution_deferred_to": "backtest.fixed_rebalance_runner"},
    )


__all__ = [
    "UniverseResolution",
    "UniverseSpec",
    "build_config_universe_spec",
    "build_fixed_rebalance_universe_resolution",
    "build_runtime_symbols_universe_spec",
    "universe_code_hash",
]
