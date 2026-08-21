from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

if __package__:
    from tools.quality_gate_runtime import GateCommand
else:
    from quality_gate_runtime import GateCommand

_PYTEST_SUMMARY_RE = re.compile(
    r"(?P<count>\d+)\s+(?P<status>passed|failed|skipped|xfailed|xpassed|errors?)\b",
    flags=re.IGNORECASE,
)
_COVERAGE_TOTAL_RE = re.compile(
    r"^TOTAL(?:\s+\d+){2,5}\s+(?P<percent>\d+(?:\.\d+)?)%",
    flags=re.MULTILINE,
)
_GROUPED_COVERAGE_RE = re.compile(
    r"^\[OK\] grouped coverage:\s+group=(?P<group>[a-z0-9_]+)\s+files=(?P<files>\d+)\s+"
    r"statement=(?P<statement_covered>\d+)/(?P<statement_total>\d+)\s+"
    r"\((?P<statement_percent>\d+(?:\.\d+)?)%\)\s+"
    r"branch=(?P<branch_covered>\d+)/(?P<branch_total>\d+)\s+"
    r"\((?P<branch_percent>\d+(?:\.\d+)?)%\)\s+"
    r"combined=(?P<combined_covered>\d+)/(?P<combined_total>\d+)\s+"
    r"\((?P<combined_percent>\d+(?:\.\d+)?)%\)$",
    flags=re.MULTILINE,
)
_PERF_SMOKE_RE = re.compile(
    r"\bperf_smoke\s+elapsed=(?P<elapsed>\d+(?:\.\d+)?)s\s+"
    r"compare=(?P<comparison>\d+(?:\.\d+)?)s\s+"
    r"rss=(?P<rss>\d+(?:\.\d+)?)MiB\b"
)
_PERF_TIME_BUDGET_RE = re.compile(
    r"\bwithin tolerance:\s+compare=\d+(?:\.\d+)?s\s+"
    r"baseline=(?P<baseline>\d+(?:\.\d+)?)s\s+tol=(?P<tolerance>\d+(?:\.\d+)?)%"
)
_PERF_RSS_BUDGET_RE = re.compile(
    r"\bRSS within tolerance:\s+compare=\d+(?:\.\d+)?MiB\s+"
    r"baseline=(?P<baseline>\d+(?:\.\d+)?)MiB\s+tol=(?P<tolerance>\d+(?:\.\d+)?)%"
)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REPO_INPUT_PREFIX = "repo://"
ROOT = Path(__file__).resolve().parents[1]


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=_reject_nonfinite_json,
    )
    if not isinstance(payload, Mapping):
        raise ValueError("top-level JSON value must be an object")
    return dict(payload)


