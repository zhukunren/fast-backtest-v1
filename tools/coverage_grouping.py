from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "coverage-groups-v1.json"
METRIC_NAMES = ("statement", "branch", "combined")
REQUIRED_GROUPS = frozenset(
    {
        "config_contracts",
        "data_cache",
        "execution_netvalue_trading",
        "orchestration_allocation",
        "strategies_runtime_static_checks",
        "web_backend",
    }
)
REQUIRED_PRODUCT_ROOTS = frozenset(
    {
        "app",
        "backtest",
        "config",
        "core",
        "data",
        "runtime_lifecycle",
        "strategies",
        "strategy_library",
        "trading",
        "web",
    }
)
REQUIRED_COVERAGE_SOURCES = REQUIRED_PRODUCT_ROOTS
ALLOWED_EXCLUSION_PATTERNS = frozenset(
    {
        "strategy_library/generated/**",
        "web/strategy_library/**",
        "web/tests/**",
    }
)
COVERAGE_FLOOR_RATCHETS: Mapping[str, Mapping[str, float]] = {
    "overall": {"statement": 84.70, "branch": 70.30, "combined": 81.05},
    "config_contracts": {"statement": 84.91, "branch": 72.50, "combined": 81.90},
    "data_cache": {"statement": 86.30, "branch": 76.10, "combined": 83.65},
    "execution_netvalue_trading": {
        "statement": 87.80,
        "branch": 70.70,
        "combined": 83.50,
    },
    "orchestration_allocation": {
        "statement": 87.50,
        "branch": 70.90,
        "combined": 83.30,
    },
    "strategies_runtime_static_checks": {
        "statement": 83.80,
        "branch": 65.10,
        "combined": 79.10,
    },
    "web_backend": {"statement": 81.90, "branch": 69.10, "combined": 78.50},
}


def _normalized_relative_path(raw: str) -> str:
    normalized = str(raw).strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        raise ValueError(f"path must be repository-relative: {raw!r}")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"path contains an invalid segment: {raw!r}")
    return "/".join(parts)


def _coverage_relative_path(raw: str, *, root: Path) -> str:
    value = str(raw).strip().replace("\\", "/")
    if value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        try:
            return Path(value).resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError) as exc:
            raise ValueError(f"coverage path is outside repository: {raw!r}") from exc
    return _normalized_relative_path(value)


def _glob_regex(pattern: str) -> re.Pattern[str]:
    normalized = _normalized_relative_path(pattern)
    expression: list[str] = ["^"]
    index = 0
    while index < len(normalized):
        char = normalized[index]
        if char == "*":
            if index + 1 < len(normalized) and normalized[index + 1] == "*":
                expression.append(".*")
                index += 2
            else:
                expression.append("[^/]*")
                index += 1
            continue
        if char == "?":
            expression.append("[^/]")
        else:
            expression.append(re.escape(char))
        index += 1
    expression.append("$")
    return re.compile("".join(expression), flags=re.IGNORECASE)


def _matches(path: str, pattern: str) -> bool:
    return _glob_regex(pattern).fullmatch(path) is not None


def _sorted_strings(values: Iterable[str]) -> list[str]:
    return sorted(values, key=str.casefold)


