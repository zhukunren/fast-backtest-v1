from __future__ import annotations

import hashlib
import platform
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from subprocess import run as _subprocess_run
from typing import Any
from uuid import RFC_4122, UUID

ROOT = Path(__file__).resolve().parents[1]
_GATE_TMP_UUID_RE = re.compile(r"^[0-9a-f]{32}$", flags=re.IGNORECASE)
_PYTHON_EXECUTABLE_RE = re.compile(
    r"^python(?:\d+(?:\.\d+)*)?(?:\.exe)?$",
    flags=re.IGNORECASE,
)
_PYTHON_IDENTITY_RE = re.compile(
    r"^python-sha256:(?P<sha256>[0-9a-f]{64}):(?P<size>[1-9][0-9]*)$"
)
_GATE_TMP_SCHEME = "gate-tmp://"
_WINDOWS_HOST_PATH_RE = re.compile(r"(?i)(?:^|[^A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\)")
_FORWARD_UNC_HOST_PATH_RE = re.compile(r"(?:^|[^A-Za-z0-9:/])//[^/]")
_POSIX_HOST_PATH_RE = re.compile(r"(?:^|[^A-Za-z0-9/])/(?!/)")
_FILE_URI_RE = re.compile(r"(?i)(?:^|[^A-Za-z0-9])file:/+")


@dataclass(frozen=True, slots=True)
class GateCommand:
    name: str
    command: tuple[str, ...]
    required: bool = True
    env: dict[str, str] | None = None
    cwd: str | None = None

    @property
    def display(self) -> str:
        return " ".join(self.command)


class _GitCommandError(RuntimeError):
    pass


def _git_bytes(*args: str) -> bytes:
    command = ["git", *args]
    try:
        completed = _subprocess_run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise _GitCommandError(f"{command!r} failed: {type(exc).__name__}: {exc}") from exc
    if completed.returncode != 0:
        raw_stderr = completed.stderr or b""
        stderr = (
            raw_stderr.decode("utf-8", errors="replace")
            if isinstance(raw_stderr, bytes)
            else str(raw_stderr)
        ).strip()
        detail = f": {stderr[:500]}" if stderr else ""
        raise _GitCommandError(f"{command!r} returned {completed.returncode}{detail}")
    raw_stdout = completed.stdout or b""
    return raw_stdout if isinstance(raw_stdout, bytes) else str(raw_stdout).encode("utf-8")


def _git_output(*args: str) -> str:
    # Remove only Git's record terminator; porcelain's leading status columns are evidence.
    return _git_bytes(*args).decode("utf-8", errors="replace").rstrip("\r\n")


