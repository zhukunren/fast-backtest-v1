"""Mutual-exclusion responses shared by backtest and maintenance routes."""
from __future__ import annotations

from flask import jsonify

from web.log_service import log_event
from web.maintenance_service import get_maintenance_progress, is_maintenance_running
from web.task_states import get_backtest_progress, is_backtest_running


def reject_backtest_submission_if_busy(*, backtest_running: bool | None = None):
    if is_maintenance_running():
        log_event(
            "WARNING",
            "backtest_api",
            "Backtest run rejected because maintenance is running",
        )
        return jsonify({
            "error": "maintenance_running",
            "progress": get_maintenance_progress(),
        }), 409
    if is_backtest_running() if backtest_running is None else backtest_running:
        log_event(
            "WARNING",
            "backtest_api",
            "Backtest run rejected because another task is running",
        )
        return jsonify({"error": "backtest_running", "progress": get_backtest_progress()}), 409
    return None


def reject_maintenance_submission_if_busy():
    if is_backtest_running():
        return jsonify({"error": "backtest_running", "progress": get_backtest_progress()}), 409
    if is_maintenance_running():
        return jsonify({
            "error": "maintenance_running",
            "progress": get_maintenance_progress(),
        }), 409
    return None


__all__ = [
    "reject_backtest_submission_if_busy",
    "reject_maintenance_submission_if_busy",
]