def load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} JSON: {type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _validate_metric_policy(raw: Any, *, field: str, errors: list[str]) -> dict[str, float]:
    if not isinstance(raw, Mapping):
        errors.append(f"{field} must be an object")
        return {}
    policy: dict[str, float] = {}
    if set(raw) != set(METRIC_NAMES):
        errors.append(f"{field} must define exactly: {', '.join(METRIC_NAMES)}")
    for metric in METRIC_NAMES:
        value = raw.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{field}.{metric} must be numeric")
            continue
        numeric = float(value)
        if not 0.0 <= numeric <= 100.0:
            errors.append(f"{field}.{metric} must be between 0 and 100")
            continue
        policy[metric] = numeric
    return policy


def _validate_floor_ratchets(manifest: Mapping[str, Any], errors: list[str]) -> None:
    scopes: dict[str, Any] = {"overall": manifest.get("overall")}
    raw_groups = manifest.get("groups")
    if isinstance(raw_groups, list):
        for raw_group in raw_groups:
            if isinstance(raw_group, Mapping) and isinstance(raw_group.get("name"), str):
                scopes[str(raw_group["name"]).casefold()] = raw_group

    for scope, expected in COVERAGE_FLOOR_RATCHETS.items():
        raw_scope = scopes.get(scope)
        if not isinstance(raw_scope, Mapping):
            continue
        floors = raw_scope.get("floors")
        if not isinstance(floors, Mapping):
            continue
        field = "overall.floors" if scope == "overall" else f"group {scope!r} floors"
        for metric, expected_value in expected.items():
            actual = floors.get(metric)
            if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                continue
            if float(actual) != expected_value:
                errors.append(
                    f"{field}.{metric} must equal the V1 ratchet "
                    f"{expected_value}, got {float(actual)}"
                )


def _validate_inventory_paths(inventory: Mapping[str, Any], field: str, errors: list[str]) -> None:
    raw_values = inventory.get(field)
    if not isinstance(raw_values, list) or not raw_values:
        errors.append(f"inventory.{field} must be a non-empty list")
        return
    normalized: list[str] = []
    for index, raw in enumerate(raw_values):
        if not isinstance(raw, str):
            errors.append(f"inventory.{field}[{index}] must be a string")
            continue
        try:
            normalized.append(_normalized_relative_path(raw))
        except ValueError as exc:
            errors.append(str(exc))
    if len({value.casefold() for value in normalized}) != len(normalized):
        errors.append(f"inventory.{field} contains duplicates")
    required = REQUIRED_PRODUCT_ROOTS if field == "product_roots" else REQUIRED_COVERAGE_SOURCES
    if {value.casefold() for value in normalized} != required:
        errors.append(f"inventory.{field} must equal the fixed active V1 source set")


def _validate_exclusions(inventory: Mapping[str, Any], errors: list[str]) -> None:
    exclusions = inventory.get("exclusions")
    if not isinstance(exclusions, list):
        errors.append("inventory.exclusions must be a list")
        return
    seen_patterns: set[str] = set()
    for index, raw in enumerate(exclusions):
        if not isinstance(raw, Mapping):
            errors.append(f"inventory.exclusions[{index}] must be an object")
            continue
        pattern = raw.get("pattern")
        reason = raw.get("reason")
        if not isinstance(pattern, str):
            errors.append(f"inventory.exclusions[{index}].pattern must be a string")
        else:
            try:
                normalized = _normalized_relative_path(pattern)
                if normalized.casefold() in seen_patterns:
                    errors.append(f"duplicate exclusion pattern: {normalized}")
                seen_patterns.add(normalized.casefold())
                _glob_regex(normalized)
                if normalized.casefold() not in ALLOWED_EXCLUSION_PATTERNS:
                    errors.append(f"exclusion pattern is not allowlisted: {normalized}")
            except ValueError as exc:
                errors.append(str(exc))
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"inventory.exclusions[{index}].reason must be non-empty")


def _validate_inventory(inventory: Any, errors: list[str]) -> bool:
    if not isinstance(inventory, Mapping):
        errors.append("inventory must be an object")
        return False
    _validate_inventory_paths(inventory, "product_roots", errors)
    _validate_inventory_paths(inventory, "coverage_sources", errors)
    _validate_exclusions(inventory, errors)
    return True


