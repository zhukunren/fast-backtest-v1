"""
测试脚本 - 验证回测系统
"""
from __future__ import annotations

import sys
import tempfile
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from tests_support import build_price_frame, temporary_env, write_local_market_dataset

# 确保项目根路径在 sys.path（兼容直接运行 python tests/test_basic.py）
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def _prepare_sample_data(sample_dir: Path) -> None:
    trade_days = pd.bdate_range("2024-01-02", "2024-06-28")
    x = np.arange(len(trade_days), dtype=float)

    stock_code = "300001.SZ"
    close = 10.0 + 0.03 * x + 0.6 * np.sin(x / 4.0)
    open_ = close * (1.0 + 0.001 * np.cos(x / 3.5))
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    volume = 800_000 + (x.astype(int) % 15) * 10_000

    price_df = build_price_frame(
        trade_days,
        stock_code=stock_code,
        stock_name="BASIC_TEST",
        open_=open_.round(4),
        high=high.round(4),
        low=low.round(4),
        close=close.round(4),
        volume=volume.astype(int),
    )
    write_local_market_dataset(
        sample_dir,
        price_frames={stock_code: price_df},
        stock_rows=[{"stock_code": stock_code, "stock_name": "BASIC_TEST"}],
    )


@lru_cache(maxsize=1)
def _get_sample_dir() -> Path:
    sample_dir = Path(tempfile.gettempdir()) / "backtest_test_basic_sample"
    sample_dir.mkdir(parents=True, exist_ok=True)
    if not (
        (sample_dir / "stock_basic.csv").is_file()
        and (sample_dir / "prices" / "300001.SZ.csv").is_file()
    ):
        _prepare_sample_data(sample_dir)
    return sample_dir


@contextmanager
def _with_local_test_env():
    with temporary_env(
        {
            "LOCAL_DATA_DIR": str(_get_sample_dir()),
            "STRATEGY_MODULE": "strategy_library.builtin.offline_smoke",
            "OFFSET_DAYS": "0",
        }
    ):
        yield


def _get_local_test_cfg():
    from config import apply_config_overrides, get_config

    return apply_config_overrides(
        get_config(reload=True),
        {"mysql.daily_fields": ()},
    )


def test_imports() -> None:
    """测试模块导入"""
    print("1. Testing imports...")
    from config import get_config  # noqa: F401
    from strategies.spec import get_strategy_spec  # noqa: F401
    from backtest.panel_native import PricePanel, SelectionBatch  # noqa: F401
    from data import read_ohlcv_from_mysql, get_all_stocks, select_time  # noqa: F401
    from strategies.api import BaseStrategy, load_strategy  # noqa: F401
    from backtest.engine_batch import run_backtest_all_stocks  # noqa: F401
    from backtest.orchestration import run_backtest  # noqa: F401
    print("   All imports OK!")


def test_config() -> None:
    """测试配置加载"""
    print("2. Testing config...")
    from config import get_config

    cfg = get_config(reload=True)
    assert cfg.backtest.start_time, "start_time should not be empty"
    assert cfg.backtest.end_time, "end_time should not be empty"
    assert hasattr(cfg, "diagnostics"), "diagnostics config should exist"
    assert not hasattr(cfg, "ai"), "AI config was removed; Codex-authored artifacts are validated locally"
    print(f"   Config: {cfg.backtest.start_time} ~ {cfg.backtest.end_time}")
    print("   Config OK!")


def test_strategy_loading() -> None:
    """测试策略加载"""
    print("3. Testing strategy loading...")
    from config import get_config
    from strategies.api import load_strategy
    from strategies.spec import get_strategy_spec

    with _with_local_test_env():
        cfg = get_config(reload=True)
        strategy = load_strategy(cfg.strategy.module)
        spec = get_strategy_spec(module=strategy)

        assert spec.top_n > 0, "top_n should be positive"
        print(f"   Strategy: TopN={spec.top_n}, sort={spec.sort_spec}")
        print("   Strategy loading OK!")


