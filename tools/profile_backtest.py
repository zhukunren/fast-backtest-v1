"""用途:
- 对结构化回测 case 做定时剖析，输出总耗时、回测内核耗时与阶段耗时 JSON。
- 当前主要用于 `offline_smoke` 基准、性能回归排查和基线采样。

用法:
- 直接打印结果：`python tools/profile_backtest.py --case offline_smoke`
- 真实股票数据（MySQL 或 Parquet panel）：`python tools/profile_backtest.py --case mysql_real --stock-limit 200`
- 输出到文件：`python tools/profile_backtest.py --case offline_smoke --json-out output/tools/profile/offline_smoke.json`"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.result import CurvePayload, RunResult


def _warm_offline_smoke_imports() -> None:
    # Keep the structured timer focused on the backtest path itself
    # rather than one-time import cost from the runner stack.
    from backtest.engines import compose_engine
    from backtest.orchestration import run_backtest  # noqa: F401
    from backtest.orchestration.signal_pipeline import _load_signal_pipeline_deps
    from config import get_config  # noqa: F401
    from core.execution_policy import get_execution_policy  # noqa: F401
    from core.models import SignalRecord  # noqa: F401
    from strategies.api import load_strategy_descriptor  # noqa: F401

    try:
        import pyarrow.dataset  # noqa: F401
    except ImportError:
        # Keep profiling usable with pandas-supported Parquet backends.
        pass

    _load_signal_pipeline_deps()
    compose_engine("legacy")


def _stage_timings_from_result(result: RunResult) -> dict[str, float]:
    return {
        str(name): float(value)
        for name, value in sorted(result.timings_sec.items(), key=lambda item: str(item[0]).casefold())
    }


def _profile_payload_from_result(
    *,
    case: str,
    elapsed: float,
    result: RunResult,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    curve_payload = result.payload if isinstance(result.payload, CurvePayload) else None
    metrics = curve_payload.metrics if curve_payload is not None else None
    payload: dict[str, object] = {
        "case": case,
        "elapsed_sec": float(elapsed),
        "duration_sec": float(result.duration_sec or 0.0),
        "signal_stock_count": int(result.request.signal_stock_count),
        "final_net_value": metrics.final_net_value if metrics is not None else None,
        "stage_timings_sec": _stage_timings_from_result(result),
    }
    if extra:
        payload.update(extra)
    return payload


def _profile_offline_smoke() -> dict[str, object]:
    from app.offline_smoke_service import run_offline_smoke_backtest

    # Keep the structured inner timer focused on the backtest path itself
    # rather than the one-time pandas/numpy import stack used by the runner.
    import pandas  # noqa: F401
    _warm_offline_smoke_imports()

    price_cache_mode = str(os.environ.get("PROFILE_OFFLINE_PRICE_CACHE_MODE") or "none").strip() or "none"
    started = perf_counter()
    run = run_offline_smoke_backtest(
        profile_timing=True,
        price_cache_mode=price_cache_mode,
    )
    elapsed = perf_counter() - started
    extra: dict[str, object] = {}
    setup_elapsed_sec = getattr(run, "setup_elapsed_sec", None)
    backtest_elapsed_sec = getattr(run, "backtest_elapsed_sec", None)
    if setup_elapsed_sec is not None:
        extra["setup_elapsed_sec"] = float(setup_elapsed_sec)
    if backtest_elapsed_sec is not None:
        extra["backtest_elapsed_sec"] = float(backtest_elapsed_sec)
    extra["price_cache_mode"] = str(getattr(run, "price_cache_mode", price_cache_mode) or price_cache_mode)
    return _profile_payload_from_result(
        case="offline_smoke",
        elapsed=elapsed,
        result=run.result,
        extra=extra,
    )


_MYSQL_TABLE_RE = re.compile(r"^(?P<code>\d{6})[_\.](?P<suffix>SH|SZ|BJ)$", re.IGNORECASE)


def _split_csv(raw: object) -> tuple[str, ...]:
    return tuple(item for item in (str(part).strip() for part in str(raw or "").split(",")) if item)


def _parse_optional_int(raw: object, *, default: int | None = None, minimum: int | None = None) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return default
    try:
        value = int(text)
    except Exception:
        return default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _parse_optional_bool(raw: object) -> bool | None:
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _normalize_stock_code_token(raw: object) -> str | None:
    token = str(raw or "").strip().upper()
    if not token:
        return None
    match = _MYSQL_TABLE_RE.fullmatch(token)
    if not match:
        return token if "." in token else None
    return f"{match.group('code')}.{match.group('suffix').upper()}"


def _unique_sorted_stock_codes(raw_codes: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    normalized = {
        code
        for raw_code in raw_codes
        for code in [_normalize_stock_code_token(raw_code)]
        if code
    }
    return tuple(sorted(normalized, key=str.casefold))


def _profile_stock_code_allowed(cfg, code: str) -> bool:
    if bool(getattr(cfg.backtest, "exclude_bj", False)) and code.endswith(".BJ"):
        return False
    prefixes = tuple(getattr(cfg.backtest, "universe_prefixes", ()) or ())
    if prefixes and not code.startswith(prefixes):
        return False
    return True


def _list_parquet_panel_stock_codes(cfg, *, stock_limit: int | None) -> tuple[str, ...]:
    from data.market_panel import _load_panel_manifest, _resolve_panel_path

    panel_path = _resolve_panel_path(getattr(cfg.panel, "path", ""), cfg=cfg)
    manifest = _load_panel_manifest(panel_path)
    if manifest is None:
        raise RuntimeError(f"Parquet panel 缺少 manifest: {panel_path}")
    coverage = manifest.get("symbol_coverage")
    if not isinstance(coverage, list):
        raise RuntimeError(f"Parquet panel manifest 缺少 symbol_coverage: {panel_path}")

    codes = [
        str(item.get("stock_code") or "")
        for item in coverage
        if isinstance(item, dict)
    ]
    out = tuple(
        code
        for code in _unique_sorted_stock_codes(codes)
        if _profile_stock_code_allowed(cfg, code)
    )
    if stock_limit is not None and stock_limit > 0:
        return out[:stock_limit]
    return out


def _resolve_mysql_profile_stock_codes(
    cfg,
    *,
    raw_stock_codes: object,
    stock_limit: int | None,
) -> tuple[str, ...]:
    explicit = _unique_sorted_stock_codes(_split_csv(raw_stock_codes))
    if explicit:
        return explicit
    panel_source = str(getattr(getattr(cfg, "panel", None), "source", "") or "").strip().lower()
    if panel_source != "parquet":
        raise RuntimeError("profile_mysql_real requires panel.source=parquet")
    return _list_parquet_panel_stock_codes(cfg, stock_limit=stock_limit)


def _build_mysql_profile_overrides(options: dict[str, object]) -> dict[str, object]:
    overrides: dict[str, object] = {
        "run.profile_timing": True,
        "run.quiet": True,
        "run.plot_net_value": False,
    }
    if options.get("start_date"):
        overrides["backtest.start_time"] = str(options["start_date"])
    if options.get("end_date"):
        overrides["backtest.end_time"] = str(options["end_date"])
    if options.get("strategy_module"):
        overrides["strategy.module"] = str(options["strategy_module"])
    if options.get("offset_days") is not None:
        overrides["backtest.offset_days"] = int(options["offset_days"])
    if options.get("panel_source"):
        overrides["panel.source"] = str(options["panel_source"])
    if options.get("panel_path"):
        overrides["panel.path"] = str(options["panel_path"])
    build_topn = options.get("build_topn_trade_segments")
    if build_topn is not None:
        overrides["run.build_topn_trade_segments_report"] = bool(build_topn)
    jit_mode = str(options.get("jit") or "").strip().lower()
    if jit_mode in {"on", "1", "true"}:
        overrides["performance.state_machine_jit"] = True
        overrides["performance.net_value_jit"] = True
    elif jit_mode in {"off", "0", "false"}:
        overrides["performance.state_machine_jit"] = False
        overrides["performance.net_value_jit"] = False
    return overrides


def _profile_mysql_real(options: dict[str, object]) -> dict[str, object]:
    import pandas  # noqa: F401

    from backtest.orchestration import run_backtest
    from config import apply_config_overrides, get_config
    from core.execution_policy import get_execution_policy
    from runtime_lifecycle.reset import reset_runtime_state
    from strategies.api import load_strategy_descriptor

    _warm_offline_smoke_imports()

    base_cfg = get_config(reload=True)
    cfg = apply_config_overrides(base_cfg, _build_mysql_profile_overrides(options))
    stock_limit = _parse_optional_int(options.get("stock_limit"), default=200, minimum=0)
    stock_codes = _resolve_mysql_profile_stock_codes(
        cfg,
        raw_stock_codes=options.get("stock_codes"),
        stock_limit=stock_limit,
    )
    if not stock_codes:
        raise RuntimeError(
            "mysql_real profile 未找到可用股票代码: "
            f"panel_source={getattr(cfg.panel, 'source', '')!r} stock_db={cfg.mysql.stock_db!r}; "
            "请检查 Parquet manifest 或 MySQL 配置，或通过 --stock-codes 显式指定。"
        )

    reset_runtime_state(clear_strategy_cache=True)
    descriptor = load_strategy_descriptor(cfg.strategy.module)
    strategy = descriptor.strategy
    spec = descriptor.spec
    execution_policy = get_execution_policy(cfg)
    risk_cfg = descriptor.risk_control

    started = perf_counter()
    result = run_backtest(
        cfg.backtest.start_time,
        cfg.backtest.end_time,
        cfg=cfg,
        spec=spec,
        execution_policy=execution_policy,
        risk_cfg=risk_cfg,
        stock_code_list=stock_codes,
        strategy=strategy,
        strategy_descriptor=descriptor,
        run_id="profile_mysql_real",
    )
    elapsed = perf_counter() - started
    return _profile_payload_from_result(
        case="mysql_real",
        elapsed=elapsed,
        result=result,
        extra={
            "start_date": str(cfg.backtest.start_time),
            "end_date": str(cfg.backtest.end_time),
            "stock_count": len(stock_codes),
            "stock_codes_sample": list(stock_codes[:10]),
            "stock_limit": stock_limit,
            "strategy_module": str(cfg.strategy.module),
            "build_topn_trade_segments_report": bool(cfg.run.build_topn_trade_segments_report),
            "state_machine_jit": bool(cfg.performance.state_machine_jit),
            "net_value_jit": bool(cfg.performance.net_value_jit),
            "mysql_stock_db": str(cfg.mysql.stock_db),
            "panel_source": str(getattr(cfg.panel, "source", "")),
            "panel_path": str(getattr(cfg.panel, "path", "")),
        },
    )


def _run_case(case: str, *, options: dict[str, object] | None = None) -> dict[str, object]:
    normalized = str(case or "").strip().lower()
    if normalized == "offline_smoke":
        return _profile_offline_smoke()
    if normalized == "mysql_real":
        return _profile_mysql_real(options or {})
    raise ValueError(f"unsupported profile case: {case!r}")


def _profile_options_from_args(args: argparse.Namespace) -> dict[str, object]:
    build_topn_cli: bool | None = None
    if args.build_topn_trade_segments:
        build_topn_cli = True
    elif args.no_build_topn_trade_segments:
        build_topn_cli = False
    else:
        build_topn_cli = _parse_optional_bool(os.environ.get("PROFILE_MYSQL_BUILD_TOPN_TRADE_SEGMENTS"))

    return {
        "start_date": args.start_date or os.environ.get("PROFILE_MYSQL_START_DATE", ""),
        "end_date": args.end_date or os.environ.get("PROFILE_MYSQL_END_DATE", ""),
        "stock_limit": args.stock_limit or os.environ.get("PROFILE_MYSQL_STOCK_LIMIT", "200"),
        "stock_codes": args.stock_codes or os.environ.get("PROFILE_MYSQL_STOCK_CODES", ""),
        "offset_days": _parse_optional_int(
            args.offset_days or os.environ.get("PROFILE_MYSQL_OFFSET_DAYS"),
            minimum=0,
        ),
        "panel_source": args.panel_source or os.environ.get("PRICE_PANEL_SOURCE", ""),
        "panel_path": args.panel_path or os.environ.get("PRICE_PANEL_PATH", ""),
        "strategy_module": args.strategy_module or os.environ.get("PROFILE_MYSQL_STRATEGY_MODULE", ""),
        "build_topn_trade_segments": build_topn_cli,
        "jit": args.jit or os.environ.get("PROFILE_MYSQL_JIT", ""),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="对结构化回测 case 做耗时剖析，并输出 JSON 结果。",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "示例:\n"
            "  python tools/profile_backtest.py --case offline_smoke\n"
            "  python tools/profile_backtest.py --case mysql_real --stock-limit 200 --start-date 20240101 --end-date 20251231\n"
            "  python tools/profile_backtest.py --case offline_smoke --json-out output/tools/profile/offline_smoke.json"
        ),
    )
    parser.add_argument(
        "--case",
        default="offline_smoke",
        help="要剖析的 case 名称；支持 `offline_smoke` / `mysql_real`。",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="可选：JSON 输出路径；不传则直接打印到控制台。",
    )
    parser.add_argument("--start-date", default="", help="mysql_real: 覆盖回测开始日期 YYYYMMDD。")
    parser.add_argument("--end-date", default="", help="mysql_real: 覆盖回测结束日期 YYYYMMDD。")
    parser.add_argument("--stock-limit", default="", help="mysql_real: 从 MySQL 行情表枚举时最多取多少只股票，默认 200。")
    parser.add_argument("--stock-codes", default="", help="mysql_real: 逗号分隔股票代码；传入后不枚举 MySQL 表。")
    parser.add_argument("--offset-days", default="", help="mysql_real: 覆盖 backtest.offset_days。")
    parser.add_argument(
        "--panel-source",
        default="",
        choices=["", "parquet"],
        help="mysql_real: 覆盖 panel.source；当前只支持 parquet。",
    )
    parser.add_argument("--panel-path", default="", help="mysql_real: 覆盖 panel.path。")
    parser.add_argument("--strategy-module", default="", help="mysql_real: 覆盖策略模块路径。")
    parser.add_argument(
        "--build-topn-trade-segments",
        action="store_true",
        help="mysql_real: 打开 TopN 交易段报告阶段。",
    )
    parser.add_argument(
        "--no-build-topn-trade-segments",
        action="store_true",
        help="mysql_real: 关闭 TopN 交易段报告阶段。",
    )
    parser.add_argument(
        "--jit",
        choices=["", "on", "off"],
        default="",
        help="mysql_real: 覆盖 STATE_MACHINE_JIT/NET_VALUE_JIT，默认使用配置。",
    )
    args = parser.parse_args(argv)

    payload = _run_case(args.case, options=_profile_options_from_args(args))
    output = json.dumps(payload, ensure_ascii=True, indent=2)
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"[OK] profile written: {out_path}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
