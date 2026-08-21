from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from flask import Response, jsonify


def library_json_response(
    name: object,
    *,
    valid_name: Callable[[object], bool],
    loader: Callable[[str], Any],
    missing_error: str,
):
    if not valid_name(name):
        return jsonify({"error": "invalid name"}), 400
    data = loader(str(name))
    if data is None:
        return jsonify({"error": missing_error}), 404
    return jsonify(data)


def library_export_response(
    export_format: object,
    *,
    build_csv: Callable[[], str],
    build_json: Callable[[], Any],
):
    normalized = str(export_format or "json").strip().lower()
    if normalized == "csv":
        return Response(
            build_csv(),
            mimetype="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": "attachment; filename=strategy_library_catalog.csv",
            },
        )
    if normalized == "json":
        return jsonify(build_json())
    return jsonify({"error": "unsupported format"}), 400


def library_report_response(
    name: object,
    *,
    valid_name: Callable[[object], bool],
    loader: Callable[[str], str | None],
    response_format: object,
):
    if not valid_name(name):
        return jsonify({"error": "invalid name"}), 400
    content = loader(str(name))
    if content is None:
        return jsonify({"error": "not found"}), 404
    if str(response_format or "").strip().lower() == "json":
        return jsonify({"content": content})
    return Response(
        content,
        mimetype="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f"inline; filename={name}_strategy_report.md",
        },
    )


def library_config_response(
    name: object,
    *,
    valid_name: Callable[[object], bool],
    library_root: Path,
    redact: Callable[[str], str],
):
    if not valid_name(name):
        return jsonify({"error": "invalid name"}), 400
    config_path = library_root / str(name) / "config.ini"
    if not config_path.is_file():
        return jsonify({"error": "not found"}), 404
    try:
        return jsonify({"content": redact(config_path.read_text(encoding="utf-8"))})
    except Exception:
        return jsonify({"error": "read error"}), 500


__all__ = [
    "library_config_response",
    "library_export_response",
    "library_json_response",
    "library_report_response",
]
