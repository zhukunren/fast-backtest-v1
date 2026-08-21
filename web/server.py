"""
Flask Web 服务 — 策略库回测展示系统。

Usage:
    python -m web
    python web/server.py
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path
from time import perf_counter

from flask import Flask, g, jsonify, render_template, request, send_from_directory, url_for

_project_root = Path(__file__).resolve().parent.parent
_web_root = Path(__file__).resolve().parent
_frontend_root = _web_root / "frontend"
_frontend_dist_root = _frontend_root / "dist"
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from web.admin_routes import admin_api
from web.api_request_support import (
    normalize_client_log_event,
    parse_bounded_int,
    parse_compare_names,
)
from web.backtest_request import (
    BacktestRequestError,
    parse_backtest_submission,
)
from web.frontend_entry import resolve_vue_entry
from web.insights.health import get_library_health
from web.insights.holding_review import compute_library_holding_trade_review
from web.insights.metrics import compute_derived_metrics
from web.insights.strategy_analysis import (
    compute_enhanced_compare,
    compute_library_market_state_analysis,
    compute_library_strategy_analysis,
)
from web.library_catalog import (
    get_library_catalog,
    get_library_catalog_summary,
    get_library_detail,
)
from web.library_curves import (
    get_library_daily_holdings,
    get_library_net_value,
    get_library_trades,
)
from web.library_metadata import LIBRARY_ROOT, _parse_strategy_config
from web.library_reports import (
    build_library_catalog_csv,
    build_library_export_payload,
    build_strategy_markdown_report,
)
from web.library_summary_views import (
    get_library_daily_holdings_summary,
    get_library_trades_summary,
)
from web.library_runtime import clear_library_cache, start_library_backtest
from web.task_states import get_backtest_progress, is_backtest_running
from web.log_service import get_log_dir, list_log_files, log_event, read_log_tail
from web.library_route_support import (
    library_config_response,
    library_export_response,
    library_json_response,
    library_report_response,
)
from web.request_security import (
    SENSITIVE_API_ENDPOINTS,
    apply_security_response_headers,
    authorize_sensitive_request,
    redact_ini_secrets,
)
from web.request_lifecycle import (
    configure_cors,
    log_api_error_response,
    log_unhandled_request_exception,
)
from web.response_compression import compress_response
from web.server_runtime import serve_web_app
from web.run_event_service import list_run_event_files, read_run_events_for_web
from web.task_gate import reject_backtest_submission_if_busy

app = Flask(
    __name__,
    template_folder=str(Path(__file__).resolve().parent / "templates"),
)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024
app.register_blueprint(admin_api)
app.after_request(apply_security_response_headers)


configure_cors(app, os.environ.get("WEB_CORS_ORIGINS"))


@app.after_request
def _compress_browser_response(response):
    if request.blueprint == "admin_api" or request.endpoint in SENSITIVE_API_ENDPOINTS:
        return response
    return compress_response(response, request=request)


_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _valid_name(name: object) -> bool:
    return isinstance(name, str) and bool(_SAFE_NAME.fullmatch(name))


def _library_name_set() -> set[str]:
    catalog = get_library_catalog()
    items = catalog.get("items") if isinstance(catalog, dict) else []
    if not isinstance(items, list):
        return set()
    return {
        str(item.get("name") or "")
        for item in items
        if isinstance(item, dict) and str(item.get("name") or "")
    }


@app.before_request
def _track_request_start() -> None:
    request.environ["web.request_start"] = perf_counter()


@app.before_request
def _authorize_sensitive_api_request():
    if request.endpoint in SENSITIVE_API_ENDPOINTS:
        return authorize_sensitive_request()
    return None


@app.after_request
def _log_api_error_response(response):
    return log_api_error_response(response, request=request, event_logger=log_event)


@app.teardown_request
def _log_unhandled_request_exception(exc: BaseException | None) -> None:
    log_unhandled_request_exception(exc, request=request, event_logger=log_event)


def _strategy_dir(name: str) -> Path:
    return LIBRARY_ROOT / name


def _frontend_dev_server_url() -> str:
    return str(os.environ.get("WEB_VUE_DEV_SERVER") or "").strip().rstrip("/")


def _resolve_vue_entry() -> dict[str, object] | None:
    return resolve_vue_entry(
        frontend_dist_root=_frontend_dist_root,
        dev_server=_frontend_dev_server_url(),
        asset_url=lambda filename: url_for("frontend_asset", filename=filename),
    )


def _render_frontend_entry():
    bundle = _resolve_vue_entry()
    if bundle is None:
        g.web_frontend_mode = "prod"
        return "frontend build not found", 503
    g.web_frontend_mode = str(bundle.get("mode") or "prod")
    return render_template("index.html", frontend_bundle=bundle)


@app.route("/")
def index():
    return _render_frontend_entry()


@app.route("/strategy/<name>")
@app.route("/strategy/<name>/")
def strategy_detail(name: str):
    if not _valid_name(name):
        return "invalid name", 400
    return _render_frontend_entry()


@app.route("/compare")
@app.route("/compare/")
@app.route("/admin")
@app.route("/admin/")
def compare_page():
    return _render_frontend_entry()


@app.route("/__frontend/<path:filename>")
def frontend_asset(filename: str):
    if not _frontend_dist_root.is_dir():
        return "frontend build not found", 404
    is_hashed_asset = filename.startswith("assets/")
    response = send_from_directory(
        _frontend_dist_root,
        filename,
        max_age=31_536_000 if is_hashed_asset else None,
    )
    if is_hashed_asset:
        response.cache_control.public = True
        response.cache_control.max_age = 31_536_000
        response.cache_control.immutable = True

    # send_file responses use direct passthrough by default. Materializing these
    # bounded build artifacts lets the shared response compressor handle them.
    response.direct_passthrough = False
    return response


# ---- 策略库列表 ----

@app.route("/api/library")
def api_library():
    response = jsonify(get_library_catalog_summary())
    response.set_etag(hashlib.sha256(response.get_data()).hexdigest())
    response.cache_control.private = True
    response.cache_control.max_age = 0
    response.cache_control.must_revalidate = True
    return response


@app.route("/api/library/health")
def api_library_health():
    return jsonify(get_library_health())


@app.route("/api/library/export")
def api_library_export():
    return library_export_response(
        request.args.get("format"),
        build_csv=build_library_catalog_csv,
        build_json=build_library_export_payload,
    )


# ---- 单策略数据 ----

@app.route("/api/library/<name>/detail")
def api_library_detail(name: str):
    return library_json_response(
        name, valid_name=_valid_name, loader=get_library_detail, missing_error="not found"
    )


@app.route("/api/library/<name>/net_value")
def api_library_net_value(name: str):
    return library_json_response(
        name,
        valid_name=_valid_name,
        loader=get_library_net_value,
        missing_error="no data",
    )


@app.route("/api/library/<name>/trades/records")
def api_library_trade_records(name: str):
    return library_json_response(
        name, valid_name=_valid_name, loader=get_library_trades, missing_error="no data"
    )


@app.route("/api/library/<name>/trades/summary")
def api_library_trades_summary(name: str):
    return library_json_response(
        name,
        valid_name=_valid_name,
        loader=get_library_trades_summary,
        missing_error="no data",
    )


@app.route("/api/library/<name>/holdings/history")
def api_library_holdings_history(name: str):
    return library_json_response(
        name,
        valid_name=_valid_name,
        loader=get_library_daily_holdings,
        missing_error="no data",
    )


@app.route("/api/library/<name>/holdings/summary")
def api_library_holdings_summary(name: str):
    return library_json_response(
        name,
        valid_name=_valid_name,
        loader=get_library_daily_holdings_summary,
        missing_error="no data",
    )


@app.route("/api/library/<name>/metrics")
def api_library_metrics(name: str):
    return library_json_response(
        name, valid_name=_valid_name, loader=compute_derived_metrics, missing_error="no data"
    )


@app.route("/api/library/<name>/analysis")
def api_library_analysis(name: str):
    return library_json_response(
        name,
        valid_name=_valid_name,
        loader=compute_library_strategy_analysis,
        missing_error="no data",
    )


@app.route("/api/library/<name>/market_states")
def api_library_market_states(name: str):
    return library_json_response(
        name,
        valid_name=_valid_name,
        loader=compute_library_market_state_analysis,
        missing_error="no data",
    )


@app.route("/api/library/<name>/review")
def api_library_holding_trade_review(name: str):
    return library_json_response(
        name,
        valid_name=_valid_name,
        loader=compute_library_holding_trade_review,
        missing_error="no data",
    )


@app.route("/api/library/<name>/report")
def api_library_report(name: str):
    return library_report_response(
        name,
        valid_name=_valid_name,
        loader=build_strategy_markdown_report,
        response_format=request.args.get("format"),
    )


@app.route("/api/library/<name>/cache", methods=["DELETE"])
def api_library_clear_cache(name: str):
    if not _valid_name(name):
        return jsonify({"error": "invalid name"}), 400
    ok = clear_library_cache(name)
    return jsonify({"cleared": ok})


@app.route("/api/library/<name>/config")
def api_library_config_ini(name: str):
    """返回策略目录下的 config.ini 原文。"""
    return library_config_response(
        name,
        valid_name=_valid_name,
        library_root=LIBRARY_ROOT,
        redact=redact_ini_secrets,
    )


# ---- 日志服务 ----

@app.route("/api/logs")
def api_logs():
    try:
        limit = parse_bounded_int(request.args.get("limit", "50"), label="limit")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    files = list_log_files(limit=limit)
    latest = files[-1]["name"] if files else ""
    return jsonify({
        "log_dir": str(get_log_dir()),
        "files": files,
        "latest": latest,
    })


@app.route("/api/logs/tail")
def api_logs_tail():
    try:
        lines = parse_bounded_int(request.args.get("lines", "200"), label="lines")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    filename = str(request.args.get("file") or "").strip() or None
    try:
        payload = read_log_tail(filename, lines=lines)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError:
        return jsonify({"error": "log file not found"}), 404
    return jsonify(payload)


@app.route("/api/logs/client", methods=["POST"])
def api_logs_client():
    data = request.get_json(silent=True) or {}
    try:
        message, level, event, page, fields = normalize_client_log_event(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    log_event(
        level,
        "client",
        message,
        event=event,
        page=page,
        fields=fields,
    )
    return jsonify({"ok": True})


@app.route("/api/run_events")
def api_run_events():
    try:
        limit = parse_bounded_int(request.args.get("limit", "50"), label="limit")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    files = list_run_event_files(limit=limit)
    latest = files[-1]["run_id"] if files else ""
    return jsonify({"files": files, "latest": latest})


@app.route("/api/run_events/<run_id>")
def api_run_events_detail(run_id: str):
    try:
        lines = parse_bounded_int(request.args.get("lines", "200"), label="lines")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        payload = read_run_events_for_web(run_id, lines=lines)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError:
        return jsonify({"error": "run events not found"}), 404
    return jsonify(payload)


@app.route("/api/backtest/run", methods=["POST"])
def api_backtest_run():
    busy_response = reject_backtest_submission_if_busy(backtest_running=is_backtest_running())
    if busy_response is not None:
        return busy_response

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "payload must be a JSON object"}), 400
    try:
        names, date_overrides = parse_backtest_submission(
            data,
            valid_name=_valid_name,
            known_names=_library_name_set,
            strategy_config=lambda name: _parse_strategy_config(
                _strategy_dir(name) / "config.ini"
            ),
        )
    except BacktestRequestError as exc:
        return jsonify({"error": str(exc)}), exc.status_code

    ok = start_library_backtest(names, date_overrides=date_overrides)
    if not ok:
        log_event(
            "WARNING",
            "backtest_api",
            "Backtest run rejected",
            names=names if names is not None else "all",
            date_override_count=len(date_overrides or {}),
        )
        return jsonify({"error": "no strategies or already running"}), 400
    log_event(
        "INFO",
        "backtest_api",
        "Backtest run accepted",
        names=names if names is not None else "all",
        strategy_count=len(names) if names is not None else None,
        date_override_count=len(date_overrides or {}),
    )
    return jsonify({"status": "started", "progress": get_backtest_progress()})


@app.route("/api/backtest/status")
def api_backtest_status():
    return jsonify(get_backtest_progress())


# ---- Multi-strategy comparison ----


def _comparison_api_response(payload: dict[str, object] | None):
    if not isinstance(payload, dict):
        return jsonify({"error": "no data"}), 404
    usable_names = payload.get("names")
    if not isinstance(usable_names, list) or len(usable_names) < 2:
        error_payload = dict(payload)
        error_payload["error"] = "at least 2 valid curves required"
        return jsonify(error_payload), 404
    return jsonify(payload)


@app.route("/api/compare/enhanced")
def api_compare_enhanced():
    try:
        names = parse_compare_names(request.args.get("names", ""), valid_name=_valid_name)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    data = compute_enhanced_compare(names)
    return _comparison_api_response(data)


def main():
    serve_web_app(app, event_logger=log_event)


if __name__ == "__main__":
    main()
