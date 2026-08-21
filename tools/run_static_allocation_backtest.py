"""Run a target-weight monthly ETF allocation backtest."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_weights(raw: str) -> dict[str, float]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("--weights must be a JSON object or a path to a JSON file.")
    path = Path(text)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("weights payload must be a JSON object: {stock_code: weight}.")
    return {str(code).strip().upper(): float(weight) for code, weight in payload.items()}


def _load_weight_schedule(raw: str | None) -> dict[str, dict[str, float]] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("weight schedule must be a JSON object: {effective_date: {stock_code: weight}}.")
    out: dict[str, dict[str, float]] = {}
    for raw_date, raw_weights in payload.items():
        if not isinstance(raw_weights, dict):
            raise ValueError("weight schedule values must be JSON objects: {stock_code: weight}.")
        out[str(raw_date).strip()] = {
            str(code).strip().upper(): float(weight)
            for code, weight in raw_weights.items()
        }
    return out


def _resolve_output_dir(raw: str | None) -> Path:
    if raw:
        return Path(raw)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT / "output" / "static_allocation" / run_id


def _load_quote_store(
    codes: list[str],
    *,
    cfg,
    symbol_type: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Any:
    from backtest.fixed_rebalance_runner import load_fixed_rebalance_quote_store

    return load_fixed_rebalance_quote_store(
        [str(code).strip().upper() for code in codes],
        cfg=cfg,
        symbol_type=symbol_type,
        start_date=start_date,
        end_date=end_date,
    )


def _write_csv(path: Path, frame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _write_metadata(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    from config import get_config
    from backtest.fixed_rebalance_runner import parse_fixed_rebalance_dates
    from backtest.signal.inputs import get_trade_days
    from backtest.static_allocation import (
        StaticAllocationConfig,
        StaticAllocationCostConfig,
        run_static_allocation_backtest,
    )

    weights = _load_weights(args.weights)
    weight_schedule = _load_weight_schedule(args.weight_schedule)
    cfg = get_config()
    if not args.start or not args.end:
        raise ValueError("--start and --end are required to load the official trade calendar.")
    trade_days = get_trade_days(
        args.start,
        args.end,
        exchange=cfg.tushare.exchange or "",
        cfg=cfg,
    )
    load_codes = sorted(
        {
            *weights,
            *(code for scheduled in (weight_schedule or {}).values() for code in scheduled),
        },
        key=str.casefold,
    )
    quote_store = _load_quote_store(
        load_codes,
        cfg=cfg,
        symbol_type=args.symbol_type,
        start_date=args.start,
        end_date=args.end,
    )
    result = run_static_allocation_backtest(
        quote_store,
        StaticAllocationConfig(
            target_weights=weights,
            trade_days=trade_days,
            target_weight_schedule=weight_schedule,
            initial_capital=float(args.initial_capital),
            lot_size=int(args.lot_size),
            cash_buffer_pct=float(args.cash_buffer_pct),
            rebalance_frequency=str(args.rebalance_frequency),
            rebalance_dates=parse_fixed_rebalance_dates(args.rebalance_dates),
            start_date=args.start,
            end_date=args.end,
            trade_constraints=cfg.trade_constraints,
            normalize_weights=bool(args.normalize_weights),
            cost=StaticAllocationCostConfig(
                enable=not bool(args.no_cost),
                commission_rate=float(args.commission_rate),
                min_commission=float(args.min_commission),
                stamp_duty_rate=float(args.stamp_duty_rate),
                slippage_bps=float(args.slippage_bps),
                include_buy_commission=True,
                include_sell_commission=True,
            ),
        ),
    )

    out_dir = _resolve_output_dir(args.out_dir)
    _write_csv(out_dir / "net_value.csv", result.merged_df)
    _write_csv(out_dir / "orders.csv", result.orders_df)
    _write_csv(out_dir / "fills.csv", result.fills_df)
    _write_csv(out_dir / "positions.csv", result.positions_df)

    final_net_value = None
    max_drawdown = None
    if not result.merged_df.empty:
        net_value = result.merged_df["sum_net_value"]
        final_net_value = float(net_value.iloc[-1])
        drawdown = (net_value / net_value.cummax()) - 1.0
        max_drawdown = float(drawdown.min())

    metadata = {
        "weights": result.target_weights,
        "weight_schedule": weight_schedule or {},
        "start": args.start,
        "end": args.end,
        "trade_days_count": len(trade_days),
        "symbol_type": args.symbol_type,
        "rebalance_frequency": args.rebalance_frequency,
        "rebalance_dates": parse_fixed_rebalance_dates(args.rebalance_dates),
        "initial_capital": float(args.initial_capital),
        "lot_size": int(args.lot_size),
        "commission_rate": float(args.commission_rate),
        "stamp_duty_rate": float(args.stamp_duty_rate),
        "slippage_bps": float(args.slippage_bps),
        "output_dir": str(out_dir),
        "final_net_value": final_net_value,
        "max_drawdown": max_drawdown,
        "rows": int(len(result.merged_df)),
        "fills": int(len(result.fills_df)),
    }
    _write_metadata(out_dir / "metadata.json", metadata)
    return metadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run monthly target-weight ETF allocation backtest.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Example:\n"
            "  python tools/run_static_allocation_backtest.py "
            "--weights '{\"510300.SH\":0.075,\"510500.SH\":0.075,\"513500.SH\":0.075,"
            "\"513100.SH\":0.075,\"511010.SH\":0.15,\"511260.SH\":0.40,"
            "\"518880.SH\":0.075,\"159985.SZ\":0.075}'"
        ),
    )
    parser.add_argument("--weights", required=True, help="JSON object or path to JSON file: {stock_code: weight}.")
    parser.add_argument(
        "--weight-schedule",
        default="",
        help="Optional JSON object or path: {effective_date: {stock_code: weight}}.",
    )
    parser.add_argument("--symbol-type", choices=["stock", "index", "etf"], default="stock")
    parser.add_argument("--start", required=True, help="Start date, e.g. 20200101 or 2020-01-01.")
    parser.add_argument("--end", required=True, help="End date, e.g. 20251231 or 2025-12-31.")
    parser.add_argument("--rebalance-frequency", default="month_start")
    parser.add_argument("--rebalance-dates", default="", help="Comma/space separated dates or JSON list for custom dates.")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--cash-buffer-pct", type=float, default=0.0)
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--min-commission", type=float, default=0.0)
    parser.add_argument("--stamp-duty-rate", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--normalize-weights", action="store_true", help="Normalize weights instead of requiring sum=1.")
    parser.add_argument("--no-cost", action="store_true", help="Disable transaction costs.")
    parser.add_argument("--out-dir", default=None, help="Output directory. Defaults to output/static_allocation/<run_id>.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    metadata = run(args)
    print(
        "[OK] static_allocation "
        f"rows={metadata['rows']} fills={metadata['fills']} "
        f"final_net_value={metadata['final_net_value']} "
        f"max_drawdown={metadata['max_drawdown']} "
        f"output={metadata['output_dir']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
