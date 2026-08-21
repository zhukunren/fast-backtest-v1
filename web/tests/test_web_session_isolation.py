from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from app.runtime_bootstrap_service import RuntimeBootstrapService
from backtest.reporting.single_stock_outputs import write_single_stock_outputs
from backtest.reporting.stage_outputs import write_execution_outputs
from config import (
    apply_config_overrides,
    get_config,
)
from tests_support.run_result_factory import vectorized_execution
from web import run_config


_OUTPUT_PATHS = (
    "signal_data",
    "stock_data",
    "trade_data",
    "return_date",
    "return_date_with_returns",
    "net_value_unique_date",
)


def _output_cfg(base_cfg, root: Path, *, clear_outputs: bool = False):
    overrides = run_config.build_library_run_overrides(
        {},
        output_root=root,
    )
    overrides["run.clear_outputs"] = clear_outputs
    return apply_config_overrides(base_cfg, overrides)


def _write_fixed_outputs(cfg) -> tuple[Path, ...]:
    frame = pd.DataFrame({"trade_date": ["2024-01-02"], "value": [1.0]})
    single_paths = write_single_stock_outputs(
        cfg=cfg,
        stock_code="000001.SZ",
        signal_payload=frame,
        price_df=frame,
        trades_df=frame,
    )
    execution_paths = write_execution_outputs(
        cfg=cfg,
        execution=vectorized_execution(
            equity_curve=pd.DataFrame(
                {"trade_date": ["2024-01-02"], "sum_net_value": [1.0]}
            ),
            trading_frames={"position_1": frame},
            processed_frames={"position_1": frame},
        ),
    )
    return single_paths + tuple(source.path for source in execution_paths)


def test_web_output_overrides_claim_all_session_directories(tmp_path: Path) -> None:
    expected = {
        f"paths.{name}": tmp_path / name
        for name in _OUTPUT_PATHS
    }
    batch_overrides = run_config.build_batch_run_overrides(
        "strategy_library.builtin.demo",
        output_root=tmp_path,
    )
    library_overrides = run_config.build_library_run_overrides(
        {},
        output_root=tmp_path,
    )

    assert run_config.build_output_path_overrides(tmp_path) == expected
    assert expected.items() <= batch_overrides.items()
    assert expected.items() <= library_overrides.items()
    assert "run.fast_pipeline" not in batch_overrides
    assert "run.fast_pipeline" not in library_overrides


def test_clear_outputs_only_removes_fixed_files_owned_by_one_session(tmp_path: Path) -> None:
    process_cfg = get_config(reload=True)
    base_cfg = _output_cfg(process_cfg, tmp_path / "base")
    alpha_cfg = _output_cfg(base_cfg, tmp_path / "alpha", clear_outputs=True)
    beta_cfg = _output_cfg(base_cfg, tmp_path / "beta", clear_outputs=True)

    written = {
        "base": _write_fixed_outputs(base_cfg),
        "alpha": _write_fixed_outputs(alpha_cfg),
        "beta": _write_fixed_outputs(beta_cfg),
    }
    keep_path = Path(alpha_cfg.paths.signal_data) / "keep.json"
    keep_path.write_text("{}", encoding="utf-8")

    alpha_roots = {Path(getattr(alpha_cfg.paths, name)) for name in _OUTPUT_PATHS}
    assert all(path.parent in alpha_roots for path in written["alpha"])
    assert {path.name for path in written["alpha"]} >= {
        "000001.SZ_signal.csv",
        "000001.SZ_data.csv",
        "000001.SZ_trades.csv",
        "position_1.csv",
        "position_1_with_returns.csv",
        "merged_net_value_with_sum.csv",
    }

    RuntimeBootstrapService().clear_outputs_if_needed(
        SimpleNamespace(
            cfg=alpha_cfg,
            raw_print=lambda *_args, **_kwargs: None,
        )
    )

    assert all(not path.exists() for path in written["alpha"])
    assert all(path.is_file() for path in written["base"])
    assert all(path.is_file() for path in written["beta"])
    assert keep_path.is_file()
