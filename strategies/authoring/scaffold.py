"""Compose neutral strategy scaffolds from profile and capability contracts."""
from __future__ import annotations

import ast
import hashlib
import keyword
import pprint
import re
from pathlib import Path
from typing import Any, Iterable

from .capabilities import (
    REGISTRY_VERSION,
    SUPPORTED_PROFILES,
    capability_payload,
    resolve_capabilities,
    unavailable_runtime_capabilities,
)
from .requirements import STANDARD_RUNTIME_ID, standard_runtime_payload


SCAFFOLD_SCHEMA_VERSION = "2.0"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATED_STRATEGY_DIR = PROJECT_ROOT / "strategy_library" / "generated"
SKELETON_DIR = Path(__file__).resolve().parent / "skeletons"

_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_BASE_FIELDS = ["trade_date", "stock_code", "open", "high", "low", "close", "volume"]


def _validate_strategy_name(name: str) -> str:
    normalized = str(name or "").strip()
    if not _SAFE_NAME_RE.fullmatch(normalized) or keyword.iskeyword(normalized):
        raise ValueError(
            "Strategy name must be a lowercase Python module identifier, start with a letter, "
            "and contain at most 80 letters, digits, or underscores"
        )
    return normalized


def _resolve_output_path(name: str, output_path: str | Path | None) -> Path:
    root = Path(GENERATED_STRATEGY_DIR).resolve()
    if output_path is None:
        target = root / f"{name}.py"
    else:
        requested = Path(output_path)
        target = requested if requested.is_absolute() else root / requested
        target = target.resolve()

    if target.suffix.casefold() != ".py":
        raise ValueError("Strategy scaffold output must be a .py file")
    if target.name != f"{name}.py":
        raise ValueError(f"Output filename must match the strategy name: {name}.py")
    try:
        relative = target.relative_to(root)
    except ValueError:
        raise ValueError(f"Strategy scaffold output must stay inside {root}") from None
    if len(relative.parts) != 1:
        raise ValueError("Strategy scaffold output must be a direct child of strategy_library/generated")
    return target


def _profile_name(profile: str) -> str:
    normalized = str(profile or "").strip().casefold()
    if normalized not in SUPPORTED_PROFILES:
        raise ValueError(f"Unknown strategy profile {profile!r}; expected {list(SUPPORTED_PROFILES)}")
    return normalized