def test_data_loading() -> None:
    """测试数据加载"""
    print("4. Testing data loading...")
    from data import read_ohlcv_from_mysql

    with _with_local_test_env():
        cfg = _get_local_test_cfg()
        df = read_ohlcv_from_mysql(
            "300001.SZ",
            "stock",
            start_date="20240101",
            end_date="20240301",
            cfg=cfg,
        )
        assert not df.empty, "DataFrame should not be empty"
        assert "close" in df.columns, "Should have 'close' column"
        print(f"   Data loaded: {len(df)} rows")
        print("   Data loading OK!")


def test_strategy_signals() -> None:
    """测试当前策略信号入口"""
    print("5. Testing strategy signals...")
    from data import read_ohlcv_from_mysql
    from strategies.api import load_strategy
    from strategies.spec import get_strategy_spec
    from tests_support.panel_native_fixtures import run_panel_strategy

    with _with_local_test_env():
        cfg = _get_local_test_cfg()
        strategy = load_strategy(cfg.strategy.module)
        df = read_ohlcv_from_mysql(
            "300001.SZ",
            "stock",
            start_date="20240101",
            end_date="20240628",
            cfg=cfg,
        )
        if "stock_code" not in df.columns:
            df = df.assign(stock_code="300001.SZ")
        run = run_panel_strategy(
            strategy,
            df,
            spec=get_strategy_spec(module=strategy),
        )
        df_result = run.signal_frame()

        assert "direction" in df_result.columns, "Should have 'direction' column"
        assert "buy_signal_raw" in df_result.columns, "Should have raw buy signals"
        assert "sell_signal_raw" in df_result.columns, "Should have raw sell signals"
        assert "score" in df_result.columns, "Should have 'score' column"

        buy_count = int((df_result["direction"] == "buy").sum())
        sell_count = int((df_result["direction"] == "sell").sum())
        assert buy_count >= 0 and sell_count >= 0
        print(f"   Trade signals: buy={buy_count}, sell={sell_count}")
        print("   Strategy signals OK!")


def test_repo_hygiene() -> None:
    """测试仓库卫生约束"""
    assert not (ROOT / "tetx.py").exists(), "tetx.py should not exist at repo root"

    scan_roots = [
        ROOT / "app",
        ROOT / "backtest",
        ROOT / "core",
        ROOT / "data",
        ROOT / "strategies",
        ROOT / "tests",
        ROOT / "tools",
    ]
    forbidden_tokens = [
        "_tmp" + "_reversal_probe",
        "_probe" + "_runtime",
    ]
    offenders: list[str] = []
    current_test = Path(__file__).resolve()
    for scan_root in scan_roots:
        for path in sorted(scan_root.rglob("*.py"), key=lambda p: str(p).casefold()):
            if path.resolve() == current_test or path.name == "test_runtime_artifact_boundaries.py":
                continue
            text = path.read_text(encoding="utf-8")
            if any(token in text for token in forbidden_tokens):
                offenders.append(str(path.relative_to(ROOT)).replace("\\", "/"))
    assert offenders == [], f"forbidden old probe paths found: {offenders}"

    assert sorted((ROOT / "tools").glob("search_*.py"), key=lambda p: str(p).casefold()) == []

    assert not (ROOT / "strategies" / "_probe_runtime").exists()
    assert not (ROOT / "strategies" / "_tmp_reversal_probe").exists()
    assert not (ROOT / "research").exists()
    assert not (ROOT / "projects" / "factor_research").exists()


def main() -> bool:
    print("=" * 60)
    print("全市场回测系统 - 重构版测试")
    print("=" * 60)

    tests = [
        ("Imports", test_imports),
        ("Config", test_config),
        ("Strategy", test_strategy_loading),
        ("Data", test_data_loading),
        ("Strategy Signals", test_strategy_signals),
        ("Repo Hygiene", test_repo_hygiene),
    ]

    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  {name}: PASS")
            passed += 1
        except Exception as e:
            print(f"  {name}: FAIL ({e})")

    print(f"\n总计: {passed}/{len(tests)} 测试通过")
    print("=" * 60)
    return passed == len(tests)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
