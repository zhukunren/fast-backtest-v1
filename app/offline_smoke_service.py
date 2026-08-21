"""Deterministic local-price fixture for smoke backtests.

The price panel is self-contained, but curve execution still obtains its
official trading calendar from the configured TuShare client.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OFFLINE_SMOKE_STOCK_CODE = "300001.SZ"
_PRICE_CSV = """trade_date,stock_code,open,high,low,close,volume
20250102,300001.SZ,12.018,12.1382,11.88,12.0,1000000
20250103,300001.SZ,12.3891,12.513,12.2478,12.3715,1012000
20250106,300001.SZ,12.7088,12.8359,12.5669,12.6939,1024000
20250107,300001.SZ,12.9361,13.0655,12.7964,12.9256,1036000
20250108,300001.SZ,13.0443,13.1747,12.9093,13.0397,1048000
20250109,300001.SZ,13.0256,13.1577,12.8953,13.0274,1060000
20250110,300001.SZ,12.8923,13.0294,12.7634,12.9004,1072000
20250113,300001.SZ,12.6748,12.8149,12.5481,12.688,1084000
20250114,300001.SZ,12.4167,12.5576,12.2925,12.4333,1096000
20250115,300001.SZ,12.1679,12.3078,12.0462,12.186,1108000
20250116,300001.SZ,11.9769,12.1145,11.8571,11.9946,1120000
20250117,300001.SZ,11.8833,12.0177,11.7644,11.8987,1132000
20250120,300001.SZ,11.9114,12.0423,11.7923,11.9231,1000000
20250121,300001.SZ,12.0665,12.194,11.9459,12.0732,1012000
20250122,300001.SZ,12.3341,12.4583,12.2108,12.335,1024000
20250123,300001.SZ,12.6819,12.8087,12.5497,12.6765,1036000
20250124,300001.SZ,13.0646,13.1953,12.9227,13.0532,1048000
20250127,300001.SZ,13.4317,13.566,13.2811,13.4153,1060000
20250128,300001.SZ,13.7347,13.872,13.5778,13.7149,1072000
20250129,300001.SZ,13.9352,14.0745,13.7752,13.9143,1084000
20250130,300001.SZ,14.0109,14.1511,13.8516,13.9915,1096000
20250131,300001.SZ,13.9594,14.099,13.8042,13.9437,1108000
20250203,300001.SZ,13.7982,13.9362,13.6501,13.7879,1120000
20250204,300001.SZ,13.5621,13.6977,13.4227,13.5583,1132000
20250205,300001.SZ,13.2976,13.4335,13.1647,13.3005,1000000
20250206,300001.SZ,13.0557,13.1954,12.9252,13.0648,1012000
20250207,300001.SZ,12.8837,13.0267,12.7549,12.8977,1024000
20250210,300001.SZ,12.8177,12.9636,12.6895,12.8353,1036000
20250211,300001.SZ,12.8774,13.0256,12.7486,12.8967,1048000
20250212,300001.SZ,13.0627,13.2126,12.9321,13.0817,1060000
20250213,300001.SZ,13.3539,13.5044,13.2204,13.3707,1072000
20250214,300001.SZ,13.7149,13.8648,13.5777,13.7275,1084000
20250217,300001.SZ,14.0984,14.2463,13.9574,14.1052,1096000
20250218,300001.SZ,14.4538,14.5983,14.3091,14.4537,1108000
20250219,300001.SZ,14.7346,14.882,14.5801,14.7273,1120000
20250220,300001.SZ,14.9064,15.0554,14.7436,14.8925,1132000
20250221,300001.SZ,14.9514,15.1009,14.7832,14.9325,1000000
20250224,300001.SZ,14.8723,15.021,14.7021,14.8506,1012000
20250225,300001.SZ,14.691,14.8379,14.5224,14.6691,1024000
20250226,300001.SZ,14.4458,14.5903,14.2819,14.4262,1036000
20250227,300001.SZ,14.185,14.3268,14.028,14.1697,1048000
20250228,300001.SZ,13.959,14.0986,13.81,13.9495,1060000
20250303,300001.SZ,13.8128,13.9509,13.6718,13.8099,1072000
20250304,300001.SZ,13.7785,13.9203,13.6407,13.7825,1084000
20250305,300001.SZ,13.8704,14.0197,13.7317,13.8809,1096000
20250306,300001.SZ,14.0831,14.2402,13.9423,14.0992,1108000
20250307,300001.SZ,14.3922,14.5565,14.2483,14.4123,1120000
20250310,300001.SZ,14.7582,14.9282,14.6106,14.7804,1132000
20250311,300001.SZ,15.1329,15.3062,14.9816,15.1547,1000000
20250312,300001.SZ,15.4667,15.6404,15.3121,15.4856,1012000
20250313,300001.SZ,15.7168,15.8877,15.5596,15.7304,1024000
20250314,300001.SZ,15.8533,16.0184,15.6948,15.8598,1036000
20250317,300001.SZ,15.8643,16.023,15.7044,15.863,1048000
20250318,300001.SZ,15.7579,15.9154,15.5914,15.7489,1060000
20250319,300001.SZ,15.5604,15.716,15.3896,15.545,1072000
20250320,300001.SZ,15.3129,15.466,15.14,15.2929,1084000
20250321,300001.SZ,15.0641,15.2148,14.8915,15.042,1096000
20250324,300001.SZ,14.8632,15.0118,14.6928,14.8412,1108000
20250325,300001.SZ,14.7514,14.8989,14.5845,14.7318,1120000
20250326,300001.SZ,14.7557,14.9033,14.5932,14.7406,1132000
20250327,300001.SZ,14.8846,15.0335,14.7268,14.8755,1000000
20250328,300001.SZ,15.1268,15.2781,14.9736,15.1249,1012000
20250331,300001.SZ,15.453,15.6133,15.2985,15.4587,1024000
"""
OFFLINE_SMOKE_PRICE_ROW_COUNT = len(_PRICE_CSV.splitlines()) - 1


@dataclass(frozen=True, slots=True)
class OfflineSmokeRun:
    result: object
    setup_elapsed_sec: float | None = None
    backtest_elapsed_sec: float | None = None
    price_cache_mode: str = "none"


def offline_smoke_dataset_info() -> dict[str, object]:
    return {
        "source": "app.offline_smoke_service",
        "stock_code": OFFLINE_SMOKE_STOCK_CODE,
        "price_rows": OFFLINE_SMOKE_PRICE_ROW_COUNT,
        "embedded": True,
        "exists": True,
    }


def materialize_offline_smoke_data(sample_dir: Path) -> None:
    frame = pd.read_csv(StringIO(_PRICE_CSV), dtype={"stock_code": str})
    sample_dir.mkdir(parents=True, exist_ok=True)
    prices_dir = sample_dir / "prices"
    prices_dir.mkdir(parents=True, exist_ok=True)
    frame.to_csv(prices_dir / f"{OFFLINE_SMOKE_STOCK_CODE}.csv", index=False)
    pd.DataFrame(
        {"stock_code": [OFFLINE_SMOKE_STOCK_CODE], "stock_name": ["OFFLINE_SMOKE"]}
    ).to_csv(sample_dir / "stock_basic.csv", index=False)


def build_offline_smoke_panel(sample_dir: Path, panel_path: Path) -> None:
    frames = []
    for csv_path in sorted((sample_dir / "prices").glob("*.csv"), key=lambda path: path.name.casefold()):
        frame = pd.read_csv(csv_path)
        if "stock_code" not in frame.columns:
            frame["stock_code"] = csv_path.stem
        frames.append(frame)
    if not frames:
        raise FileNotFoundError("offline smoke price data is empty")
    panel = pd.concat(frames, ignore_index=True)
    panel["trade_date"] = pd.to_datetime(
        panel["trade_date"].astype(str).str.strip(),
        format="%Y%m%d",
        errors="raise",
    ).dt.normalize()
    panel["stock_code"] = panel["stock_code"].astype(str).str.strip().str.upper()
    panel = panel.sort_values(["stock_code", "trade_date"], kind="mergesort").reset_index(drop=True)
    panel_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(panel_path, index=False)
    content_sha256 = hashlib.sha256(panel_path.read_bytes()).hexdigest()
    manifest = {
        "format": "parquet",
        "schema_version": 3,
        "created_at_utc": "2026-01-01T00:00:00+00:00",
        "dataset_type": "daily_a_share_price_panel",
        "source": "offline_fixture",
        "source_table": "embedded_prices",
        "source_db": "offline",
        "source_config": {
            "source": "offline_fixture",
            "source_table": "embedded_prices",
            "source_db": "offline",
            "columns": list(panel.columns),
        },
        "output": str(panel_path),
        "file_sha256": content_sha256,
        "rows": int(len(panel)),
        "stocks": int(panel["stock_code"].nunique()),
        "columns": list(panel.columns),
        "duplicate_keys": int(panel.duplicated(["stock_code", "trade_date"]).sum()),
        "min_trade_date": panel["trade_date"].min().strftime("%Y-%m-%d"),
        "max_trade_date": panel["trade_date"].max().strftime("%Y-%m-%d"),
    }
    panel_path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _env_overrides(
    sample_dir: Path,
    *,
    profile_timing: bool,
    panel_path: Path | str | None,
    output_root: Path | str | None,
) -> dict[str, str]:
    isolated_output_root = Path(output_root) if output_root is not None else sample_dir / "output"
    isolated_output_root = isolated_output_root.resolve()
    return {
        "LOCAL_DATA_DIR": str(sample_dir.resolve()),
        "SIGNAL_DATA_DIR": str(isolated_output_root / "signal_data"),
        "STOCK_DATA_DIR": str(isolated_output_root / "stock_data"),
        "TRADE_DATA_DIR": str(isolated_output_root / "trade_data"),
        "RETURN_DATE_DIR": str(isolated_output_root / "return_date"),
        "RETURN_DATE_WITH_RETURNS_DIR": str(isolated_output_root / "return_date_with_returns"),
        "NET_VALUE_UNIQUE_DATE_DIR": str(isolated_output_root / "net_value_unique_date"),
        "STRATEGY_MODULE": "strategy_library.builtin.offline_smoke",
        "START_TIME": "20250102",
        "END_TIME": "20250331",
        "DEFAULT_STOCK_CODE": OFFLINE_SMOKE_STOCK_CODE,
        "COMPUTE_METRICS": "1",
        "RUN_QUIET": "1",
        "PROFILE_TIMING": "1" if profile_timing else "0",
        "PLOT_NET_VALUE": "0",
        "CLEAR_OUTPUTS": "1",
        "CLEAR_ALL_TABLES": "0",
        "STATE_MACHINE_JIT": "0",
        "NET_VALUE_JIT": "0",
        "N_JOBS": "1",
        "OFFSET_DAYS": "0",
        "CHECK_TRADE_CALENDAR": "0",
        "PORTFOLIO_ENGINE": "legacy",
        "PYDANTIC_VALIDATE": "1",
        "PYDANTIC_VALIDATE_SAMPLE": "50",
    }


@contextmanager
def offline_smoke_environment(
    sample_dir: Path,
    *,
    profile_timing: bool = False,
    panel_path: Path | str | None = None,
    output_root: Path | str | None = None,
) -> Iterator[None]:
    effective_panel_path = Path(panel_path) if panel_path is not None else sample_dir / "panel.parquet"
    if not effective_panel_path.is_file():
        build_offline_smoke_panel(sample_dir, effective_panel_path)
    overrides = _env_overrides(
        sample_dir,
        profile_timing=profile_timing,
        panel_path=effective_panel_path,
        output_root=output_root,
    )
    overrides["PRICE_PANEL_SOURCE"] = "parquet"
    overrides["PRICE_PANEL_PATH"] = str(effective_panel_path.resolve())
    keys = tuple(sorted(overrides, key=str.casefold))
    previous = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ[key] = overrides[key]
    from config import get_config

    get_config(reload=True)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_config(reload=True)


def _allocate_sample_dir() -> Path:
    override = str(os.environ.get("OFFLINE_SMOKE_TMP_ROOT") or "").strip()
    candidates = [Path(override)] if override else []
    candidates.extend(
        (
            Path(tempfile.gettempdir()) / "backtest_offline_smoke",
            ROOT / "_pytest_tmp",
        )
    )
    last_error: Exception | None = None
    for root in candidates:
        try:
            root.mkdir(parents=True, exist_ok=True)
            sample_dir = root / f"offline_smoke_{uuid4().hex}"
            sample_dir.mkdir(parents=True, exist_ok=False)
            return sample_dir
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"unable to allocate offline smoke sample dir: {last_error}")


def _normalize_price_cache_mode(raw: object) -> str:
    value = str(raw or "none").strip().lower().replace("-", "_")
    if value in {"", "0", "false", "no", "none"}:
        return "none"
    raise ValueError("offline smoke only supports panel-backed price data")


def run_offline_smoke_backtest(
    *,
    profile_timing: bool = False,
    panel_path: Path | str | None = None,
    price_cache_mode: str = "none",
    artifact_policy: str = "none",
) -> OfflineSmokeRun:
    setup_started = perf_counter()
    normalized_price_cache_mode = _normalize_price_cache_mode(price_cache_mode)
    sample_dir = _allocate_sample_dir()
    try:
        materialize_offline_smoke_data(sample_dir)
        generated_panel_path = sample_dir / "panel.parquet"
        build_offline_smoke_panel(sample_dir, generated_panel_path)
        effective_panel_path = Path(panel_path).resolve() if panel_path is not None else generated_panel_path
        with offline_smoke_environment(
            sample_dir,
            profile_timing=profile_timing,
            panel_path=effective_panel_path,
        ):
            from backtest.orchestration import run_backtest
            from config import apply_config_overrides, get_config
            from core.execution_policy import get_execution_policy
            from core.market_timing import BoardMemberCache
            from runtime_lifecycle.reset import reset_runtime_state
            from strategies.api import load_strategy_descriptor

            cfg = apply_config_overrides(
                get_config(),
                {
                    "mysql.daily_fields": (),
                    "panel.source": "parquet",
                    "panel.path": str(effective_panel_path),
                },
            )
            reset_runtime_state(clear_strategy_cache=True)
            descriptor = load_strategy_descriptor(cfg.strategy.module)
            execution_policy = get_execution_policy(cfg)
            setup_elapsed_sec = perf_counter() - setup_started
            backtest_started = perf_counter()
            result = run_backtest(
                "20250102",
                "20250331",
                cfg=cfg,
                spec=descriptor.spec,
                execution_policy=execution_policy,
                risk_cfg=descriptor.risk_control,
                stock_code_list=(OFFLINE_SMOKE_STOCK_CODE,),
                strategy_descriptor=descriptor,
                strategy=descriptor.strategy,
                artifact_policy=artifact_policy,
                board_member_cache=BoardMemberCache({}),
            )
            return OfflineSmokeRun(
                result=result,
                setup_elapsed_sec=setup_elapsed_sec,
                backtest_elapsed_sec=perf_counter() - backtest_started,
                price_cache_mode=normalized_price_cache_mode,
            )
    finally:
        shutil.rmtree(sample_dir, ignore_errors=True)


__all__ = [
    "OFFLINE_SMOKE_PRICE_ROW_COUNT",
    "OFFLINE_SMOKE_STOCK_CODE",
    "OfflineSmokeRun",
    "build_offline_smoke_panel",
    "materialize_offline_smoke_data",
    "offline_smoke_dataset_info",
    "offline_smoke_environment",
    "run_offline_smoke_backtest",
]
