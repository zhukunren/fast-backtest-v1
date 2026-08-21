from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests_support.library_owners import library
from backtest.artifacts import ArtifactGenerationError
from tests_support.run_result_factory import curve_result, ledger_execution, vectorized_execution
from tests_support.web_cache import publish_web_cache_fixture
from web.library_analysis import _build_period_stats, _record_date_key
from web.exporter import build_daily_holdings_payload, build_net_value_payload
from web.export_common import _clean_float, _clean_int
from web.holdings import broker
from web.tools import rebuild_daily_holdings_cache as holdings_cache_tool
from web.tools import clean_invalid_library_cache as clean_cache_tool


def _write_current_strategy_fixture(
    strategy_dir: Path,
    *,
    start_time: str = "20240101",
    end_time: str = "20241231",
) -> Path:
    """Create the source/config pair required by a current cache generation."""
    strategy_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "strategy.py").write_text(
        "STRATEGY_META = {}\n",
        encoding="utf-8",
    )
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n"
        f"[backtest]\nstart_time = {start_time}\nend_time = {end_time}\n",
        encoding="utf-8",
    )
    return strategy_dir / "cache"


def _snapshot_progress() -> dict:
    return library.get_progress()


def test_sort_library_entries_treats_zero_return_as_real_value():
    entries = [
        {"name": "zero", "status": "completed", "cum_return": 0.0, "family": "builtin", "evaluation_context_id": "context"},
        {"name": "neg", "status": "completed", "cum_return": -0.1, "family": "builtin", "evaluation_context_id": "context"},
        {"name": "pos", "status": "completed", "cum_return": 0.2, "family": "builtin", "evaluation_context_id": "context"},
    ]

    sorted_entries = library._sort_library_entries(entries)

    assert [item["name"] for item in sorted_entries] == ["pos", "zero", "neg"]
    assert [item["rank"] for item in sorted_entries] == [1, 2, 3]


def test_trade_segment_date_normalization_prefers_exit_date_contract():
    assert _record_date_key({"买入日": "20240102", "卖出日": "20240105"}) == "20240105"


def test_build_daily_holdings_tolerates_missing_optional_columns():
    result = curve_result(
        execution=vectorized_execution(
            processed_frames={
                "df_2_1_with_returns": pd.DataFrame({
                    "trade_date": ["2024-01-03", "2024-01-02"],
                    "stock_code": ["300002.SZ", "300001.SZ"],
                    "direction": ["", "buy"],
                    "net_value": [1.1, 1.0],
                })
            }
        )
    )

    payload = build_daily_holdings_payload(result)

    assert payload is not None
    assert payload["dates"] == ["2024-01-02", "2024-01-03"]
    assert payload["holdings_by_date"]["2024-01-02"] == [{
        "slot": "2-1",
        "code": "300001.SZ",
        "name": "",
        "dir": "buy",
        "close": None,
        "shares": None,
        "original_shares": None,
        "shares_scaled": False,
        "holding_value": None,
        "cost_price": None,
        "cost_value": None,
        "unrealized_pnl": None,
        "unrealized_pnl_pct": None,
        "nv": 1.0,
        "utilization_rate": None,
        "effective_position_ratio": None,
    }]
    assert payload["summary_by_date"]["2024-01-02"] == {
        "net_value": 1.0,
        "asset": None,
        "cash": None,
        "position_value": None,
        "cum_pnl": None,
        "daily_pnl": None,
        "realized_pnl": None,
        "unrealized_pnl": None,
        "daily_return": None,
        "holding_count": 1,
    }