def _validate_groups(groups: Any, errors: list[str]) -> None:
    if not isinstance(groups, list) or not groups:
        errors.append("groups must be a non-empty list")
        return
    names: list[str] = []
    for index, raw in enumerate(groups):
        if not isinstance(raw, Mapping):
            errors.append(f"groups[{index}] must be an object")
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"groups[{index}].name must be non-empty")
            continue
        names.append(name)
        patterns = raw.get("patterns")
        if not isinstance(patterns, list) or not patterns:
            errors.append(f"groups[{index}].patterns must be a non-empty list")
        else:
            seen_patterns: set[str] = set()
            for pattern_index, pattern in enumerate(patterns):
                if not isinstance(pattern, str):
                    errors.append(f"groups[{index}].patterns[{pattern_index}] must be a string")
                    continue
                try:
                    normalized = _normalized_relative_path(pattern)
                    if normalized.casefold() in seen_patterns:
                        errors.append(f"group {name!r} contains duplicate pattern: {normalized}")
                    seen_patterns.add(normalized.casefold())
                    _glob_regex(normalized)
                except ValueError as exc:
                    errors.append(str(exc))
        floors = _validate_metric_policy(raw.get("floors"), field=f"groups[{index}].floors", errors=errors)
        targets = _validate_metric_policy(
            raw.get("next_targets"),
            field=f"groups[{index}].next_targets",
            errors=errors,
        )
        for metric in METRIC_NAMES:
            if metric in floors and metric in targets and targets[metric] < floors[metric]:
                errors.append(f"group {name!r} next_targets.{metric} is below its floor")

    folded_names = [name.casefold() for name in names]
    if len(set(folded_names)) != len(folded_names):
        errors.append("group names must be unique case-insensitively")
    observed_groups = set(folded_names)
    missing_groups = _sorted_strings(REQUIRED_GROUPS - observed_groups)
    if missing_groups:
        errors.append("missing required groups: " + ", ".join(missing_groups))
    unexpected_groups = _sorted_strings(observed_groups - REQUIRED_GROUPS)
    if unexpected_groups:
        errors.append("unexpected groups: " + ", ".join(unexpected_groups))


def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    enforce_floor_ratchets: bool = True,
) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("schema_version must equal 1")
    if not _validate_inventory(manifest.get("inventory"), errors):
        return errors
    _validate_groups(manifest.get("groups"), errors)
    overall = manifest.get("overall")
    if not isinstance(overall, Mapping):
        errors.append("overall must be an object")
    else:
        floors = _validate_metric_policy(overall.get("floors"), field="overall.floors", errors=errors)
        targets = _validate_metric_policy(
            overall.get("next_targets"),
            field="overall.next_targets",
            errors=errors,
        )
        for metric in METRIC_NAMES:
            if metric in floors and metric in targets and targets[metric] < floors[metric]:
                errors.append(f"overall.next_targets.{metric} is below its floor")
    if enforce_floor_ratchets:
        _validate_floor_ratchets(manifest, errors)
    return errors


def coverage_sources_from_manifest(path: Path = DEFAULT_MANIFEST) -> tuple[str, ...]:
    manifest = load_json_object(path, label="coverage grouping manifest")
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError("; ".join(errors))
    inventory = manifest["inventory"]
    return tuple(_normalized_relative_path(value) for value in inventory["coverage_sources"])


def collect_tracked_product_files(
    *,
    root: Path,
    product_roots: Sequence[str],
) -> list[str]:
    roots = {value.casefold() for value in product_roots}
    paths: list[str] = []
    commands = (
        ("tracked", ["git", "ls-files", "-z", "--", "*.py"]),
        (
            "untracked",
            ["git", "ls-files", "--others", "--exclude-standard", "-z", "--", "*.py"],
        ),
    )
    for inventory_kind, command in commands:
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            detail = stderr or f"rc={completed.returncode}"
            raise RuntimeError(f"git ls-files {inventory_kind} inventory failed: {detail}")
        for raw in completed.stdout.split(b"\0"):
            if not raw:
                continue
            path = _normalized_relative_path(raw.decode("utf-8", errors="surrogateescape"))
            if (
                path.split("/", 1)[0].casefold() in roots
                and (root / Path(path)).is_file()
            ):
                paths.append(path)
    return sorted(set(paths), key=lambda value: (value.casefold(), value))


