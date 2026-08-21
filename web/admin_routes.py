"""Authenticated Flask routes for the bounded Web maintenance console."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from web.log_service import log_event
from web.maintenance_service import (
    MaintenanceRequestError,
    get_maintenance_progress,
    get_repository_status,
    start_data_update,
    start_version_update,
)
from web.request_security import authorize_sensitive_request
from web.strategy_admin_service import (
    delete_strategy,
    install_uploaded_strategy,
    rename_strategy,
)
from web.task_gate import reject_maintenance_submission_if_busy


admin_api = Blueprint("admin_api", __name__, url_prefix="/api/admin")


@admin_api.before_request
def _authorize_admin_request():
    return authorize_sensitive_request()


def _maintenance_error_response(exc: MaintenanceRequestError):
    return jsonify({"error": str(exc)}), exc.status_code


@admin_api.get("/status")
def status():
    return jsonify({
        "maintenance": get_maintenance_progress(),
        "repository": get_repository_status(),
    })


@admin_api.get("/task/status")
def task_status():
    return jsonify(get_maintenance_progress())


@admin_api.post("/strategies/upload")
def strategy_upload():
    busy_response = reject_maintenance_submission_if_busy()
    if busy_response is not None:
        return busy_response
    try:
        payload = install_uploaded_strategy(request.files.get("file"), request.form)
    except MaintenanceRequestError as exc:
        return _maintenance_error_response(exc)
    except Exception as exc:
        log_event(
            "ERROR",
            "admin_strategy",
            "Strategy upload failed",
            error=str(exc),
            exception_type=type(exc).__name__,
        )
        return jsonify({"error": "strategy upload failed"}), 500
    return jsonify({"ok": True, "strategy": payload})


@admin_api.delete("/strategies/<name>")
def strategy_delete(name: str):
    busy_response = reject_maintenance_submission_if_busy()
    if busy_response is not None:
        return busy_response
    try:
        payload = delete_strategy(name)
    except MaintenanceRequestError as exc:
        return _maintenance_error_response(exc)
    except Exception as exc:
        log_event(
            "ERROR",
            "admin_strategy",
            "Strategy delete failed",
            name=name,
            error=str(exc),
            exception_type=type(exc).__name__,
        )
        return jsonify({"error": "strategy delete failed"}), 500
    return jsonify({"ok": True, **payload})


@admin_api.patch("/strategies/<name>")
def strategy_rename(name: str):
    busy_response = reject_maintenance_submission_if_busy()
    if busy_response is not None:
        return busy_response
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "payload must be a JSON object"}), 400
    try:
        payload = rename_strategy(
            name,
            new_name=data.get("name", data.get("new_name")),
            title=data.get("title", data.get("display_title")),
        )
    except MaintenanceRequestError as exc:
        return _maintenance_error_response(exc)
    except Exception as exc:
        log_event(
            "ERROR",
            "admin_strategy",
            "Strategy rename failed",
            name=name,
            error=str(exc),
            exception_type=type(exc).__name__,
        )
        return jsonify({"error": "strategy rename failed"}), 500
    return jsonify({"ok": True, "strategy": payload})


@admin_api.post("/data-update/run")
def data_update_run():
    busy_response = reject_maintenance_submission_if_busy()
    if busy_response is not None:
        return busy_response
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "payload must be a JSON object"}), 400
    try:
        started = start_data_update(data)
    except MaintenanceRequestError as exc:
        return _maintenance_error_response(exc)
    except Exception as exc:
        log_event(
            "ERROR",
            "maintenance",
            "Data update start failed",
            error=str(exc),
            exception_type=type(exc).__name__,
        )
        return jsonify({"error": "data update start failed"}), 500
    if not started:
        return jsonify({
            "error": "maintenance_running",
            "progress": get_maintenance_progress(),
        }), 409
    return jsonify({"status": "started", "progress": get_maintenance_progress()})


@admin_api.post("/version-update/run")
def version_update_run():
    busy_response = reject_maintenance_submission_if_busy()
    if busy_response is not None:
        return busy_response
    try:
        started = start_version_update()
    except MaintenanceRequestError as exc:
        return _maintenance_error_response(exc)
    except Exception as exc:
        log_event(
            "ERROR",
            "maintenance",
            "GitHub update start failed",
            error=str(exc),
            exception_type=type(exc).__name__,
        )
        return jsonify({"error": "GitHub update start failed"}), 500
    if not started:
        return jsonify({
            "error": "maintenance_running",
            "progress": get_maintenance_progress(),
        }), 409
    return jsonify({"status": "started", "progress": get_maintenance_progress()})


__all__ = ["admin_api"]
