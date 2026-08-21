from __future__ import annotations

import pytest

from web.library_evaluation_identity import _build_evaluation_identity


pytestmark = pytest.mark.unit


def _cache_config(*, date_source: str = "config") -> dict[str, object]:
    return {
        "schema": 2,
        "source": {"sha256": "a" * 64, "size": 10},
        "configured_window": {"start_time": "20240101", "end_time": "20241231"},
        "date_source": date_source,
    }


def _identity(
    *,
    name: str,
    module: str,
    config: dict[str, object],
    overrides=None,
    effective_window: dict[str, str] | None = None,
):
    return _build_evaluation_identity(
        name=name,
        module=module,
        cache_config=_cache_config(
            date_source="override" if overrides is not None and effective_window else "config"
        ),
        resolved_config=config,
        effective_window=effective_window or {"start_time": "20240101", "end_time": "20241231"},
        date_overrides=overrides,
        run_result=None,
        meta={},
    )


def test_evaluation_context_is_strategy_agnostic_but_full_id_is_not() -> None:
    config = {
        "backtest": {"offset_days": 0},
        "run": {"exec_shift_t1": True},
        "transaction_cost": {"enable": True, "commission_rate": 0.0003},
    }
    first = _identity(name="alpha", module="alpha.py", config=config)
    second = _identity(name="beta", module="beta.py", config=config)

    assert first["context_id"] == second["context_id"]
    assert first["id"] != second["id"]
    assert first["run_kind"] == "default"
    assert first["default_eligible"] is True


def test_resolved_cost_config_and_date_override_change_identity() -> None:
    base = {
        "transaction_cost": {
            "enable": True,
            "commission_rate": 0.0003,
            "min_commission": 5.0,
            "stamp_duty_rate": 0.001,
            "slippage_bps": 2.0,
        },
        "portfolio": {"engine": "ledger"},
    }
    changed_cost = {
        **base,
        "transaction_cost": {
            **base["transaction_cost"],
            "slippage_bps": 3.0,
        },
    }
    default = _identity(name="alpha", module="alpha.py", config=base)
    cost_variant = _identity(name="alpha", module="alpha.py", config=changed_cost)
    ad_hoc = _identity(
        name="alpha",
        module="alpha.py",
        config=base,
        overrides={"start_time": "20240201"},
        effective_window={"start_time": "20240201", "end_time": "20241231"},
    )
    equivalent_override = _build_evaluation_identity(
        name="alpha",
        module="alpha.py",
        cache_config=_cache_config(date_source="config"),
        resolved_config=base,
        effective_window={"start_time": "20240101", "end_time": "20241231"},
        date_overrides={"start_time": "20240101", "end_time": "20241231"},
        run_result=None,
        meta={},
    )

    assert default["context_id"] != cost_variant["context_id"]
    assert default["id"] != cost_variant["id"]
    assert ad_hoc["run_kind"] == "ad_hoc"
    assert ad_hoc["default_eligible"] is False
    assert equivalent_override["run_kind"] == "default"
    assert equivalent_override["default_eligible"] is True


def test_identity_snapshot_masks_credentials() -> None:
    identity = _identity(
        name="alpha",
        module="alpha.py",
        config={"mysql": {"password": "do-not-persist"}, "tushare": {"token": "secret"}},
    )

    serialized = str(identity["resolved_config"])
    assert "do-not-persist" not in serialized
    assert "<set>" in serialized