def test_build_daily_holdings_payload_keeps_slot_like_summary_asset_consistent_with_net_value():
    slot_a_nv = 1.1842735149480887
    slot_b_nv = 1.1530630616988002
    portfolio_nv = (slot_a_nv + slot_b_nv) / 2.0
    raw_asset_sum = round(302991.0635828001 + 288265.76542470005, 2)
    expected_asset = round(500000.0 * portfolio_nv, 2)
    expected_position_value = 272916.0
    expected_cash = round(expected_asset - expected_position_value, 2)

    slot_a = pd.DataFrame({
        "trade_date": ["2024-01-02", "2024-01-03"],
        "stock_code": ["", ""],
        "close": [0.0, 0.0],
        "shares": [0, 0],
        "equity": [250000.0, 302991.0635828001],
        "cash": [250000.0, 302991.0635828001],
        "position_value_abs": [0.0, 0.0],
        "net_value": [1.0, slot_a_nv],
        "utilization_rate": [0.0, 0.0],
        "effective_position_ratio": [0.0, 0.0],
    })
    slot_b = pd.DataFrame({
        "trade_date": ["2024-01-02", "2024-01-03"],
        "stock_code": ["", "301118.SZ"],
        "close": [0.0, 20.52],
        "shares": [0, 13300],
        "equity": [250000.0, 288265.76542470005],
        "cash": [250000.0, 15349.765424700046],
        "position_value_abs": [0.0, expected_position_value],
        "net_value": [1.0, slot_b_nv],
        "utilization_rate": [0.0, 0.9467513410685956],
        "effective_position_ratio": [0.0, 0.9467513410685956],
    })
    merged_df = pd.DataFrame({
            "trade_date": ["2024-01-02", "2024-01-03"],
            "sum_net_value": [1.0, portfolio_nv],
            "daily_return": [0.0, portfolio_nv - 1.0],
        })
    result = curve_result(
        equity_curve=merged_df,
        execution=vectorized_execution(
            equity_curve=merged_df,
            processed_frames={
                "df_1_1_with_returns": slot_a,
                "df_1_2_with_returns": slot_b,
            },
        ),
    )

    payload = build_daily_holdings_payload(result)

    assert payload is not None
    summary = payload["summary_by_date"]["2024-01-03"]
    assert summary["net_value"] == round(portfolio_nv, 4)
    assert summary["asset"] == expected_asset
    assert summary["position_value"] == expected_position_value
    assert summary["cash"] == expected_cash
    assert round(summary["cash"] + summary["position_value"], 2) == summary["asset"]
    assert summary["asset"] != raw_asset_sum


