"""用途:
- 查看 `merged_signals.csv` 在指定 `trade_date` 的 TopN 切片，快速核对信号合并与排序结果。
- 当目标日期没有记录时，会额外打印最近有数据的日期，方便排查时间窗或过滤条件。

用法:
- 自动定位输入文件：`python tools/check_topn_slice.py --date 2025-05-16 --top 10`
- 指定输入文件：`python tools/check_topn_slice.py --date 2025-05-16 --path output/pipeline/signal_data/merged_signals.csv`"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd


def _load_pandas():
    import pandas as pd

    return pd


def _candidate_default_paths() -> list[Path]:
    candidates: list[Path] = []
    try:
        from config import get_config

        cfg = get_config()
        candidates.append(Path(cfg.paths.signal_data) / "merged_signals.csv")
    except Exception:
        pass

    candidates.extend(
        [
            Path("output/pipeline/signal_data/merged_signals.csv"),
            Path("output/signal_data/merged_signals.csv"),
            Path("output/_compare_results/current/merged_signals.csv"),
            Path("output/_compare_results/clean/merged_signals.csv"),
        ]
    )
    candidates.extend(
        sorted(
            Path("output/tools").glob(
                "*/current/strategies/*/signal_data/merged_signals.csv"
            ),
            key=lambda path: str(path).casefold(),
        )
    )

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _resolve_input_path(path_arg: str | None) -> Path:
    if path_arg:
        return Path(path_arg)
    candidates = _candidate_default_paths()
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if not candidates:
        raise FileNotFoundError("missing merged_signals.csv: no auto-resolve candidates configured")
    searched = "\n".join(f"- {candidate}" for candidate in candidates)
    raise FileNotFoundError(f"missing merged_signals.csv; searched:\n{searched}")


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _normalize_trade_dates(df: pd.DataFrame) -> pd.DataFrame:
    pd = _load_pandas()
    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise").dt.normalize()
    return out


def _sort_slice(slice_df: pd.DataFrame) -> pd.DataFrame:
    if slice_df.empty:
        return slice_df
    sort_cols: list[str] = []
    ascending: list[bool] = []
    if "score" in slice_df.columns:
        sort_cols.append("score")
        ascending.append(False)
    sort_cols.append("stock_code")
    ascending.append(True)
    return slice_df.sort_values(sort_cols, ascending=ascending, kind="mergesort").reset_index(drop=True)


def _nearest_dates(df: pd.DataFrame, target_date: pd.Timestamp, *, limit: int = 5) -> pd.DataFrame:
    pd = _load_pandas()
    if df.empty:
        return pd.DataFrame(columns=["trade_date", "rows"])
    grouped = df.groupby("trade_date", sort=True).size().reset_index(name="rows")
    grouped["distance_days"] = (grouped["trade_date"] - target_date).abs().dt.days
    grouped = grouped.sort_values(
        ["distance_days", "trade_date"],
        ascending=[True, True],
        kind="mergesort",
    ).head(max(1, int(limit)))
    grouped["trade_date"] = grouped["trade_date"].dt.strftime("%Y-%m-%d")
    return grouped.loc[:, ["trade_date", "rows"]].reset_index(drop=True)


def _build_slice_from_bytes(content: bytes, trade_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    pd = _load_pandas()
    df = pd.read_csv(io.BytesIO(content))
    if "trade_date" not in df.columns or "stock_code" not in df.columns:
        raise KeyError("merged_signals.csv missing trade_date/stock_code columns")

    df = _normalize_trade_dates(df)
    target_date = pd.Timestamp(trade_date).normalize()
    slice_df = df[df["trade_date"] == target_date].copy()
    nearest_df = _nearest_dates(df, target_date)
    if not slice_df.empty:
        slice_df["trade_date"] = slice_df["trade_date"].dt.strftime("%Y-%m-%d")
        slice_df = _sort_slice(slice_df)
    return slice_df, nearest_df


def _build_slice(path: Path, trade_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    return _build_slice_from_bytes(path.read_bytes(), trade_date)


def build_slice_report(path: Path, trade_date: str, *, top: int = 10) -> dict[str, Any]:
    if type(top) is not int or top <= 0:
        raise ValueError("top must be a positive integer")
    pd = _load_pandas()
    normalized_trade_date = pd.Timestamp(trade_date).normalize().strftime("%Y-%m-%d")
    resolved_path = path.resolve(strict=True)
    content = resolved_path.read_bytes()
    slice_df, nearest_df = _build_slice_from_bytes(content, normalized_trade_date)
    preview_cols = ["trade_date", "stock_code"]
    if not slice_df.empty and "score" in slice_df.columns and "score" not in preview_cols:
        preview_cols.append("score")
    preview = slice_df.loc[:, preview_cols].head(top).to_dict(orient="records") if not slice_df.empty else []
    rows = int(len(slice_df))
    if rows == 0:
        status = "topn_slice_missing"
    elif rows < top:
        status = "topn_slice_incomplete"
    else:
        status = "topn_slice_ok"
    return {
        "schema_version": 1,
        "ok": rows >= top,
        "status": status,
        "trade_date": normalized_trade_date,
        "top": top,
        "input_path": str(resolved_path),
        "input_sha256": hashlib.sha256(content).hexdigest(),
        "input_size_bytes": len(content),
        "rows": rows,
        "preview": preview,
        "nearest_dates": nearest_df.to_dict(orient="records"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="查看 merged_signals 在指定交易日的 TopN 切片结果。",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "示例:\n"
            "  python tools/check_topn_slice.py --date 2025-05-16 --top 10\n"
            "  python tools/check_topn_slice.py --date 2025-05-16 --path output/pipeline/signal_data/merged_signals.csv"
        ),
    )
    parser.add_argument("--date", required=True, help="目标交易日，格式 `YYYY-MM-DD`。")
    parser.add_argument("--top", type=int, default=10, help="最多显示多少行。")
    parser.add_argument(
        "--path",
        default=None,
        help="可选：手工指定 `merged_signals.csv` 路径；默认自动从配置和常见输出目录中寻找。",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="可选：把结构化切片结果写到一个 JSON 文件。",
    )
    args = parser.parse_args()
    if args.top <= 0:
        parser.error("--top must be a positive integer")

    path = _resolve_input_path(args.path)
    if args.path and not path.exists():
        raise FileNotFoundError(f"missing file: {path}")

    report = build_slice_report(path, args.date, top=args.top)
    if args.json_out:
        _write_json(Path(args.json_out), report)

    if report["status"] == "topn_slice_missing":
        print(f"TopN missing for {report['trade_date']}: rows=0 top={report['top']}")
        nearest_dates = report["nearest_dates"]
        if nearest_dates:
            print("Nearest populated dates:")
            print(_load_pandas().DataFrame(nearest_dates).to_string(index=False))
        return 1
    if report["status"] == "topn_slice_incomplete":
        print(
            f"TopN incomplete for {report['trade_date']}: "
            f"rows={report['rows']} top={report['top']}"
        )
        print(_load_pandas().DataFrame(report["preview"]))
        return 1

    print(
        f"TopN complete for {report['trade_date']}: "
        f"rows={report['rows']} top={report['top']}"
    )
    print(_load_pandas().DataFrame(report["preview"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