def _strategy_meta(name: str, profile: str, selected: set[str]) -> dict[str, Any]:
    sort_column = "factor_value" if profile == "factor" else "candidate_priority"
    required_fields = list(_BASE_FIELDS)
    meta: dict[str, Any] = {
        "strategy_contract_version": "2.0",
        "execution_mode": "panel",
        "name": name,
        "required_fields": required_fields,
        "sort_spec": [f"-{sort_column}"],
        "portfolio_num_j": 1,
        "portfolio_num_i": 1,
    }

    if "research_contract" in selected:
        meta.update(
            {
                "hypothesis": "__REQUIRED__",
                "universe_assumption": {
                    "source": "__REQUIRED__",
                    "locked": "__REQUIRED__",
                    "notes": "__REQUIRED__",
                },
                "validation": {
                    "train_period": ["__REQUIRED__", "__REQUIRED__"],
                    "test_period": ["__REQUIRED__", "__REQUIRED__"],
                },
                "expected_market_regimes": ["__REQUIRED__"],
            }
        )

    if "extra_price_columns" in selected:
        meta["price_columns"] = ["__REQUIRED__"]
        required_fields.append("__REQUIRED__")

    use_market_features = bool(selected.intersection({"market_rank", "market_breadth"}))
    meta["use_market_features"] = use_market_features
    meta["market_rank_specs"] = (
        [
            {
                "feature_col": "__REQUIRED__",
                "rank_col": "__REQUIRED__",
                "pct_col": "__REQUIRED__",
                "count_col": "__REQUIRED__",
                "ascending": "__REQUIRED__",
            }
        ]
        if "market_rank" in selected
        else []
    )
    meta["market_aggregate_specs"] = (
        [
            {
                "source_col": "__REQUIRED__",
                "output_col": "__REQUIRED__",
                "agg": "__REQUIRED__",
            }
        ]
        if "market_breadth" in selected
        else []
    )
    meta["market_feature_passthrough_cols"] = (
        ["__REQUIRED__"] if "market_passthrough" in selected else []
    )

    meta["use_index_timing"] = "index_timing" in selected
    if "index_timing" in selected:
        meta["index_code"] = "__REQUIRED__"
        meta["index_timing_columns"] = [
            "trade_date",
            "close",
            "volume",
            "amount",
            "high",
            "low",
            "open",
        ]

    board_enabled = bool(selected.intersection({"board_membership", "board_timing"}))
    meta["use_board_timing"] = "board_timing" in selected
    meta["require_board_member"] = "board_membership" in selected
    meta["board_index_codes"] = ["__REQUIRED__"] if board_enabled else []
    if "board_timing" in selected:
        meta["board_timing_columns"] = [
            "trade_date",
            "close",
            "volume",
            "open",
            "high",
            "low",
        ]

    risk: dict[str, Any] = {
        "enable_nv": "nav_risk" in selected,
        "enable_dd": "drawdown_risk" in selected,
    }
    if "nav_risk" in selected:
        risk.update(
            {
                "nv_warn": "__REQUIRED__",
                "nv_danger": "__REQUIRED__",
                "nv_clear": "__REQUIRED__",
                "nv_ratios": ["__REQUIRED__", "__REQUIRED__", "__REQUIRED__", "__REQUIRED__"],
            }
        )
    if "drawdown_risk" in selected:
        risk.update(
            {
                "warn_dd": "__REQUIRED__",
                "danger_dd": "__REQUIRED__",
                "lock_dd": "__REQUIRED__",
                "dd_ratios": ["__REQUIRED__", "__REQUIRED__", "__REQUIRED__", "__REQUIRED__"],
            }
        )
    meta["risk_control"] = risk

    if "point_in_time_data" in selected:
        meta["auxiliary_data_specs"] = [
            {
                "dataset": "__REQUIRED__",
                "fields": ["__REQUIRED__"],
                "key_columns": ["stock_code"],
                "available_at_column": "available_at",
                "available_at_output_col": "aux_available_at",
                "join_policy": "asof_backward",
                "required": True,
            }
        ]
    if "reference_series" in selected:
        meta["reference_series_specs"] = [
            {
                "name": "__REQUIRED__",
                "symbol": "__REQUIRED__",
                "fields": ["close"],
                "output_prefix": "reference_",
                "join_policy": "asof_backward",
            }
        ]
    if "cross_section_transforms" in selected:
        meta["cross_section_transform_specs"] = [
            {
                "source_col": "__REQUIRED__",
                "output_col": "__REQUIRED__",
                "method": "pct_rank",
                "ascending": False,
            }
        ]
    if "position_policy" in selected:
        meta["position_policy"] = {
            "min_hold_days": "__REQUIRED__",
            "max_hold_days": "__REQUIRED__",
            "stop_loss_return": "__REQUIRED__",
            "take_profit_return": "__REQUIRED__",
            "trailing_drawdown": "__REQUIRED__",
            "cooldown_after_exit_days": "__REQUIRED__",
        }
    if "portfolio_construction" in selected:
        meta["portfolio_construction"] = {
            "mode": "fixed_weight" if "target_weight_decision" in selected else "top_n",
            "rebalance_frequency": "daily",
            "cash_buffer": 0.0,
            "max_single_weight": None,
            "max_turnover": None,
            "normalize_weights": True,
        }
    if "typed_parameters" in selected:
        meta["strategy_parameter_specs"] = {
            "lookback": {
                "type": "int",
                "default": 20,
                "min": 2,
                "max": 500,
                "description": "__REQUIRED__",
            }
        }
        meta["runtime_parameters"] = {}
    return meta