def _record_exclusion(
    path: str,
    exclusions: Sequence[Mapping[str, Any]],
    exclusion_hits: list[int],
    excluded_records: list[dict[str, Any]],
    violations: list[dict[str, Any]],
) -> bool:
    matched = [index for index, item in enumerate(exclusions) if _matches(path, str(item["pattern"]))]
    if not matched:
        return False
    if len(matched) > 1:
        violations.append({"kind": "multiple_exclusions", "path": path})
    for index in matched:
        exclusion_hits[index] += 1
    if path.casefold().startswith("strategies/") or (
        path.casefold().startswith("strategy_library/")
        and not path.casefold().startswith("strategy_library/generated/")
    ):
        violations.append({"kind": "protected_strategy_exclusion", "path": path})
    selected = exclusions[matched[0]]
    excluded_records.append(
        {
            "path": path,
            "pattern": str(selected["pattern"]),
            "reason": str(selected["reason"]).strip(),
        }
    )
    return True


def _matching_groups(
    path: str,
    groups: Sequence[Mapping[str, Any]],
    group_pattern_hits: list[list[int]],
) -> list[str]:
    matched_groups: list[str] = []
    for group_index, group in enumerate(groups):
        matches = [
            pattern_index
            for pattern_index, pattern in enumerate(group["patterns"])
            if _matches(path, str(pattern))
        ]
        for pattern_index in matches:
            group_pattern_hits[group_index][pattern_index] += 1
        if matches:
            matched_groups.append(str(group["name"]))
    return matched_groups


def _append_stale_policy_violations(
    exclusions: Sequence[Mapping[str, Any]],
    groups: Sequence[Mapping[str, Any]],
    exclusion_hits: Sequence[int],
    group_pattern_hits: Sequence[Sequence[int]],
    violations: list[dict[str, Any]],
) -> None:
    for index, exclusion in enumerate(exclusions):
        if exclusion_hits[index] == 0:
            violations.append({"kind": "stale_exclusion", "pattern": str(exclusion["pattern"])})
    for group_index, group in enumerate(groups):
        for pattern_index, pattern in enumerate(group["patterns"]):
            if group_pattern_hits[group_index][pattern_index] == 0:
                violations.append(
                    {
                        "kind": "stale_group_pattern",
                        "group": str(group["name"]),
                        "pattern": str(pattern),
                    }
                )


def _classification(
    manifest: Mapping[str, Any],
    tracked_paths: Sequence[str],
) -> tuple[dict[str, list[str]], list[dict[str, Any]], list[dict[str, Any]]]:
    inventory = manifest["inventory"]
    exclusions = list(inventory["exclusions"])
    groups = list(manifest["groups"])
    grouped = {str(group["name"]): [] for group in groups}
    excluded_records: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    exclusion_hits = [0 for _ in exclusions]
    group_pattern_hits = [[0 for _ in group["patterns"]] for group in groups]

    for path in _sorted_strings(tracked_paths):
        if _record_exclusion(path, exclusions, exclusion_hits, excluded_records, violations):
            continue
        matched_groups = _matching_groups(path, groups, group_pattern_hits)
        if not matched_groups:
            violations.append({"kind": "ungrouped_product_file", "path": path})
        elif len(matched_groups) > 1:
            violations.append(
                {"kind": "multiply_grouped_product_file", "path": path, "groups": matched_groups}
            )
        else:
            grouped[matched_groups[0]].append(path)

    _append_stale_policy_violations(
        exclusions,
        groups,
        exclusion_hits,
        group_pattern_hits,
        violations,
    )
    for paths in grouped.values():
        paths.sort(key=str.casefold)
    excluded_records.sort(key=lambda item: str(item["path"]).casefold())
    return grouped, excluded_records, violations
