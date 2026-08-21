"""Optional behavioral probes for loaded daily strategies."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd

from core.panel_native import (
    ColumnStore,
    PanelView,
    PricePanel,
)
from strategies.market_features import (
    apply_cross_section_transforms,
    apply_market_features,
)
from core.invariants import assert_price_frame_preserved, capture_price_frame_invariants
from strategies.point_in_time import StrategyDataInputs, apply_declared_data_inputs

if TYPE_CHECKING:
    from strategies.descriptor import LoadedStrategyDescriptor


BEHAVIORAL_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class BehavioralProbeInputs:
    single_stock_frame: pd.DataFrame
    panel_frame: pd.DataFrame | None = None
    prefix_rows: int | None = None
    check_order_invariance: bool = True
    check_prefix_causality: bool = True
    panel_single_equivalence_columns: tuple[str, ...] = ()
    data_inputs: StrategyDataInputs | Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class BehavioralCheck:
    code: str
    status: str
    message: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "status": self.status, "message": self.message}


@dataclass(frozen=True, slots=True)
class BehavioralReport:
    checks: tuple[BehavioralCheck, ...]

    @property
    def ok(self) -> bool:
        return not any(check.status == "fail" for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BEHAVIORAL_SCHEMA_VERSION,
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
            "summary": {
                "pass": sum(check.status == "pass" for check in self.checks),
                "fail": sum(check.status == "fail" for check in self.checks),
                "skipped": sum(check.status == "skipped" for check in self.checks),
            },
        }


@dataclass(slots=True)
class _PanelProbeState:
    panel: PricePanel
    store: ColumnStore
    features: pd.DataFrame


def _reset(strategy: object) -> None:
    reset = getattr(strategy, "reset_runtime_state", None)
    if callable(reset):
        reset()


def _canonical(frame: pd.DataFrame) -> pd.DataFrame:
    keys = [name for name in ("stock_code", "trade_date") if name in frame.columns]
    if not keys:
        return frame.reset_index(drop=True)
    return frame.sort_values(keys, kind="mergesort").reset_index(drop=True)


def _normalized_panel(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy(deep=True)
    out["stock_code"] = out["stock_code"].astype(str).str.strip().str.upper()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise")
    return _canonical(out)


def _project_panel_state(
    panel: PricePanel,
    store: ColumnStore,
    *,
    buy_raw: object | None = None,
    sell_raw: object | None = None,
) -> pd.DataFrame:
    columns: dict[str, object] = {
        "trade_date": panel.index.trade_dates,
        "stock_code": panel.stock_code_by_row,
    }
    columns.update(store.columns)
    if buy_raw is not None:
        columns["buy_raw"] = buy_raw
    if sell_raw is not None:
        columns["sell_raw"] = sell_raw
    return pd.DataFrame(columns)


def _assert_equal(left: pd.DataFrame, right: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(
        left,
        right,
        check_dtype=True,
        check_exact=True,
        check_like=False,
    )


def _prefix_row_count(length: int, requested: int | None) -> int:
    rows = requested if requested is not None else max(1, int(length * 0.7))
    return min(max(1, int(rows)), length - 1)


def _panel_prefix(frame: pd.DataFrame, requested_rows: int | None) -> pd.DataFrame:
    ordered = frame.sort_values(["trade_date", "stock_code"], kind="mergesort")
    cutoff_position = _prefix_row_count(len(ordered), requested_rows) - 1
    cutoff_date = pd.Timestamp(ordered.iloc[cutoff_position]["trade_date"])
    prefix = frame.loc[pd.to_datetime(frame["trade_date"], errors="raise").le(cutoff_date)]
    if len(prefix) >= len(frame):
        earlier_dates = sorted(
            pd.to_datetime(frame["trade_date"], errors="raise").drop_duplicates().tolist()
        )
        if len(earlier_dates) < 2:
            return frame.iloc[0:0].copy()
        prefix = frame.loc[
            pd.to_datetime(frame["trade_date"], errors="raise").lt(earlier_dates[-1])
        ]
    return prefix.copy()


def _record(
    checks: list[BehavioralCheck],
    code: str,
    fn: Callable[[], object],
) -> object | None:
    try:
        value = fn()
    except Exception as exc:
        checks.append(BehavioralCheck(code, "fail", f"{type(exc).__name__}: {exc}"))
        return None
    checks.append(BehavioralCheck(code, "pass"))
    return value


def probe_strategy_behavior(
    descriptor: LoadedStrategyDescriptor,
    inputs: BehavioralProbeInputs,
) -> BehavioralReport:
    strategy = descriptor.strategy
    checks: list[BehavioralCheck] = []
    mode = str(
        getattr(strategy, "STRATEGY_META", {}).get("execution_mode", "panel")
        or "panel"
    ).strip().casefold()
    source = inputs.single_stock_frame.copy(deep=True)

    if mode == "fixed_rebalance":
        return BehavioralReport((BehavioralCheck("fixed_rebalance_contract", "pass"),))

    if source.empty:
        return BehavioralReport((BehavioralCheck("single_stock_input", "fail", "input is empty"),))

    if mode == "single":
        signal_builder = getattr(strategy, "build_single_signals", None)
        if not callable(signal_builder):
            return BehavioralReport(
                (BehavioralCheck("build_single_signals", "fail", "hook is not callable"),)
            )

        def run_single(frame: pd.DataFrame) -> pd.DataFrame:
            _reset(strategy)
            expected = capture_price_frame_invariants(_canonical(frame))
            result = signal_builder(frame.copy(deep=True))
            assert_price_frame_preserved(
                expected,
                _canonical(result),
                source="build_single_signals",
                error_cls=ValueError,
            )
            return result

        first = _record(checks, "single_signal_contract", lambda: run_single(source))
        if isinstance(first, pd.DataFrame):
            _record(checks, "single_signal_repeatability", lambda: _assert_equal(first, run_single(source)))
            if inputs.check_order_invariance:
                shuffled = source.sample(frac=1.0, random_state=20260727).reset_index(drop=True)
                _record(
                    checks,
                    "single_signal_order_invariance",
                    lambda: _assert_equal(_canonical(first), _canonical(run_single(shuffled))),
                )
            else:
                checks.append(BehavioralCheck("single_signal_order_invariance", "skipped"))
            if inputs.check_prefix_causality and len(source) > 1:
                def single_prefix_causality() -> None:
                    ordered = source.sort_values("trade_date", kind="mergesort").reset_index(drop=True)
                    rows = _prefix_row_count(len(ordered), inputs.prefix_rows)
                    prefix_result = _canonical(run_single(ordered.iloc[:rows].copy()))
                    full_result = _canonical(run_single(ordered))
                    prefix_dates = set(prefix_result["trade_date"])
                    full_prefix = full_result.loc[
                        full_result["trade_date"].isin(prefix_dates)
                    ].reset_index(drop=True)
                    common = [name for name in prefix_result.columns if name in full_prefix.columns]
                    _assert_equal(prefix_result.loc[:, common], full_prefix.loc[:, common])

                _record(checks, "single_signal_prefix_causality", single_prefix_causality)
            else:
                checks.append(BehavioralCheck("single_signal_prefix_causality", "skipped"))
        checks.append(BehavioralCheck("panel_contract", "skipped"))
        return BehavioralReport(tuple(checks))

    if mode != "panel":
        return BehavioralReport((BehavioralCheck("execution_mode", "fail", f"unsupported mode: {mode}"),))

    panel = inputs.panel_frame
    if panel is None or panel.empty:
        return BehavioralReport((BehavioralCheck("panel_input", "fail", "panel input is empty"),))

    prepared = _record(
        checks,
        "declared_data_inputs_contract",
        lambda: apply_declared_data_inputs(
            panel.copy(deep=True), descriptor.advanced, inputs.data_inputs
        ),
    )
    prepared_panel = prepared if isinstance(prepared, pd.DataFrame) else None
    feature_hook = getattr(strategy, "compute_features", None)
    signal_hook = getattr(strategy, "compute_signals", None)
    if prepared_panel is None or not callable(feature_hook):
        checks.append(BehavioralCheck("panel_features_contract", "fail", "hook is not callable"))
        return BehavioralReport(tuple(checks))
    if not callable(signal_hook):
        checks.append(BehavioralCheck("panel_signals_contract", "fail", "hook is not callable"))
        return BehavioralReport(tuple(checks))

    def run_features(frame: pd.DataFrame) -> _PanelProbeState:
        _reset(strategy)
        price_panel = PricePanel.from_frame(_normalized_panel(frame))
        store = ColumnStore(price_panel)
        store.register_features(feature_hook(PanelView(price_panel, store)))
        if bool(getattr(descriptor.spec, "use_market_features", False)):
            apply_market_features(price_panel, store, spec=descriptor.spec)
        if descriptor.advanced.cross_section_transforms:
            apply_cross_section_transforms(
                price_panel,
                store,
                descriptor.advanced.cross_section_transforms,
            )
        return _PanelProbeState(
            panel=price_panel,
            store=store,
            features=_project_panel_state(price_panel, store),
        )

    feature_result = _record(checks, "panel_features_contract", lambda: run_features(prepared_panel))
    if not isinstance(feature_result, _PanelProbeState):
        return BehavioralReport(tuple(checks))
    checks.append(
        BehavioralCheck(
            "cross_section_contract",
            "pass" if descriptor.advanced.cross_section_transforms else "skipped",
        )
    )

    def finish_signals(state: _PanelProbeState) -> pd.DataFrame:
        buy_raw, sell_raw = state.store.register_signals(
            signal_hook(PanelView(state.panel, state.store))
        )
        return _project_panel_state(
            state.panel,
            state.store,
            buy_raw=buy_raw,
            sell_raw=sell_raw,
        )

    def run_signals(frame: pd.DataFrame) -> pd.DataFrame:
        return finish_signals(run_features(frame))

    result = _record(checks, "panel_signals_contract", lambda: finish_signals(feature_result))
    if isinstance(result, pd.DataFrame):
        _record(checks, "panel_signals_repeatability", lambda: _assert_equal(result, run_signals(prepared_panel)))
        if inputs.check_order_invariance:
            shuffled = prepared_panel.sample(frac=1.0, random_state=20260727).reset_index(drop=True)
            _record(
                checks,
                "panel_signal_order_invariance",
                lambda: _assert_equal(_canonical(result), _canonical(run_signals(shuffled))),
            )
        else:
            checks.append(BehavioralCheck("panel_signal_order_invariance", "skipped"))
        prefix = (
            _panel_prefix(prepared_panel, inputs.prefix_rows)
            if inputs.check_prefix_causality and len(prepared_panel) > 1
            else prepared_panel.iloc[0:0].copy()
        )
        if not prefix.empty:
            def panel_prefix_causality() -> None:
                prefix_result = _canonical(run_signals(prefix))
                full_result = _canonical(run_signals(prepared_panel))
                prefix_keys = prefix_result.loc[:, ["stock_code", "trade_date"]]
                full_prefix = full_result.merge(
                    prefix_keys,
                    how="inner",
                    on=["stock_code", "trade_date"],
                    sort=False,
                    validate="one_to_one",
                )
                _assert_equal(prefix_result, _canonical(full_prefix))

            _record(checks, "panel_signal_prefix_causality", panel_prefix_causality)
        else:
            checks.append(BehavioralCheck("panel_signal_prefix_causality", "skipped"))
    checks.append(BehavioralCheck("single_signal_contract", "skipped"))
    return BehavioralReport(tuple(checks))


__all__ = [
    "BEHAVIORAL_SCHEMA_VERSION",
    "BehavioralCheck",
    "BehavioralProbeInputs",
    "BehavioralReport",
    "probe_strategy_behavior",
]
