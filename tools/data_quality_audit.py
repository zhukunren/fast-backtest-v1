"""Run explicit data-quality audit checks with validation enabled."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.data_quality import audit_panel_manifest_payload, check_point_in_time_frame, check_price_frame_quality  # noqa: E402
from core.data_quality import read_panel_manifest  # noqa: E402
from core.research_identity import sha256_file  # noqa: E402


def build_audit_env(*, sample_size: int) -> dict[str, str]:
    env = os.environ.copy()
    env["PYDANTIC_VALIDATE"] = "1"
    env["PYDANTIC_VALIDATE_SAMPLE"] = str(int(sample_size))
    env.setdefault("RUN_QUIET", "1")
    return env


def build_audit_command(*, target: str) -> list[str]:
    normalized = str(target or "").strip().lower()
    if normalized == "offline-smoke":
        return [sys.executable, "tests/test_offline_smoke.py"]
    if normalized == "pytest-offline-smoke":
        return [sys.executable, "tests/test_offline_smoke.py"]
    if normalized == "local-source":
        return [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_local_source.py",
            "tests/test_thsindex_local_source.py",
            "-q",
        ]
    if normalized == "trade-calendar":
        return [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_trade_calendar_tushare.py",
            "tests/test_trade_calendar_offsets.py",
            "-q",
        ]
    if normalized == "stock-basic":
        return [sys.executable, "-m", "pytest", "tests/test_stock_basic_local.py", "-q"]
    if normalized == "mysql-boundary-dry-run":
        return [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_mysql_support.py",
            "tests/test_read_daily_aliases.py",
            "tests/test_read_ohlcv_fallback.py",
            "-q",
        ]
    if normalized == "panel-native-storage":
        return [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_market_panel.py",
            "tests/test_panel_native_index.py",
            "tests/test_panel_native_quote_store.py",
            "-q",
        ]
    if normalized == "price-frame-quality":
        return []
    if normalized == "panel-manifest":
        return []
    if normalized == "point-in-time":
        return []
    raise ValueError(f"unsupported audit target: {target!r}")


def _iter_price_csv_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    candidates: list[Path] = []
    roots = [input_path]
    prices_dir = input_path / "prices"
    if prices_dir.is_dir():
        roots = [prices_dir]
    for root in roots:
        if not root.is_dir():
            continue
        candidates.extend(path for path in root.glob("*.csv") if path.is_file())
    return sorted(set(candidates), key=lambda path: str(path).casefold())


def run_price_frame_quality_audit(
    *,
    input_path: Path,
    max_files: int = 0,
    max_abs_daily_return: float = 0.5,
) -> dict[str, Any]:
    files = _iter_price_csv_files(input_path)
    if max_files > 0:
        files = files[:max_files]
    reports: list[dict[str, Any]] = []
    for path in files:
        try:
            df = pd.read_csv(path)
            report = check_price_frame_quality(
                df,
                source=str(path),
                max_abs_daily_return=max_abs_daily_return,
            )
            payload = report.to_dict()
        except Exception as exc:
            payload = {
                "artifact_type": "data_quality_report",
                "source": str(path),
                "ok": False,
                "issues": [
                    {
                        "severity": "error",
                        "code": "read_failed",
                        "message": str(exc),
                        "count": 1,
                        "sample": [],
                    }
                ],
            }
        reports.append(payload)
    failed = [report for report in reports if not bool(report.get("ok"))]
    return {
        "ok": not failed and bool(files),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "target": "price-frame-quality",
        "input_path": str(input_path),
        "file_count": len(files),
        "failed_count": len(failed),
        "reports": reports,
    }


def _resolve_input_path(raw: str, *, default: str = "") -> Path:
    value = str(raw or default or "").strip()
    if not value:
        return Path()
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def _read_tabular(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    raise ValueError(f"unsupported tabular input type: {path}")


def _panel_manifest_path(input_path: Path) -> Path:
    if input_path.suffix.casefold() == ".parquet":
        return input_path.with_suffix(".manifest.json")
    return input_path


def _panel_path_from_manifest(manifest_path: Path, payload: dict[str, Any]) -> Path:
    declared = str(payload.get("output") or "").strip()
    if declared:
        declared_path = Path(declared)
        if declared_path.is_absolute():
            return declared_path.resolve()
        candidates = (
            (manifest_path.parent / declared_path).resolve(),
            (ROOT / declared_path).resolve(),
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    suffix = ".manifest.json"
    name = manifest_path.name
    if name.casefold().endswith(suffix):
        return manifest_path.with_name(f"{name[:-len(suffix)]}.parquet")
    return manifest_path.with_suffix(".parquet")


def _identity_issue(*, code: str, message: str) -> dict[str, Any]:
    return {
        "severity": "error",
        "code": str(code),
        "message": str(message),
        "count": 1,
        "sample": [],
    }


def _append_identity_issue(report: dict[str, Any], *, code: str, message: str) -> None:
    issues = report.setdefault("issues", [])
    if not isinstance(issues, list):
        issues = []
        report["issues"] = issues
    issues.append(_identity_issue(code=code, message=message))
    report["ok"] = False


def run_panel_manifest_audit(
    *,
    input_path: Path,
    required_columns: tuple[str, ...],
) -> dict[str, Any]:
    if str(input_path).strip() in {"", "."}:
        input_path = ROOT / "data" / "panel_cache" / "stock_daily.parquet"
    input_path = input_path.resolve()
    manifest_path = _panel_manifest_path(input_path)
    panel_path = input_path if input_path.suffix.casefold() == ".parquet" else Path()
    identity: dict[str, Any] = {
        "panel_path": str(panel_path) if str(panel_path) != "." else "",
        "manifest_path": str(manifest_path),
        "declared_sha256": "",
        "actual_sha256": "",
        "verified": False,
    }

    if not manifest_path.is_file():
        report_payload = {
            "artifact_type": "data_quality_report",
            "dataset_type": "panel_manifest",
            "source": str(manifest_path),
            "ok": False,
            "issues": [
                _identity_issue(
                    code="manifest_file_missing",
                    message=f"panel manifest file does not exist: {manifest_path}",
                )
            ],
        }
    else:
        try:
            payload = read_panel_manifest(manifest_path)
            report = audit_panel_manifest_payload(payload, source=str(manifest_path), required_columns=required_columns)
            report_payload = report.to_dict()
            if input_path.suffix.casefold() != ".parquet":
                panel_path = _panel_path_from_manifest(manifest_path, payload)
                identity["panel_path"] = str(panel_path)

            declared_sha256 = str(payload.get("file_sha256") or "").strip().casefold()
            identity["declared_sha256"] = declared_sha256
            if not panel_path.is_file():
                _append_identity_issue(
                    report_payload,
                    code="panel_file_missing",
                    message=f"panel file referenced by manifest does not exist: {panel_path}",
                )
            elif not declared_sha256:
                _append_identity_issue(
                    report_payload,
                    code="manifest_file_sha256_missing",
                    message=f"panel manifest does not declare file_sha256: {manifest_path}",
                )
            else:
                try:
                    actual_sha256 = sha256_file(panel_path).casefold()
                except OSError as exc:
                    _append_identity_issue(
                        report_payload,
                        code="panel_file_hash_failed",
                        message=f"failed to hash panel file {panel_path}: {exc}",
                    )
                else:
                    identity["actual_sha256"] = actual_sha256
                    identity["verified"] = actual_sha256 == declared_sha256
                    if not identity["verified"]:
                        _append_identity_issue(
                            report_payload,
                            code="panel_file_sha256_mismatch",
                            message=(
                                "panel file SHA-256 does not match manifest: "
                                f"panel={panel_path} declared={declared_sha256} actual={actual_sha256}"
                            ),
                        )
        except Exception as exc:
            report_payload = {
                "artifact_type": "data_quality_report",
                "dataset_type": "panel_manifest",
                "source": str(manifest_path),
                "ok": False,
                "issues": [
                    _identity_issue(
                        code="manifest_read_failed",
                        message=f"failed to read panel manifest {manifest_path}: {exc}",
                    )
                ],
            }
    return {
        "ok": bool(report_payload.get("ok")),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "target": "panel-manifest",
        "input_path": str(input_path),
        "manifest_path": str(manifest_path),
        "panel_path": str(panel_path) if str(panel_path) != "." else "",
        "identity": identity,
        "report": report_payload,
    }


def run_point_in_time_audit(
    *,
    input_path: Path,
    trade_date_col: str,
    available_at_col: str,
) -> dict[str, Any]:
    try:
        df = _read_tabular(input_path)
        report = check_point_in_time_frame(
            df,
            source=str(input_path),
            trade_date_col=trade_date_col,
            available_at_col=available_at_col,
        )
        report_payload = report.to_dict()
    except Exception as exc:
        report_payload = {
            "artifact_type": "data_quality_report",
            "dataset_type": "point_in_time_frame",
            "source": str(input_path),
            "ok": False,
            "issues": [
                {
                    "severity": "error",
                    "code": "point_in_time_read_failed",
                    "message": str(exc),
                    "count": 1,
                    "sample": [],
                }
            ],
        }
    return {
        "ok": bool(report_payload.get("ok")),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "target": "point-in-time",
        "input_path": str(input_path),
        "trade_date_col": trade_date_col,
        "available_at_col": available_at_col,
        "report": report_payload,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="运行显式数据质量审计，并临时打开 Pydantic 记录级校验。",
    )
    parser.add_argument(
        "--target",
        choices=(
            "offline-smoke",
            "pytest-offline-smoke",
            "local-source",
            "trade-calendar",
            "stock-basic",
            "mysql-boundary-dry-run",
            "panel-native-storage",
            "price-frame-quality",
            "panel-manifest",
            "point-in-time",
        ),
        default="offline-smoke",
        help="审计目标。",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=int(os.environ.get("PYDANTIC_VALIDATE_SAMPLE", "200") or "200"),
        help="Pydantic 抽样行数；<=0 表示全量。",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="可选：写出审计摘要 JSON。",
    )
    parser.add_argument(
        "--input",
        default="",
        help="price-frame-quality 目标的 CSV 文件或目录；默认使用 LOCAL_DATA_DIR/SAMPLE_DATA_DIR。",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="price-frame-quality 最多检查文件数；0 表示不限。",
    )
    parser.add_argument(
        "--max-abs-daily-return",
        type=float,
        default=0.5,
        help="price-frame-quality 的单日收盘收益绝对值告警阈值。",
    )
    parser.add_argument(
        "--trade-date-col",
        default="trade_date",
        help="point-in-time 目标的交易日期列名。",
    )
    parser.add_argument(
        "--available-at-col",
        default="available_at",
        help="point-in-time 目标的数据可见时间列名。",
    )
    parser.add_argument(
        "--required-columns",
        default="trade_date,stock_code,open,high,low,close",
        help="panel-manifest 必须声明的列，逗号分隔。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    normalized_target = str(args.target).strip().lower()
    if normalized_target == "price-frame-quality":
        input_raw = args.input or os.environ.get("LOCAL_DATA_DIR") or os.environ.get("SAMPLE_DATA_DIR") or ""
        if not input_raw:
            payload = {
                "ok": False,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "target": args.target,
                "reason": "missing_input",
            }
        else:
            payload = run_price_frame_quality_audit(
                input_path=Path(input_raw),
                max_files=int(args.max_files or 0),
                max_abs_daily_return=float(args.max_abs_daily_return),
            )
        if args.json_out:
            _write_json(Path(args.json_out), payload)
        if payload["ok"]:
            print(
                f"[OK] data_quality_audit target={args.target} files={payload.get('file_count', 0)} "
                f"failed={payload.get('failed_count', 0)}"
            )
            return 0
        print(
            f"[FAIL] data_quality_audit target={args.target} files={payload.get('file_count', 0)} "
            f"failed={payload.get('failed_count', 0)}"
        )
        return 1
    if normalized_target == "panel-manifest":
        required_columns = tuple(item.strip() for item in str(args.required_columns or "").split(",") if item.strip())
        payload = run_panel_manifest_audit(
            input_path=_resolve_input_path(args.input),
            required_columns=required_columns,
        )
        if args.json_out:
            _write_json(Path(args.json_out), payload)
        status = "[OK]" if payload["ok"] else "[FAIL]"
        print(f"{status} data_quality_audit target={args.target} manifest={payload.get('manifest_path')}")
        return 0 if payload["ok"] else 1
    if normalized_target == "point-in-time":
        input_path = _resolve_input_path(args.input)
        if not input_path:
            payload = {
                "ok": False,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "target": args.target,
                "reason": "missing_input",
            }
        else:
            payload = run_point_in_time_audit(
                input_path=input_path,
                trade_date_col=str(args.trade_date_col or "trade_date"),
                available_at_col=str(args.available_at_col or "available_at"),
            )
        if args.json_out:
            _write_json(Path(args.json_out), payload)
        status = "[OK]" if payload["ok"] else "[FAIL]"
        print(f"{status} data_quality_audit target={args.target} input={payload.get('input_path', '')}")
        return 0 if payload["ok"] else 1

    command = build_audit_command(target=args.target)
    env = build_audit_env(sample_size=args.sample_size)
    completed = subprocess.run(command, cwd=str(ROOT), env=env, check=False)
    payload = {
        "ok": completed.returncode == 0,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "target": args.target,
        "command": command,
        "returncode": int(completed.returncode),
        "env": {
            "PYDANTIC_VALIDATE": env["PYDANTIC_VALIDATE"],
            "PYDANTIC_VALIDATE_SAMPLE": env["PYDANTIC_VALIDATE_SAMPLE"],
            "RUN_QUIET": env.get("RUN_QUIET", ""),
        },
    }
    if args.json_out:
        _write_json(Path(args.json_out), payload)
    if completed.returncode == 0:
        print(f"[OK] data_quality_audit target={args.target} sample={args.sample_size}")
    else:
        print(f"[FAIL] data_quality_audit target={args.target} rc={completed.returncode}")
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