def _worktree_sha256(diff: bytes, raw_untracked: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(diff)
    untracked = sorted(
        (
            item.decode("utf-8", errors="surrogateescape")
            for item in raw_untracked.split(b"\0")
            if item
        ),
        key=str.casefold,
    )
    for relative in untracked:
        digest.update(b"\0untracked\0")
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        try:
            digest.update((ROOT / relative).read_bytes())
        except OSError as exc:
            raise _GitCommandError(
                f"cannot hash untracked file {relative!r}: {type(exc).__name__}: {exc}"
            ) from exc
    return digest.hexdigest()


def _dirty_worktree_sha256() -> str:
    diff = _git_bytes("diff", "--binary", "--no-ext-diff", "--no-textconv", "HEAD", "--")
    raw_untracked = _git_bytes("ls-files", "--others", "--exclude-standard", "-z")
    return _worktree_sha256(diff, raw_untracked)


def _sha256_and_size(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _python_identity() -> dict[str, Any]:
    resolved = Path(sys.executable).resolve(strict=True)
    if not resolved.is_file():
        raise OSError("Python executable is not a regular file")
    executable_sha256, executable_size_bytes = _sha256_and_size(resolved)
    if executable_size_bytes <= 0:
        raise OSError("Python executable is empty")
    token = f"python-sha256:{executable_sha256}:{executable_size_bytes}"
    return {
        "executable": token,
        "executable_sha256": executable_sha256,
        "executable_size_bytes": executable_size_bytes,
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
    }


def _failed_python_identity(exc: Exception) -> dict[str, Any]:
    return {
        "executable": "",
        "executable_sha256": "",
        "executable_size_bytes": 0,
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "error": f"{type(exc).__name__}: Python executable fingerprint failed",
    }


def _failed_run_identity(
    exc: Exception,
    *,
    python_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if python_identity is None:
        try:
            python_identity = _python_identity()
        except OSError as python_exc:
            python_identity = _failed_python_identity(python_exc)
    return {
        "git": {
            "collection_ok": False,
            "commit": "",
            "branch": "",
            "dirty": None,
            "status_sha256": "",
            "worktree_sha256": "",
            "error": f"{type(exc).__name__}: {exc}",
        },
        "python": dict(python_identity),
        "platform": platform.platform(),
    }


def collect_run_identity() -> dict[str, Any]:
    try:
        python_identity = _python_identity()
    except OSError as exc:
        return _failed_run_identity(exc, python_identity=_failed_python_identity(exc))
    try:
        commit = _git_output("rev-parse", "HEAD")
        branch = _git_output("rev-parse", "--abbrev-ref", "HEAD")
        status = _git_output("status", "--porcelain=v1", "--untracked-files=all")
        diff = _git_bytes(
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        )
        raw_untracked = _git_bytes("ls-files", "--others", "--exclude-standard", "-z")
        if not commit or not branch:
            raise _GitCommandError("Git commit and branch must be non-empty")
        dirty = bool(status)
        if not dirty and (diff or raw_untracked):
            raise _GitCommandError("Git status disagrees with diff or untracked files")
        worktree_sha256 = _worktree_sha256(diff, raw_untracked) if dirty else ""
    except _GitCommandError as exc:
        return _failed_run_identity(exc, python_identity=python_identity)
    return {
        "git": {
            "collection_ok": True,
            "commit": commit,
            "branch": branch,
            "dirty": dirty,
            "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest() if status else "",
            "worktree_sha256": worktree_sha256,
        },
        "python": python_identity,
        "platform": platform.platform(),
    }


def git_identity_shape_error(identity: Mapping[str, Any]) -> str:
    if identity.get("collection_ok") is not True:
        return "collection_ok must be true"
    for key in ("commit", "branch"):
        if not isinstance(identity.get(key), str) or not identity[key]:
            return f"{key} must be a non-empty string"
    dirty = identity.get("dirty")
    if type(dirty) is not bool:
        return "dirty must be a boolean"
    hashes = (identity.get("status_sha256"), identity.get("worktree_sha256"))
    if dirty and not all(isinstance(value, str) and value for value in hashes):
        return "dirty identities must have non-empty status and worktree hashes"
    if not dirty and hashes != ("", ""):
        return "clean identities must have empty status and worktree hashes"
    if "error" in identity:
        return "error must be absent after successful collection"
    return ""


def identity_collection_ok(identity: Mapping[str, Any]) -> bool:
    git_identity = identity.get("git")
    python_identity = identity.get("python")
    if not isinstance(git_identity, Mapping) or not isinstance(python_identity, Mapping):
        return False
    _normalized, python_error = validate_python_identity(python_identity)
    return not git_identity_shape_error(git_identity) and not python_error


def json_out_preflight_error(path: Path) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(ROOT.resolve())
    except ValueError:
        return ""
    if relative == Path("."):
        return "--json-out must name a file, not the repository root"
    try:
        completed = _subprocess_run(
            ["git", "check-ignore", "--quiet", "--", relative.as_posix()],
            cwd=str(ROOT),
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return f"cannot verify whether --json-out is ignored: {type(exc).__name__}: {exc}"
    if completed.returncode == 0:
        return ""
    if completed.returncode == 1:
        return (
            "--json-out inside the repository must be Git-ignored before the gate starts: "
            f"{relative.as_posix()}"
        )
    raw_stderr = completed.stderr or b""
    stderr = (
        raw_stderr.decode("utf-8", errors="replace")
        if isinstance(raw_stderr, bytes)
        else str(raw_stderr)
    ).strip()
    detail = f": {stderr[:500]}" if stderr else ""
    return f"git check-ignore failed with return code {completed.returncode}{detail}"


def _is_absolute_cross_platform_path(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def _is_canonical_uuid4_hex(value: str) -> bool:
    if not _GATE_TMP_UUID_RE.fullmatch(value):
        return False
    parsed = UUID(hex=value)
    return parsed.hex == value.casefold() and parsed.version == 4 and parsed.variant == RFC_4122


def parse_gate_tmp_arg(value: str) -> tuple[str, str, str] | None:
    embedded_prefix = "--cov-report=json:"
    if value.startswith(embedded_prefix):
        prefix = embedded_prefix
        raw_path_value = value[len(prefix) :]
    else:
        prefix = ""
        raw_path_value = value

    if raw_path_value.startswith(_GATE_TMP_SCHEME):
        if "\\" in raw_path_value:
            return None
        remainder = raw_path_value.removeprefix(_GATE_TMP_SCHEME)
        run_id, separator, suffix = remainder.partition("/")
        if not _is_canonical_uuid4_hex(run_id):
            return None
        if separator and (not suffix or any(part in {"", ".", ".."} for part in suffix.split("/"))):
            return None
        normalized_suffix = f"/{suffix}" if separator else ""
        return prefix, normalized_suffix, f"{_GATE_TMP_SCHEME}{run_id.casefold()}"

    path_value = raw_path_value.replace("\\", "/")
    marker = "/_pytest_tmp/full_quality_gate/"
    if path_value.count(marker) != 1:
        return None
    root, remainder = path_value.split(marker, maxsplit=1)
    run_id, separator, suffix = remainder.partition("/")
    candidate_root = f"{root}{marker}{run_id}"
    if not _is_canonical_uuid4_hex(run_id) or not _is_absolute_cross_platform_path(candidate_root):
        return None
    if any(part in {".", ".."} for part in candidate_root.split("/")):
        return None
    if separator and (not suffix or any(part in {"", ".", ".."} for part in suffix.split("/"))):
        return None
    normalized_suffix = f"/{suffix}" if separator else ""
    return prefix, normalized_suffix, f"{_GATE_TMP_SCHEME}{run_id.casefold()}"


def portable_gate_tmp_arg(value: str) -> str:
    parsed = parse_gate_tmp_arg(value)
    if parsed is None:
        return value
    prefix, suffix, identity = parsed
    return f"{prefix}{identity}{suffix}"


def _normalize_gate_tmp_arg(value: str) -> str:
    parsed = parse_gate_tmp_arg(value)
    if parsed is None:
        return value.replace("\\", "/")
    prefix, suffix, _identity = parsed
    return f"{prefix}<gate-tmp>{suffix}"


def _executable_basename(value: str) -> str:
    return value.replace("\\", "/").rsplit("/", maxsplit=1)[-1]


def is_python_executable(value: str) -> bool:
    if _PYTHON_EXECUTABLE_RE.fullmatch(_executable_basename(value)) or python_executable_identity(value):
        return True
    try:
        return Path(value).resolve() == Path(sys.executable).resolve()
    except (OSError, RuntimeError):
        return False


def portable_command(command: tuple[str, ...], *, python_token: str) -> list[str]:
    if command and is_python_executable(command[0]):
        if not python_executable_identity(python_token):
            raise ValueError("python_token must be a canonical portable Python identity")
        first = python_token
    else:
        first = command[0] if command else ""
    return [
        first if index == 0 else portable_gate_tmp_arg(value)
        for index, value in enumerate(command)
    ]


def _normalized_command(
    command: list[str],
    *,
    expected: tuple[str, ...],
) -> tuple[str, ...]:
    normalized: list[str] = []
    for index, value in enumerate(command):
        expected_value = expected[index] if index < len(expected) else ""
        if index == 0 and is_python_executable(expected_value):
            if is_python_executable(value):
                normalized.append("<python>")
                continue
        if index == 0 and expected_value.casefold() in {"npm", "npm.cmd"}:
            if value.casefold() in {"npm", "npm.cmd"}:
                normalized.append("<npm>")
                continue
        normalized.append(_normalize_gate_tmp_arg(value))
    return tuple(normalized)


def commands_match(raw_command: Any, expected: tuple[str, ...]) -> bool:
    if not isinstance(raw_command, list) or not all(isinstance(value, str) for value in raw_command):
        return False
    if len(raw_command) != len(expected):
        return False
    expected_command = _normalized_command(list(expected), expected=expected)
    observed_command = _normalized_command(raw_command, expected=expected)
    return observed_command == expected_command


def python_executable_identity(value: str) -> str:
    match = _PYTHON_IDENTITY_RE.fullmatch(value)
    if match is None:
        return ""
    return f"python-sha256:{match.group('sha256')}:{int(match.group('size'))}"


def validate_python_identity(identity: Mapping[str, Any]) -> tuple[str, str]:
    executable = identity.get("executable")
    if not isinstance(executable, str) or not executable:
        return "", "missing_executable"
    normalized = python_executable_identity(executable)
    if not normalized:
        return "", "invalid_executable"
    _, executable_sha256, executable_size = normalized.split(":", maxsplit=2)
    if identity.get("executable_sha256") != executable_sha256:
        return "", "sha256_mismatch"
    size = identity.get("executable_size_bytes")
    if type(size) is not int or size != int(executable_size):
        return "", "size_mismatch"
    return normalized, ""


def host_path_locations(value: Any, *, location: str = "report") -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        if any(
            pattern.search(value)
            for pattern in (
                _WINDOWS_HOST_PATH_RE,
                _FORWARD_UNC_HOST_PATH_RE,
                _POSIX_HOST_PATH_RE,
                _FILE_URI_RE,
            )
        ):
            found.append(location)
    elif isinstance(value, Mapping):
        for index, (key, item) in enumerate(value.items()):
            found.extend(host_path_locations(key, location=f"{location}.keys[{index}]"))
            found.extend(host_path_locations(item, location=f"{location}.values[{index}]"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(host_path_locations(item, location=f"{location}[{index}]"))
    return found


__all__ = [
    "GateCommand",
    "collect_run_identity",
    "commands_match",
    "git_identity_shape_error",
    "host_path_locations",
    "identity_collection_ok",
    "is_python_executable",
    "json_out_preflight_error",
    "parse_gate_tmp_arg",
    "portable_command",
    "portable_gate_tmp_arg",
    "python_executable_identity",
    "validate_python_identity",
]