def _strategy_semantics(profile: str, selected: set[str]) -> dict[str, Any]:
    semantics: dict[str, Any] = {
        "strategy_type": profile,
        "market": "A_share",
        "timeframe": "daily",
    }
    if "research_contract" in selected:
        semantics.update(
            {
                "holding_period": "__REQUIRED__",
                "primary_signals": ["__REQUIRED__"],
                "risk_profile": "__REQUIRED__",
            }
        )
    return semantics


def _strategy_description(selected: set[str]) -> dict[str, str]:
    if "research_contract" not in selected:
        return {}
    return {
        "entry": "__REQUIRED__",
        "exit": "__REQUIRED__",
        "ranking": "__REQUIRED__",
        "market_features": "__REQUIRED__",
        "risk": "__REQUIRED__",
        "data_assumptions": "__REQUIRED__",
    }


def _strategy_limits() -> dict[str, int]:
    # Complexity budgets are opt-in research notes, not an authoring ceiling.
    return {}


def _market_hooks(selected: set[str], profile: str) -> str:
    if profile == "panel":
        return ""
    market_enabled = bool(selected.intersection({"market_rank", "market_breadth"}))
    if not market_enabled:
        return '''def _compute_panel_features(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[:, ["trade_date", "stock_code"]].copy()'''

    profile_enricher = {
        "rule": "feature_engine",
        "factor": "factor_engine",
    }[profile]
    return f'''def _declared_market_source_columns() -> list[str]:
    columns: list[str] = []
    for item in STRATEGY_META.get("market_rank_specs", []) or []:
        column = str(item.get("feature_col", "")).strip()
        if column:
            columns.append(column)
    for item in STRATEGY_META.get("market_aggregate_specs", []) or []:
        column = str(item.get("source_col", "")).strip()
        if column:
            columns.append(column)
    columns.extend(STRATEGY_META.get("market_feature_passthrough_cols", []) or [])
    return list(dict.fromkeys(str(column).strip() for column in columns if str(column).strip()))


def market_feature_sources(df: pd.DataFrame) -> pd.DataFrame:
    # <editable id="market_feature_sources">
    raise NotImplementedError(
        "__REQUIRED__: produce every declared rank, aggregate, and passthrough source column"
    )
    # </editable>


def _compute_panel_features(df: pd.DataFrame) -> pd.DataFrame:
    out = {profile_enricher}(df)
    out = market_feature_sources(out)
    columns = ["trade_date", "stock_code", *_declared_market_source_columns()]
    missing = sorted(set(columns) - set(out.columns), key=str.casefold)
    if missing:
        raise ValueError(f"market_feature_sources() missing declared columns: {{missing}}")
    return out.loc[:, columns].copy()'''


def _timing_hooks(selected: set[str]) -> str:
    if "index_timing" in selected:
        index_hook = '''def build_index_timing_signal(index_df: pd.DataFrame) -> pd.Series:
    # <editable id="index_timing">
    raise NotImplementedError("__REQUIRED__: implement a causal aligned index timing signal")
    # </editable>'''
    else:
        index_hook = '''def build_index_timing_signal(index_df: pd.DataFrame) -> pd.Series:
    return pd.Series(True, index=index_df.index, dtype=bool)'''

    if "board_timing" in selected:
        board_hook = '''def build_board_timing_signal(board_df: pd.DataFrame) -> pd.Series:
    # <editable id="board_timing">
    raise NotImplementedError("__REQUIRED__: implement a causal aligned board timing signal")
    # </editable>'''
    else:
        board_hook = '''def build_board_timing_signal(board_df: pd.DataFrame) -> pd.Series:
    return pd.Series(True, index=board_df.index, dtype=bool)'''
    return f"{index_hook}\n\n\n{board_hook}"


def _literal(value: Any) -> str:
    return pprint.pformat(value, width=100, sort_dicts=False)


def _advanced_hooks(selected: set[str]) -> str:
    if "target_weight_decision" not in selected:
        return ""
    return '''def build_target_weights(context):
    # <editable id="target_weight_decision">
    raise NotImplementedError("__REQUIRED__: return symbol-to-target-weight mapping")
    # </editable>'''