def _is_absolute_cross_platform_path(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _has_unresolved_path_parts(value: str) -> bool:
    normalized = value.replace("\\", "/")
    return any(part in {".", ".."} for part in normalized.split("/"))


def _sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _validate_topn_input_path(input_path: Any) -> tuple[bool, list[str]]:
    if not isinstance(input_path, str) or not input_path:
        return False, ["input_path must be a non-empty absolute path"]
    if not _is_absolute_cross_platform_path(input_path):
        return False, ["input_path must be a non-empty absolute path"]
    if _has_unresolved_path_parts(input_path):
        return False, ["input_path must not contain unresolved '.' or '..' parts"]
    return True, []


def _validate_topn_input_ref(input_ref: Any) -> list[str]:
    if not isinstance(input_ref, str) or not input_ref.startswith(_REPO_INPUT_PREFIX):
        return ["input_ref must be a repo:// relative reference"]
    relative = input_ref.removeprefix(_REPO_INPUT_PREFIX)
    if not relative or relative.startswith("/") or "\\" in relative:
        return ["input_ref must be a repo:// relative POSIX path"]
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return ["input_ref must not contain empty, '.' or '..' path parts"]
    if ":" in relative:
        return ["input_ref must not contain a drive or URI delimiter"]
    return []


def _validate_topn_preview(
    preview: Any,
    *,
    rows: Any,
    expected_top: int,
    expected_trade_date: str,
) -> list[str]:
    if not isinstance(preview, list):
        return ["preview must be a list"]
    errors: list[str] = []
    expected_preview_rows = min(rows, expected_top) if type(rows) is int and rows > 0 else None
    if expected_preview_rows is not None and len(preview) != expected_preview_rows:
        errors.append(f"preview must contain exactly min(rows, top)={expected_preview_rows} rows")
    for index, row in enumerate(preview):
        if not isinstance(row, Mapping):
            errors.append(f"preview[{index}] must be an object")
            continue
        if row.get("trade_date") != expected_trade_date:
            errors.append(f"preview[{index}].trade_date must match the target date")
        stock_code = row.get("stock_code")
        if not isinstance(stock_code, str) or not stock_code.strip():
            errors.append(f"preview[{index}].stock_code must be a non-empty string")
    return errors


def _validate_topn_source_file(
    input_path: str,
    *,
    input_sha256: Any,
    input_size_bytes: Any,
) -> list[str]:
    errors: list[str] = []
    source = Path(input_path)
    try:
        resolved = source.resolve(strict=True)
    except OSError as exc:
        return [f"input_path cannot be resolved: {type(exc).__name__}: {exc}"]
    if os.path.normcase(os.path.normpath(str(resolved))) != os.path.normcase(
        os.path.normpath(input_path)
    ):
        errors.append("input_path is not the resolved source path")
    try:
        observed_sha256, observed_size = _sha256_and_size(resolved)
    except OSError as exc:
        errors.append(f"input_path cannot be hashed: {type(exc).__name__}: {exc}")
        return errors
    if input_sha256 != observed_sha256:
        errors.append("input_sha256 does not match the resolved input file")
    if input_size_bytes != observed_size:
        errors.append("input_size_bytes does not match the resolved input file")
    return errors


def validate_topn_slice_evidence(
    payload: Mapping[str, Any],
    *,
    expected_trade_date: str,
    expected_top: int,
    verify_input: bool,
) -> list[str]:
    """Validate TopN provenance, optionally re-reading the exact input on the run host."""
    errors: list[str] = []
    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        errors.append("schema_version must be 1")
    if payload.get("ok") is not True:
        errors.append("ok must be true")
    if payload.get("status") != "topn_slice_ok":
        errors.append("status must be 'topn_slice_ok'")
    if payload.get("trade_date") != expected_trade_date:
        errors.append(f"trade_date must be {expected_trade_date!r}")

    top = payload.get("top")
    if type(top) is not int or top != expected_top:
        errors.append(f"top must be the integer {expected_top}")

    rows = payload.get("rows")
    if type(rows) is not int or rows < expected_top:
        errors.append(f"rows must be an integer greater than or equal to top={expected_top}")

    input_path: Any = None
    path_is_valid = False
    if verify_input:
        input_path = payload.get("input_path")
        path_is_valid, path_errors = _validate_topn_input_path(input_path)
        errors.extend(path_errors)
        if "input_ref" in payload:
            errors.append("runtime TopN evidence must not contain input_ref")
    else:
        errors.extend(_validate_topn_input_ref(payload.get("input_ref")))
        if "input_path" in payload:
            errors.append("portable TopN evidence must not contain input_path")

    input_sha256 = payload.get("input_sha256")
    if not isinstance(input_sha256, str) or not _SHA256_RE.fullmatch(input_sha256):
        errors.append("input_sha256 must be a lowercase 64-character SHA256")

    input_size_bytes = payload.get("input_size_bytes")
    if type(input_size_bytes) is not int or input_size_bytes <= 0:
        errors.append("input_size_bytes must be a positive integer")

    errors.extend(
        _validate_topn_preview(
            payload.get("preview"),
            rows=rows,
            expected_top=expected_top,
            expected_trade_date=expected_trade_date,
        )
    )

    if verify_input and path_is_valid:
        assert isinstance(input_path, str)
        errors.extend(
            _validate_topn_source_file(
                input_path,
                input_sha256=input_sha256,
                input_size_bytes=input_size_bytes,
            )
        )
    return errors


def _grouped_coverage_summary(matches: list[re.Match[str]]) -> dict[str, Any]:
    grouped: dict[str, Any] = {}
    for match in matches:
        grouped[match.group("group")] = {
            "file_count": int(match.group("files")),
            "statement": {
                "covered": int(match.group("statement_covered")),
                "total": int(match.group("statement_total")),
                "percent": float(match.group("statement_percent")),
            },
            "branch": {
                "covered": int(match.group("branch_covered")),
                "total": int(match.group("branch_total")),
                "percent": float(match.group("branch_percent")),
            },
            "combined": {
                "covered": int(match.group("combined_covered")),
                "total": int(match.group("combined_total")),
                "percent": float(match.group("combined_percent")),
            },
        }
    return grouped


def summarize_command_output(stdout: str, stderr: str = "") -> dict[str, Any]:
    text = ANSI_ESCAPE_RE.sub("", "\n".join(part for part in (stdout, stderr) if part))
    pytest_counts: dict[str, int] = {}
    for match in _PYTEST_SUMMARY_RE.finditer(text):
        pytest_counts[match.group("status").casefold()] = int(match.group("count"))
    summary: dict[str, Any] = {}
    if pytest_counts:
        summary["pytest"] = pytest_counts
    coverage_matches = list(_COVERAGE_TOTAL_RE.finditer(text))
    if coverage_matches:
        summary["coverage_percent"] = float(coverage_matches[-1].group("percent"))
    grouped_matches = list(_GROUPED_COVERAGE_RE.finditer(text))
    if grouped_matches:
        summary["grouped_coverage"] = _grouped_coverage_summary(grouped_matches)
    perf_matches = list(_PERF_SMOKE_RE.finditer(text))
    if perf_matches:
        perf_match = perf_matches[-1]
        performance = {
            "elapsed_sec": float(perf_match.group("elapsed")),
            "comparison_elapsed_sec": float(perf_match.group("comparison")),
            "peak_rss_mib": float(perf_match.group("rss")),
        }
        time_budget_matches = list(_PERF_TIME_BUDGET_RE.finditer(text))
        if time_budget_matches:
            time_budget = time_budget_matches[-1]
            performance["elapsed_baseline_sec"] = float(time_budget.group("baseline"))
            performance["elapsed_tolerance_pct"] = float(time_budget.group("tolerance"))
        rss_budget_matches = list(_PERF_RSS_BUDGET_RE.finditer(text))
        if rss_budget_matches:
            rss_budget = rss_budget_matches[-1]
            performance["rss_baseline_mib"] = float(rss_budget.group("baseline"))
            performance["rss_tolerance_pct"] = float(rss_budget.group("tolerance"))
        summary["performance"] = performance
    return summary


def _command_option(command: tuple[str, ...], option: str) -> str:
    try:
        index = command.index(option)
        value = command[index + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"command is missing required {option} value") from exc
    if not value:
        raise ValueError(f"command has an empty {option} value")
    return value


def _load_topn_step_summary(spec: GateCommand) -> dict[str, Any]:
    trade_date = _command_option(spec.command, "--date")
    try:
        top = int(_command_option(spec.command, "--top"))
    except ValueError as exc:
        raise ValueError("TopN command has an invalid --top value") from exc
    json_out = Path(_command_option(spec.command, "--json-out"))
    payload = load_json_object(json_out)
    evidence_errors = validate_topn_slice_evidence(
        payload,
        expected_trade_date=trade_date,
        expected_top=top,
        verify_input=True,
    )
    if evidence_errors:
        raise ValueError("; ".join(evidence_errors))
    input_path = payload.get("input_path")
    assert isinstance(input_path, str)
    if __package__:
        from tools.check_topn_slice import build_slice_report
    else:
        from check_topn_slice import build_slice_report

    canonical = build_slice_report(Path(input_path), trade_date, top=top)
    if payload != canonical:
        mismatched_fields = sorted(
            (
                key
                for key in set(payload) | set(canonical)
                if payload.get(key) != canonical.get(key)
            ),
            key=str.casefold,
        )
        raise ValueError(
            "TopN evidence does not match canonical reconstruction; mismatched fields: "
            + ", ".join(mismatched_fields)
        )
    resolved_input = Path(input_path).resolve(strict=True)
    try:
        relative_input = resolved_input.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("TopN input must resolve inside the repository for portable evidence") from exc
    portable = dict(payload)
    portable.pop("input_path", None)
    portable["input_ref"] = f"{_REPO_INPUT_PREFIX}{relative_input.as_posix()}"
    return portable


def summarize_gate_step(
    spec: GateCommand,
    *,
    returncode: int,
    stdout: str,
    stderr: str,
) -> tuple[dict[str, Any], str]:
    summary = summarize_command_output(stdout, stderr)
    if returncode != 0 or spec.name != "topn_slice":
        return summary, ""
    try:
        summary["topn_slice"] = _load_topn_step_summary(spec)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        error = f"invalid structured TopN evidence: {type(exc).__name__}: {exc}"
        return summary, error
    return summary, ""