def test_build_daily_holdings_payload_ignores_slot_like_empty_placeholder_rows_and_uses_initial_asset_hint():
    slot_a = pd.DataFrame({
        "trade_date": ["2024-01-02", "2024-01-03", "2024-01-06", "2024-01-07"],
        "stock_code": [None, None, "300001.SZ", None],
        "close": [None, None, 11.0, None],
        "position": [0, 0, 1, 0],
        "net_value": [1.0, 1.0, 1.1, 1.1],
        "utilization_rate": [0.0, 0.0, 1.0, 1.0],
        "effective_position_ratio": [0.0, 0.0, 1.0, 1.0],
        "buy_price": [None, None, 10.0, None],
        "shares": [None, None, 50000, None],
    })
    slot_b = pd.DataFrame({
        "trade_date": ["2024-01-02", "2024-01-03", "2024-01-06", "2024-01-07"],
        "stock_code": [None, None, None, None],
        "close": [None, None, None, None],
        "position": [0, 0, 0, 0],
        "net_value": [1.0, 1.0, 1.0, 1.0],
        "utilization_rate": [0.0, 0.0, 0.0, 0.0],
        "effective_position_ratio": [0.0, 0.0, 0.0, 0.0],
        "buy_price": [None, None, None, None],
        "shares": [None, None, None, None],
    })
    fills_df = pd.DataFrame({
        "trade_date": ["2024-01-06", "2024-01-07"],
        "slot_id": ["df_1_1", "df_1_1"],
        "stock_code": ["300001.SZ", "300001.SZ"],
        "side": ["buy", "sell"],
        "qty": [50000, 50000],
        "price": [10.0, 11.0],
        "cash_delta": [-500000.0, 550000.0],
        "total_cost": [0.0, 0.0],
    })
    merged_df = pd.DataFrame({
            "trade_date": ["2024-01-02", "2024-01-03", "2024-01-06", "2024-01-07"],
            "sum_net_value": [1.0, 1.0, 1.05, 1.05],
            "daily_return": [0.0, 0.0, 0.05, 0.0],
        })
    result = curve_result(
        engine_id="slot_broker",
        equity_curve=merged_df,
        execution=ledger_execution(
            equity_curve=merged_df,
            processed_frames={
                "df_1_1_with_returns": slot_a,
                "df_1_2_with_returns": slot_b,
            },
            fills_df=fills_df,
        ),
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        payload = build_daily_holdings_payload(result, initial_asset_hint=1000000.0)

    assert payload is not None
    assert "2024-01-02" not in payload["holdings_by_date"]
    assert "2024-01-03" not in payload["holdings_by_date"]
    assert payload["holdings_by_date"]["2024-01-06"] == [{
        "slot": "1-1",
        "code": "300001.SZ",
        "name": "",
        "dir": "",
        "close": 11.0,
        "shares": 50000,
        "original_shares": 50000,
        "shares_scaled": False,
        "holding_value": 550000.0,
        "cost_price": 10.0,
        "cost_value": 500000.0,
        "unrealized_pnl": 50000.0,
        "unrealized_pnl_pct": 0.1,
        "nv": 1.1,
        "utilization_rate": 1.0,
        "effective_position_ratio": 1.0,
    }]
    summary_open = payload["summary_by_date"]["2024-01-06"]
    assert summary_open["asset"] == 1050000.0
    assert summary_open["position_value"] == 550000.0
    assert summary_open["cash"] == 500000.0
    assert summary_open["daily_pnl"] == 50000.0
    assert summary_open["unrealized_pnl"] == 50000.0
    assert summary_open["holding_count"] == 1
    summary_exit = payload["summary_by_date"]["2024-01-07"]
    assert summary_exit["asset"] == 1050000.0
    assert summary_exit["position_value"] == 0.0
    assert summary_exit["cash"] == 1050000.0
    assert summary_exit["daily_pnl"] == 0.0
    assert summary_exit["unrealized_pnl"] is None
    assert summary_exit["realized_pnl"] == 50000.0
    assert summary_exit["holding_count"] == 0


def test_build_daily_holdings_payload_broker_derives_per_position_effective_ratio():
    merged_df = pd.DataFrame({
        "trade_date": pd.to_datetime(["2024-01-03"]),
        "sum_net_value": [1.0],
        "daily_return": [0.0],
        "equity": [10000.0],
        "equity_raw": [10000.0],
        "cash": [5000.0],
        "position_value": [5000.0],
        "utilization_rate": [0.5],
        "utilization_rate_raw": [0.8],
        "effective_position_ratio": [0.5],
    })
    snapshots = (
        {
            "trade_date": "2024-01-03",
            "positions": [
                {"stock_code": "AAA.SZ", "qty": 100, "close": 30.0},
                {"stock_code": "BBB.SZ", "qty": 50, "close": 100.0},
            ],
        },
    )
    result = curve_result(
        engine_id="broker",
        equity_curve=merged_df,
        execution=ledger_execution(
            equity_curve=merged_df,
            daily_position_snapshots=snapshots,
        ),
    )

    payload = build_daily_holdings_payload(result)

    assert payload is not None
    rows = sorted(payload["holdings_by_date"]["2024-01-03"], key=lambda item: item["code"])
    assert rows[0]["code"] == "AAA.SZ"
    assert rows[0]["holding_value"] == 1875.0
    assert rows[0]["effective_position_ratio"] == 0.1875
    assert rows[0]["utilization_rate"] == 0.3
    assert rows[0]["shares_scaled"] is True
    assert rows[1]["code"] == "BBB.SZ"
    assert rows[1]["holding_value"] == 3125.0
    assert rows[1]["effective_position_ratio"] == 0.3125
    assert rows[1]["utilization_rate"] == 0.5
    assert rows[1]["shares_scaled"] is True
    assert round(sum(float(row["effective_position_ratio"]) for row in rows), 4) == 0.5


def test_build_net_value_payload_sorts_dates_and_keeps_latest_duplicate():
    result = curve_result(
        equity_curve=pd.DataFrame({
            "trade_date": ["2024-01-03", "2024-01-02", "2024-01-03", "2024-01-04"],
            "sum_net_value": [1.3, 1.0, 1.2, 1.1],
            "utilization_rate": [0.3, 0.0, 0.2, 0.1],
        }),
    )

    payload = build_net_value_payload(result)

    assert payload is not None
    assert payload["dates"] == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert payload["net_value"] == [1.0, 1.2, 1.1]
    assert payload["drawdown"] == [0.0, 0.0, -0.083333]
    assert payload["utilization"] == [0.0, 0.2, 0.1]


def test_build_net_value_payload_counts_initial_capital_as_drawdown_peak():
    result = curve_result(
        equity_curve=pd.DataFrame({
            "trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "sum_net_value": [0.99, 1.01, 0.98],
        }),
    )

    payload = build_net_value_payload(result)

    assert payload is not None
    assert payload["net_value"] == [0.99, 1.01, 0.98]
    assert payload["drawdown"] == [-0.01, 0.0, -0.029703]


def test_build_net_value_payload_rejects_curve_without_required_net_value_column():
    result = curve_result(
        equity_curve=pd.DataFrame({
            "trade_date": ["2024-01-02", "2024-01-03"],
            "utilization_rate": [0.0, 0.5],
        }),
    )

    assert build_net_value_payload(result) is None


def test_broker_fill_snapshots_ignore_fills_without_stock_code():
    result = curve_result(
        equity_curve=pd.DataFrame({
            "trade_date": ["2024-01-02"],
            "sum_net_value": [1.0],
        }),
        execution=ledger_execution(
            fills_df=pd.DataFrame({
                "trade_date": ["2024-01-02"],
                "side": ["buy"],
                "qty": [100],
            }),
        ),
    )

    states, realized = broker.build_fill_accounting_snapshots(
        result,
        ordered_dates=["2024-01-02"],
    )

    assert states == {}
    assert realized == {}


def test_export_numeric_cleaners_reject_non_finite_values():
    assert _clean_float(float("inf")) is None
    assert _clean_int(float("-inf")) is None


def test_compute_derived_metrics_derives_calmar_without_drawdown(
    tmp_path,
    monkeypatch,
):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = _write_current_strategy_fixture(strategy_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "net_value.json").write_text(
        json.dumps(
            {
                "dates": ["2024-01-02", "2024-01-03", "2024-01-04"],
                "net_value": [1.0, 1.2, 1.1],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    publish_web_cache_fixture(library_root / "demo_strategy", flat_cache_dir=cache_dir)
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "_read_benchmark_daily", lambda *args, **kwargs: None)

    metrics = library.compute_derived_metrics("demo_strategy")

    daily_returns = [0.0, 0.2, 1.1 / 1.2 - 1.0]
    mean_r = sum(daily_returns) / len(daily_returns)
    variance = sum((value - mean_r) ** 2 for value in daily_returns) / len(daily_returns)
    expected_sharpe = round((mean_r / (variance ** 0.5)) * (250 ** 0.5), 4)
    expected_ann_raw = (1.1 ** (250 / 3)) - 1.0
    assert metrics is not None
    assert metrics["ann_return"] == round(expected_ann_raw, 6)
    assert metrics["sharpe"] == expected_sharpe
    assert metrics["calmar"] == round(expected_ann_raw / 0.08333333333333337, 4)
    assert metrics["max_dd_days"] == 1


def test_compute_derived_metrics_includes_first_point_relative_to_initial_capital(
    tmp_path,
    monkeypatch,
):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = _write_current_strategy_fixture(strategy_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "net_value.json").write_text(
        json.dumps(
            {
                "dates": ["2024-01-02", "2024-01-03", "2024-01-04"],
                "net_value": [0.9901, 1.01, 0.98],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    publish_web_cache_fixture(library_root / "demo_strategy", flat_cache_dir=cache_dir)
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(
        library,
        "_build_benchmark_curves",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("derived metrics must not query benchmark data")
        ),
    )

    metrics = library.compute_derived_metrics("demo_strategy")

    assert metrics is not None
    assert metrics["cum_return"] == round(0.98 - 1.0, 6)
    assert metrics["max_drawdown"] == round(1.0 - 0.98 / 1.01, 6)
    assert metrics["max_dd_days"] == 1


def test_period_stats_use_previous_period_end_as_next_period_baseline():
    points = [
        {"date_key": "20241231", "date": "2024-12-31", "net_value": 1.2},
        {"date_key": "20250102", "date": "2025-01-02", "net_value": 1.1},
        {"date_key": "20250103", "date": "2025-01-03", "net_value": 1.3},
    ]

    yearly = _build_period_stats(points, period="year")
    monthly = _build_period_stats(points, period="month")

    assert [row["return"] for row in yearly] == [0.2, round(1.3 / 1.2 - 1.0, 6)]
    assert yearly[1]["positive_days"] == 1
    assert yearly[1]["negative_days"] == 1
    assert monthly[1]["return"] == round(1.3 / 1.2 - 1.0, 6)


def test_compute_derived_metrics_zero_volatility_has_explicit_sharpe(
    tmp_path,
    monkeypatch,
):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = _write_current_strategy_fixture(strategy_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "net_value.json").write_text(
        json.dumps(
            {
                "dates": ["2024-01-02", "2024-01-03", "2024-01-04"],
                "net_value": [1.0, 1.0, 1.0],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    publish_web_cache_fixture(library_root / "demo_strategy", flat_cache_dir=cache_dir)
    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "_read_benchmark_daily", lambda *args, **kwargs: None)

    metrics = library.compute_derived_metrics("demo_strategy")

    assert metrics is not None
    assert metrics["ann_return"] == 0.0
    assert metrics["sharpe"] == 0.0
    assert metrics["calmar"] is None


def test_rebuild_daily_holdings_cache_rewrites_payload_from_strategy_outputs(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    (strategy_dir / "cache").mkdir(parents=True, exist_ok=True)
    (strategy_dir / "return_date_with_returns").mkdir(parents=True, exist_ok=True)
    (strategy_dir / "net_value_unique_date").mkdir(parents=True, exist_ok=True)

    pd.DataFrame({
        "trade_date": ["2024-01-02", "2024-01-03"],
        "sum_net_value": [1.0, 1.1],
        "daily_return": [0.0, 0.1],
    }).to_csv(
        strategy_dir / "net_value_unique_date" / "merged_net_value_with_sum.csv",
        index=False,
        encoding="utf-8",
    )
    pd.DataFrame({
        "trade_date": ["2024-01-03"],
        "stock_code": ["300001.SZ"],
        "stock_name": ["示例股票"],
        "direction": ["buy"],
        "close": [10.0],
        "shares": [100],
        "position_value_abs": [1000.0],
        "equity": [1100.0],
        "net_value": [1.1],
        "utilization_rate": [0.9090909],
        "effective_position_ratio": [0.9090909],
    }).to_csv(
        strategy_dir / "return_date_with_returns" / "df_1_1_with_returns.csv",
        index=False,
        encoding="utf-8",
    )
    (strategy_dir / "cache" / "meta.json").write_text(
        json.dumps({"extra": {"status": "completed"}}),
        encoding="utf-8",
    )
    (strategy_dir / "cache" / "net_value.json").write_text(
        json.dumps({"dates": ["2024-01-02", "2024-01-03"], "net_value": [1.0, 1.1]}),
        encoding="utf-8",
    )
    publish_web_cache_fixture(strategy_dir, flat_cache_dir=strategy_dir / "cache")

    monkeypatch.setattr(holdings_cache_tool, "LIBRARY_ROOT", library_root)

    result = holdings_cache_tool.rebuild_strategy_daily_holdings("demo_strategy")

    payload = json.loads(result.output_path.read_text(encoding="utf-8"))
    summary = payload["summary_by_date"]["2024-01-03"]
    assert result.date_count == 2
    assert result.holding_row_count == 1
    assert payload["dates"] == ["2024-01-02", "2024-01-03"]
    assert summary["asset"] == round(summary["cash"] + summary["position_value"], 2)
    assert summary["holding_count"] == 1
    assert payload["holdings_by_date"]["2024-01-03"][0]["code"] == "300001.SZ"


def test_publish_web_cache_rejects_legacy_daily_holdings_shape(tmp_path):
    library_root = tmp_path / "strategy_library"
    cache_dir = library_root / "demo_strategy" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "daily_holdings.json").write_text(
        json.dumps(
            {
                "2024-01-02": [{"slot": "1-1", "code": "AAA.SZ", "dir": "buy", "close": 10.0, "nv": 1.0}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactGenerationError, match="requires list field: dates"):
        publish_web_cache_fixture(
            library_root / "demo_strategy",
            flat_cache_dir=cache_dir,
        )


def test_publish_web_cache_rejects_daily_holdings_without_dates(tmp_path):
    library_root = tmp_path / "strategy_library"
    cache_dir = library_root / "demo_strategy" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "daily_holdings.json").write_text(
        json.dumps(
            {
                "holdings_by_date": {
                    "2024-01-02": [{"slot": "1-1", "code": "AAA.SZ"}],
                },
                "summary_by_date": {
                    "2024-01-02": {"holding_count": 1},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactGenerationError, match="requires list field: dates"):
        publish_web_cache_fixture(
            library_root / "demo_strategy",
            flat_cache_dir=cache_dir,
        )


def test_get_library_net_value_adds_benchmark_and_excess_curves_with_sz_fallback(
    tmp_path,
    monkeypatch,
):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = _write_current_strategy_fixture(strategy_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "net_value.json").write_text(
        json.dumps({
            "dates": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "net_value": [1.0, 1.1, 1.21],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    publish_web_cache_fixture(library_root / "demo_strategy", flat_cache_dir=cache_dir)

    calls: list[str] = []

    def fake_read_benchmark_daily(symbol_code: str, trade_dates: list[str], *, start_date: str, end_date: str):
        calls.append(symbol_code)
        assert trade_dates == ["2024-01-02", "2024-01-03", "2024-01-04"]
        assert start_date == "20240102"
        assert end_date == "20240104"
        if symbol_code != "399006.SZ":
            raise FileNotFoundError(f"missing {symbol_code} table")
        return pd.DataFrame({
            "trade_date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "close": [100.0, 105.0, 110.0],
        })

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "_read_benchmark_daily", fake_read_benchmark_daily)

    payload = library.get_library_net_value("demo_strategy")

    assert payload is not None
    assert calls == ["399006.SZ"]
    assert payload["benchmark_code"] == "399006.SZ"
    assert payload["benchmark_source_code"] == "399006.SZ"
    assert payload["benchmark_label"] == "创业板指 399006.SZ"
    assert payload["benchmark_net_value"] == [1.0, 1.05, 1.1]
    assert payload["excess_net_value"] == [1.0, 1.047619, 1.1]


def test_get_library_net_value_can_skip_catalog_enrichment(
    tmp_path,
    monkeypatch,
):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = _write_current_strategy_fixture(strategy_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "net_value.json").write_text(
        json.dumps({
            "dates": ["2024-01-02", "2024-01-03"],
            "net_value": [1.0, 1.1],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    publish_web_cache_fixture(library_root / "demo_strategy", flat_cache_dir=cache_dir)

    def fail_if_benchmark_is_requested(*args, **kwargs):
        raise AssertionError("catalog net-value reads must not request benchmark curves")

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(library, "_build_benchmark_curves", fail_if_benchmark_is_requested)

    payload = library.get_library_net_value(
        "demo_strategy",
        include_benchmark=False,
    )

    assert payload == {
        "artifact_type": "net_value",
        "schema_version": 2,
        "dates": ["2024-01-02", "2024-01-03"],
        "net_value": [1.0, 1.1],
    }


@pytest.mark.parametrize(
    ("dates", "message"),
    [
        (["20240103", "2024-01-02", "2024-01-03"], "canonical YYYY-MM-DD"),
        (["2024-01-03", "2024-01-02", "2024-01-03"], "unique and strictly increasing"),
    ],
)
def test_web_cache_rejects_noncanonical_curve_order_and_duplicates(
    tmp_path,
    dates,
    message,
):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = _write_current_strategy_fixture(strategy_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "net_value.json").write_text(
        json.dumps(
            {
                "dates": dates,
                "net_value": [1.3, 1.0, 1.2],
                "drawdown": [-0.1, 0.0, -0.05],
                "portfolios": {"slot_a": [1.3, 1.0, 1.2]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactGenerationError, match=message):
        publish_web_cache_fixture(
            library_root / "demo_strategy",
            flat_cache_dir=cache_dir,
        )


def test_mismatched_strategy_cache_is_ignored_for_catalog_and_api(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = strategy_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n",
        encoding="utf-8",
    )
    (cache_dir / "meta.json").write_text(
        json.dumps(
            {
                "strategy_module": "strategy_library.builtin.old_strategy",
                "extra": {"status": "completed"},
                "result": {"cum_return": 9.99},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (cache_dir / "net_value.json").write_text(
        json.dumps({"dates": ["2024-01-02"], "net_value": [9.99]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (cache_dir / "trades.json").write_text(json.dumps({"records": [{"pnl": 1.0}]}), encoding="utf-8")
    (cache_dir / "daily_holdings.json").write_text(
        json.dumps(
            {
                "dates": ["2024-01-02"],
                "holdings_by_date": {"2024-01-02": []},
                "summary_by_date": {"2024-01-02": {"holding_count": 0}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    publish_web_cache_fixture(strategy_dir, flat_cache_dir=cache_dir)

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)

    catalog = library.get_library_catalog()
    entry = catalog["items"][0]
    assert entry["module"] == "strategy.py"
    assert entry["family"] == "library"
    assert entry["has_cache"] is False
    assert entry["status"] == "stale"
    assert entry["evaluation_comparable"] is None
    assert entry["cum_return"] == 9.99
    assert library.get_library_detail("demo_strategy") is None
    assert library.get_library_net_value("demo_strategy") is None
    assert library.get_library_trades("demo_strategy") is None
    assert library.get_library_daily_holdings("demo_strategy") is None


def test_cache_with_mismatched_backtest_window_is_ignored(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = strategy_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "config.ini").write_text(
        "\n".join([
            "[strategy]",
            "module = strategy.py",
            "",
            "[backtest]",
            "start_time = 20240101",
            "end_time = 20241231",
        ]),
        encoding="utf-8",
    )
    (cache_dir / "meta.json").write_text(
        json.dumps(
            {
                "strategy_module": "strategy.py",
                "backtest": {"start_time": "20210101", "end_time": "20231231"},
                "extra": {"status": "completed"},
                "result": {"cum_return": 1.23},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (cache_dir / "net_value.json").write_text(
        json.dumps({"dates": ["2021-01-04"], "net_value": [2.23]}, ensure_ascii=False),
        encoding="utf-8",
    )
    publish_web_cache_fixture(strategy_dir, flat_cache_dir=cache_dir, with_cache_config=False)

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)

    entry = library.get_library_catalog()["items"][0]
    assert entry["has_cache"] is False
    assert entry["cache_status"] == "invalid"
    assert entry["cache_reason"] == "缓存缺少 schema-v2 完整签名"
    assert entry["cache_actual_start_time"] == ""
    assert entry["cache_actual_end_time"] == ""
    assert entry["status"] == "stale"
    assert entry["cum_return"] == 1.23
    assert library.get_library_detail("demo_strategy") is None
    assert library.get_library_net_value("demo_strategy") is None


def test_legacy_cache_without_end_date_is_ignored_for_closed_config_window(
    tmp_path,
    monkeypatch,
):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = strategy_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n"
        "[backtest]\nstart_time = 20240101\nend_time = 20240131\n",
        encoding="utf-8",
    )
    (cache_dir / "meta.json").write_text(
        json.dumps(
            {
                "strategy_module": "strategy.py",
                "backtest": {"start_time": "20240101"},
                "extra": {"status": "completed"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (cache_dir / "net_value.json").write_text(
        json.dumps({"dates": ["2024-01-02"], "net_value": [1.1]}),
        encoding="utf-8",
    )
    publish_web_cache_fixture(strategy_dir, flat_cache_dir=cache_dir, with_cache_config=False)

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)

    entry = library.get_library_catalog()["items"][0]
    assert entry["cache_status"] == "invalid"
    assert entry["cache_reason"] == "缓存缺少 schema-v2 完整签名"
    assert entry["has_cache"] is False
    assert library.get_library_net_value("demo_strategy") is None


def test_payload_without_meta_is_ignored_when_config_declares_module(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = strategy_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n",
        encoding="utf-8",
    )
    (cache_dir / "net_value.json").write_text(
        json.dumps({"dates": ["2024-01-02"], "net_value": [1.23]}, ensure_ascii=False),
        encoding="utf-8",
    )

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)

    assert library.get_library_catalog()["items"][0]["has_cache"] is False
    assert library.get_library_catalog()["items"][0]["cache_status"] == "missing"
    assert library.get_library_net_value("demo_strategy") is None


def test_cache_invalidates_when_local_strategy_source_changes(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = strategy_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    source_path = strategy_dir / "strategy.py"
    source_path.write_text("STRATEGY_META = {'top_n': 1}\n", encoding="utf-8")
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n[backtest]\nstart_time = 20240101\nend_time = 20240131\n",
        encoding="utf-8",
    )
    strat_cfg = library._parse_strategy_config(strategy_dir / "config.ini")
    cache_config = library._build_cache_config_signature(
        strat_cfg,
        effective_window={"start_time": "20240101", "end_time": "20240131"},
        strategy_dir=strategy_dir,
        source_path=source_path,
    )
    (cache_dir / "meta.json").write_text(
        json.dumps(
            {
                "strategy_module": "strategy.py",
                "extra": {"status": "completed"},
                "strategy_meta": {"top_n": 1},
                "result": {"cum_return": 0.2},
                "cache_config": cache_config,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (cache_dir / "net_value.json").write_text(
        json.dumps({"dates": ["2024-01-02"], "net_value": [1.2]}, ensure_ascii=False),
        encoding="utf-8",
    )
    publish_web_cache_fixture(strategy_dir, flat_cache_dir=cache_dir)

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)

    entry = library.get_library_catalog()["items"][0]
    assert entry["cache_status"] == "valid"
    assert entry["version_status"] == "verified"
    assert entry["has_cache"] is True
    assert entry["description_status"] == "missing"
    assert entry["strategy_description"] is None
    assert entry["top_n"] is None

    source_path.write_text("STRATEGY_META = {'top_n': 99}\n", encoding="utf-8")

    entry = library.get_library_catalog()["items"][0]
    assert entry["cache_status"] == "invalid"
    assert entry["version_status"] == "changed"
    assert entry["cache_reason"] == "策略源码不一致"
    assert entry["has_cache"] is False
    assert entry["top_n"] is None
    assert library.get_library_net_value("demo_strategy") is None


def test_schema1_cache_without_source_fingerprint_is_invalid_for_local_strategy(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = strategy_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "strategy.py").write_text("STRATEGY_META = {'top_n': 3}\n", encoding="utf-8")
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n[backtest]\nstart_time = 20240101\nend_time = 20240131\n",
        encoding="utf-8",
    )
    strat_cfg = library._parse_strategy_config(strategy_dir / "config.ini")
    cache_config = library._build_cache_config_signature(
        strat_cfg,
        effective_window={"start_time": "20240101", "end_time": "20240131"},
        strategy_dir=strategy_dir,
        source_path=strategy_dir / "strategy.py",
    )
    cache_config["schema"] = 1
    cache_config.pop("source", None)
    (cache_dir / "meta.json").write_text(
        json.dumps(
            {
                "strategy_module": "strategy.py",
                "extra": {"status": "completed"},
                "strategy_meta": {"top_n": 1},
                "result": {"cum_return": 0.2},
                "cache_config": cache_config,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    publish_web_cache_fixture(strategy_dir, flat_cache_dir=cache_dir, with_cache_config=False)

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)

    entry = library.get_library_catalog()["items"][0]
    assert entry["cache_status"] == "invalid"
    assert entry["cache_reason"] == "缓存缺少 schema-v2 完整签名"
    assert entry["has_cache"] is False
    assert entry["description_status"] == "missing"
    assert entry["strategy_description"] is None
    assert entry["top_n"] is None


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("fixed_rebalance.target_weights", "510300.SH:0.6,510500.SH:0.4"),
        ("fixed_rebalance.symbol_type", "etf"),
        ("fixed_rebalance.rebalance_frequency", "monthly"),
        ("fixed_rebalance.rebalance_dates", "20240102,20240201"),
        ("fixed_rebalance.buy_price_col", "open"),
        ("fixed_rebalance.sell_price_col", "close"),
        ("fixed_rebalance.mark_price_col", "close"),
        ("fixed_rebalance.normalize_weights", "1"),
        ("mysql.etf_db", "etf_market"),
    ],
)
def test_cache_signature_tracks_every_fixed_rebalance_and_etf_source_key(key, value):
    base = library._cache_static_signature({"strategy.module": "strategy.py"})
    changed = library._cache_static_signature({"strategy.module": "strategy.py", key: value})

    assert changed != base


def test_clean_invalid_library_cache_dry_run_and_apply(tmp_path, monkeypatch):
    library_root = tmp_path / "strategy_library"
    strategy_dir = library_root / "demo_strategy"
    cache_dir = strategy_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (strategy_dir / "strategy.py").write_text("STRATEGY_META = {}\n", encoding="utf-8")
    (strategy_dir / "config.ini").write_text(
        "[strategy]\nmodule = strategy.py\n[backtest]\nstart_time = 20240101\n",
        encoding="utf-8",
    )
    (cache_dir / "meta.json").write_text(
        json.dumps(
            {
                "strategy_module": "strategy.py",
                "backtest": {"start_time": "20210101"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    publish_web_cache_fixture(strategy_dir, flat_cache_dir=cache_dir, with_cache_config=False)

    monkeypatch.setattr(library, "LIBRARY_ROOT", library_root)
    monkeypatch.setattr(clean_cache_tool, "LIBRARY_ROOT", library_root)

    dry_run = clean_cache_tool.clean_invalid_library_caches(dry_run=True)
    assert [item.name for item in dry_run] == ["demo_strategy"]
    assert dry_run[0].removed is False
    assert (strategy_dir / "web_cache").is_dir()

    applied = clean_cache_tool.clean_invalid_library_caches(dry_run=False)
    assert [item.name for item in applied] == ["demo_strategy"]
    assert applied[0].removed is True
    assert not (strategy_dir / "web_cache").exists()