def _extra_imports(selected: set[str]) -> str:
    names: list[str] = []
    if "typed_parameters" in selected:
        names.append("resolve_declared_parameters")
    if not names:
        return ""
    return f"from strategies.api import {', '.join(names)}"


def _panel_hooks(profile: str) -> str:
    if profile == "panel":
        return "\n\n\n".join(
            (
                _read_skeleton("panel_features.py.tmpl"),
                _read_skeleton("panel_signals.py.tmpl"),
            )
        )
    return ""


def _runtime_wiring(profile: str, *, advanced_wiring: str) -> str:
    feature_body = (
        "        return _compute_features(panel)"
        if profile == "panel"
        else '''        return compute_feature_batch(
            panel,
            _compute_panel_features,
            strategy_meta=self.STRATEGY_META,
        )'''
    )
    signal_body = (
        "        return _compute_signals(panel)"
        if profile == "panel"
        else "        return compute_signal_batch(panel, _compute_panel_signals)"
    )
    return f'''def build_time_ok(stock_code: str) -> pd.DataFrame:
    return build_index_time_ok(
        strategy_meta=STRATEGY_META,
        build_index_timing_signal=build_index_timing_signal,
        stock_code=stock_code,
    )


def build_board_time_ok(board_code: str) -> pd.DataFrame:
    return _build_board_time_ok(
        strategy_meta=STRATEGY_META,
        build_board_timing_signal=build_board_timing_signal,
        board_code=board_code,
    )


class Strategy(BaseStrategy):
    STRATEGY_META = STRATEGY_META
    STRATEGY_SEMANTICS = STRATEGY_SEMANTICS
    STRATEGY_DESCRIPTION = STRATEGY_DESCRIPTION
    STRATEGY_LIMITS = STRATEGY_LIMITS

    def compute_features(self, panel: PanelView) -> FeatureBatch:
{feature_body}

    def compute_signals(self, panel: PanelView) -> SignalBatch:
{signal_body}

    build_time_ok = staticmethod(build_time_ok)
    build_board_time_ok = staticmethod(build_board_time_ok)
{advanced_wiring}'''


def _parameter_binding(selected: set[str]) -> str:
    if "typed_parameters" not in selected:
        return ""
    return "PARAMETERS = resolve_declared_parameters(STRATEGY_META)"


def _read_skeleton(name: str) -> str:
    path = SKELETON_DIR / name
    return path.read_text(encoding="utf-8").rstrip()


def _render_strategy(name: str, profile: str, capabilities: tuple[str, ...]) -> str:
    selected = set(capabilities)
    base = _read_skeleton("base.py.tmpl")
    profile_hooks = "" if profile == "panel" else _read_skeleton(f"{profile}.py.tmpl")
    advanced_wiring = (
        "    build_target_weights = staticmethod(build_target_weights)"
        if "target_weight_decision" in selected
        else ""
    )
    replacements = {
        "{{PROFILE}}": profile,
        "{{REGISTRY_VERSION}}": REGISTRY_VERSION,
        "{{CAPABILITY_LIST}}": ", ".join(capabilities),
        "{{EXTRA_IMPORTS}}": _extra_imports(selected),
        "{{STRATEGY_META}}": _literal(_strategy_meta(name, profile, selected)),
        "{{PARAMETER_BINDING}}": _parameter_binding(selected),
        "{{STRATEGY_SEMANTICS}}": _literal(_strategy_semantics(profile, selected)),
        "{{STRATEGY_DESCRIPTION}}": _literal(_strategy_description(selected)),
        "{{STRATEGY_LIMITS}}": _literal(_strategy_limits()),
        "{{PROFILE_HOOKS}}": profile_hooks,
        "{{PANEL_HOOKS}}": _panel_hooks(profile),
        "{{MARKET_HOOKS}}": _market_hooks(selected, profile),
        "{{TIMING_HOOKS}}": _timing_hooks(selected),
        "{{ADVANCED_HOOKS}}": _advanced_hooks(selected),
        "{{RUNTIME_WIRING}}": _runtime_wiring(
            profile,
            advanced_wiring=advanced_wiring,
        ),
    }
    rendered = base
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    unresolved = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", rendered)), key=str.casefold)
    if unresolved:
        raise RuntimeError(f"Unresolved scaffold template tokens: {unresolved}")
    rendered = rendered.rstrip() + "\n"
    ast.parse(rendered, filename=f"{name}.py")
    return rendered


def _recipe_paths(capabilities: tuple[str, ...]) -> list[str]:
    recipes: set[str] = set()
    for capability_id in capabilities:
        record = capability_payload(capability_id)
        raw_recipes = record.get("recipes") or [record.get("recipe", "")]
        if isinstance(raw_recipes, str):
            raw_recipes = [raw_recipes]
        if isinstance(raw_recipes, (list, tuple, set)):
            recipes.update(
                str(recipe).strip()
                for recipe in raw_recipes
                if str(recipe).strip()
            )
    return sorted(recipes, key=str.casefold)


def scaffold_strategy(
    name: str,
    profile: str = "rule",
    capabilities: Iterable[str] | str | None = (),
    excluded_capabilities: Iterable[str] | str | None = (),
    output_path: str | Path | None = None,
    force: bool = False,
    runtime_id: str = STANDARD_RUNTIME_ID,
    allow_unavailable_runtime: bool = False,
) -> dict[str, Any]:
    """Generate one neutral strategy module and return its reproducible manifest."""
    strategy_name = _validate_strategy_name(name)
    normalized_profile = _profile_name(profile)
    resolved_capabilities = resolve_capabilities(
        normalized_profile,
        capabilities,
        excluded_capabilities,
    )
    normalized_runtime_id = str(standard_runtime_payload(runtime_id)["id"])
    unavailable = unavailable_runtime_capabilities(
        resolved_capabilities,
        runtime_id=normalized_runtime_id,
    )
    if not isinstance(allow_unavailable_runtime, bool):
        raise TypeError("allow_unavailable_runtime must be a bool")
    if unavailable and not allow_unavailable_runtime:
        raise ValueError(
            f"runtime {normalized_runtime_id!r} cannot execute selected capabilities: {unavailable}; "
            "set allow_unavailable_runtime=True only for schema or programmatic-runtime work"
        )
    target = _resolve_output_path(strategy_name, output_path)
    source = _render_strategy(strategy_name, normalized_profile, resolved_capabilities)

    if not isinstance(force, bool):
        raise TypeError("force must be a bool")
    existed = target.exists()
    if existed and not force:
        raise FileExistsError(f"Strategy scaffold already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if force else "x"
    with target.open(mode, encoding="utf-8", newline="\n") as handle:
        handle.write(source)

    module_name = f"strategy_library.generated.{strategy_name}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": SCAFFOLD_SCHEMA_VERSION,
        "registry_version": REGISTRY_VERSION,
        "name": strategy_name,
        "profile": normalized_profile,
        "runtime_id": normalized_runtime_id,
        "runtime_unavailable_capabilities": {
            key: list(value)
            for key, value in unavailable.items()
        },
        "module": module_name,
        "capabilities": list(resolved_capabilities),
        "excluded_capabilities": list(
            sorted(
                {
                    str(item).strip().casefold()
                    for item in (
                        excluded_capabilities.split(",")
                        if isinstance(excluded_capabilities, str)
                        else excluded_capabilities or ()
                    )
                    if str(item).strip()
                },
                key=str.casefold,
            )
        ),
        "recipes": _recipe_paths(resolved_capabilities),
        "placeholders": ["__REQUIRED__", "NotImplementedError"],
        "generated_file": target.name,
        "sha256": digest,
    }
    return {
        "schema_version": SCAFFOLD_SCHEMA_VERSION,
        "status": "overwritten" if existed else "created",
        "created": not existed,
        "overwritten": existed,
        "path": str(target),
        "module": module_name,
        "manifest": manifest,
    }


__all__ = ["GENERATED_STRATEGY_DIR", "SCAFFOLD_SCHEMA_VERSION", "scaffold_strategy"]
