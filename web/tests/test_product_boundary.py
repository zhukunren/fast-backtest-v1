from __future__ import annotations

import configparser
from pathlib import Path

import pytest

import web.admin_routes as admin_routes
from tests_support.library_owners import library
import web.server as server
from web.request_security import redact_ini_secrets
from web.server import app


ROOT = Path(__file__).resolve().parents[2]
MODERN_CSP = (
    "default-src 'self'; base-uri 'self'; connect-src 'self'; "
    "font-src 'self' data:; form-action 'self'; frame-ancestors 'none'; "
    "img-src 'self' data: blob:; object-src 'none'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; worker-src 'self' blob:"
)


@pytest.mark.integration
@pytest.mark.parametrize(
    "strategy_name",
    (
        "bulk_0316_rsi_reclaim",
        "codex_2024_2025_shock_reclaim_confirm",
        "codex_2024_2026_qualified_reclaim_chase",
        "codex_2024_now_ret400_win55_2x2",
        "codex_chase_dd_control_breakout",
        "codex_chase_momentum_breakout",
        "codex_post_burst_quiet_reclaim",
    ),
)
def test_selected_library_strategies_use_slot_broker(strategy_name: str):
    parser = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#", ";"))
    parser.read(str(library.LIBRARY_ROOT / strategy_name / "config.ini"), encoding="utf-8")

    assert parser.get("portfolio", "engine") == "slot_broker"


def test_deleted_strategies_are_absent_from_product_library():
    deleted = {"bulk_0212_rsi_reclaim", "codex_net_mf_amount_pulse"}
    catalog_names = {item["name"] for item in library.get_library_catalog()["items"]}

    assert deleted.isdisjoint(catalog_names)


def test_maintenance_control_surface_is_registered():
    routes = {rule.rule for rule in app.url_map.iter_rules()}

    assert "/admin" in routes
    assert "/api/admin/data-update/run" in routes
    assert "/api/admin/version-update/run" in routes
    assert "/api/admin/strategies/upload" in routes
    assert "/api/admin/strategies/<name>" in routes
    rename_methods = {
        method
        for rule in app.url_map.iter_rules()
        if rule.rule == "/api/admin/strategies/<name>"
        for method in rule.methods
    }
    assert "PATCH" in rename_methods


def test_product_ui_registers_compare_and_maintenance():
    app_source = (ROOT / "web" / "frontend" / "src" / "App.vue").read_text(encoding="utf-8")

    assert "StrategyComparePage" in app_source
    assert "kind: 'compare'" in app_source
    assert "AdminPage" in app_source
    assert "kind: 'admin'" in app_source


def test_admin_api_requires_configured_token(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_TOKEN", "test-secret")
    client = app.test_client()

    unauthorized = client.get("/api/admin/status")

    assert unauthorized.status_code == 401
    assert unauthorized.get_json()["error"] == "admin_auth_required"


def test_admin_strategy_rename_route_forwards_json_payload(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_TOKEN", "test-secret")
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_rename(name: str, **kwargs):
        calls.append((name, kwargs))
        return {
            "old_name": name,
            "name": kwargs["new_name"],
            "title": kwargs["title"],
            "renamed": True,
        }

    monkeypatch.setattr(admin_routes, "rename_strategy", fake_rename)
    client = app.test_client()

    invalid = client.patch(
        "/api/admin/strategies/alpha",
        headers={"X-Web-Admin-Token": "test-secret"},
        data="not-json",
        content_type="application/json",
    )
    response = client.patch(
        "/api/admin/strategies/alpha",
        headers={"X-Web-Admin-Token": "test-secret"},
        json={"name": "renamed_alpha", "title": "重命名策略"},
    )

    assert invalid.status_code == 400
    assert response.status_code == 200
    assert response.get_json()["strategy"]["name"] == "renamed_alpha"
    assert calls == [("alpha", {"new_name": "renamed_alpha", "title": "重命名策略"})]


def test_web_responses_deny_cross_site_framing_and_sensitive_caching(monkeypatch):
    monkeypatch.delenv("WEB_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("WEB_HOST", raising=False)
    monkeypatch.delenv("WEB_VUE_DEV_SERVER", raising=False)
    monkeypatch.setattr(
        server,
        "_resolve_vue_entry",
        lambda: {
            "mode": "prod",
            "css_urls": [],
            "favicon_url": "/__frontend/favicon.svg",
            "js_url": "/__frontend/main.js",
        },
    )

    with app.test_client() as client:
        page_response = client.get("/admin")
        sensitive_response = client.get("/api/logs")

    assert page_response.headers["Content-Security-Policy"] == MODERN_CSP
    assert page_response.headers["X-Frame-Options"] == "DENY"
    assert page_response.headers["X-Content-Type-Options"] == "nosniff"
    assert page_response.headers["Referrer-Policy"] == "no-referrer"
    assert sensitive_response.headers["Cache-Control"] == "no-store"
    assert sensitive_response.headers["Pragma"] == "no-cache"


def test_vue_dev_server_csp_binds_https_and_websocket_to_explicit_origin(monkeypatch):
    dev_origin = "https://[::1]:5173"
    monkeypatch.setenv("WEB_VUE_DEV_SERVER", dev_origin)

    with app.test_client() as client:
        response = client.get("/")

    csp = response.headers["Content-Security-Policy"]
    assert response.status_code == 200
    assert f"script-src 'self' {dev_origin}" in csp
    assert f"connect-src 'self' {dev_origin} wss://[::1]:5173" in csp
    assert f"font-src 'self' data: {dev_origin}" in csp
    assert f"img-src 'self' data: blob: {dev_origin}" in csp


def test_web_frontend_requires_vue_build(monkeypatch, tmp_path):
    monkeypatch.delenv("WEB_VUE_DEV_SERVER", raising=False)
    monkeypatch.setattr(server, "_frontend_dist_root", tmp_path / "missing-dist")

    with app.test_client() as client:
        response = client.get("/")

    assert response.status_code == 503
    assert response.get_data(as_text=True) == "frontend build not found"
    assert response.headers["Content-Security-Policy"] == MODERN_CSP


def test_legacy_selector_does_not_switch_frontend(monkeypatch):
    monkeypatch.delenv("WEB_VUE_DEV_SERVER", raising=False)
    monkeypatch.setenv("WEB_FORCE_LEGACY_FRONTEND", "1")
    monkeypatch.setattr(
        server,
        "_resolve_vue_entry",
        lambda: {
            "mode": "prod",
            "css_urls": [],
            "favicon_url": "/__frontend/favicon.svg",
            "js_url": "/__frontend/main.js",
        },
    )

    with app.test_client() as client:
        response = client.get("/?legacy=1")

    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert 'id="app"' in text
    assert "legacy.html" not in text


@pytest.mark.parametrize(
    "key",
    (
        "aws_secret_access_key",
        "market-data-access-key",
        "database_dsn",
        "flask_secret_key",
        "mysql.pwd",
        "provider_api_key_name",
        "signing_private_key_path",
    ),
)
def test_ini_redaction_matches_sensitive_markers_at_key_boundaries(key):
    content = redact_ini_secrets(f"[provider]\n{key} = unit-secret\n")

    assert "unit-secret" not in content
    assert f"{key} = <redacted>" in content


@pytest.mark.parametrize("key", ("tokenizer", "passwordless", "secretary", "apikeyboard"))
def test_ini_redaction_does_not_match_sensitive_marker_substrings(key):
    source = f"[provider]\n{key} = visible\n"

    assert redact_ini_secrets(source) == source


def test_public_strategy_config_redacts_sensitive_continuation_lines(monkeypatch, tmp_path):
    library_root = tmp_path / "strategy_library"
    strategy_root = library_root / "redaction_probe"
    strategy_root.mkdir(parents=True)
    (strategy_root / "config.ini").write_text(
        "[provider]\n"
        "aws_secret_access_key = first-secret-fragment\n"
        "    second-secret-fragment\n"
        "database_dsn = dsn-secret-fragment\n"
        "provider_pwd: password-fragment\n"
        "flask_secret_key = third-secret-fragment\n"
        "tokenizer = visible-tokenizer-setting\n"
        "safe_setting = visible\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "LIBRARY_ROOT", library_root)

    with app.test_client() as client:
        response = client.get("/api/library/redaction_probe/config")

    assert response.status_code == 200
    content = response.get_json()["content"]
    assert "first-secret-fragment" not in content
    assert "second-secret-fragment" not in content
    assert "third-secret-fragment" not in content
    assert "dsn-secret" not in content
    assert "password-fragment" not in content
    assert "tokenizer = visible-tokenizer-setting" in content
    assert "safe_setting = visible" in content


SENSITIVE_REQUESTS = (
    ("GET", "/api/logs", None),
    ("GET", "/api/logs/tail", None),
    ("POST", "/api/logs/client", {"message": "boundary probe"}),
    ("GET", "/api/run_events", None),
    ("GET", "/api/run_events/missing-run", None),
    ("DELETE", "/api/library/bad$name/cache", None),
    ("GET", "/api/library/bad$name/config", None),
    ("POST", "/api/backtest/run", {}),
    ("GET", "/api/backtest/status", None),
)


@pytest.mark.parametrize(
    "path",
    ("/api/library/bad$name/config", "/api/backtest/status"),
)
def test_config_and_backtest_status_share_auth_and_no_store(monkeypatch, path):
    monkeypatch.setenv("WEB_ADMIN_TOKEN", "test-secret")

    with app.test_client() as client:
        unauthorized = client.get(path)
        authorized = client.get(path, headers={"X-Web-Admin-Token": "test-secret"})

    assert unauthorized.status_code == 401
    assert unauthorized.headers["Cache-Control"] == "no-store"
    assert unauthorized.headers["Pragma"] == "no-cache"
    assert authorized.status_code not in {401, 403, 503}
    assert authorized.headers["Cache-Control"] == "no-store"
    assert authorized.headers["Pragma"] == "no-cache"


@pytest.mark.parametrize(("method", "path", "payload"), SENSITIVE_REQUESTS)
def test_sensitive_api_keeps_local_default_compatibility(monkeypatch, method, path, payload):
    monkeypatch.delenv("WEB_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("WEB_HOST", raising=False)
    monkeypatch.delenv("WEB_CORS_ORIGINS", raising=False)

    with app.test_client() as client:
        response = client.open(path, method=method, json=payload)

    assert response.status_code not in {401, 403, 503}


@pytest.mark.parametrize(("method", "path", "payload"), SENSITIVE_REQUESTS)
def test_sensitive_api_fails_closed_for_remote_bind_without_token(monkeypatch, method, path, payload):
    monkeypatch.delenv("WEB_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("WEB_HOST", "0.0.0.0")

    with app.test_client() as client:
        response = client.open(path, method=method, json=payload)

    assert response.status_code == 503
    assert response.get_json()["error"] == "sensitive_auth_not_configured"


@pytest.mark.parametrize(("method", "path", "payload"), SENSITIVE_REQUESTS)
def test_sensitive_api_uses_shared_configured_token(monkeypatch, method, path, payload):
    monkeypatch.setenv("WEB_ADMIN_TOKEN", "test-secret")
    monkeypatch.delenv("WEB_HOST", raising=False)

    with app.test_client() as client:
        unauthorized = client.open(path, method=method, json=payload)
        authorized = client.open(
            path,
            method=method,
            json=payload,
            headers={"X-Web-Admin-Token": "test-secret"},
        )

    assert unauthorized.status_code == 401
    assert unauthorized.get_json()["error"] == "admin_auth_required"
    assert authorized.status_code not in {401, 403, 503}


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    SENSITIVE_REQUESTS
    + (
        ("GET", "/api/admin/status", None),
        ("POST", "/api/admin/version-update/run", {}),
    ),
)
def test_sensitive_api_rejects_cross_site_browser_requests(monkeypatch, method, path, payload):
    monkeypatch.delenv("WEB_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("WEB_HOST", raising=False)
    monkeypatch.delenv("WEB_CORS_ORIGINS", raising=False)

    with app.test_client() as client:
        response = client.open(
            path,
            method=method,
            json=payload,
            headers={"Origin": "https://attacker.example"},
        )

    assert response.status_code == 403
    assert response.get_json()["error"] == "cross_site_request_rejected"


def test_loopback_admin_rejects_dns_rebinding_origin(monkeypatch):
    monkeypatch.delenv("WEB_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("WEB_HOST", raising=False)
    monkeypatch.delenv("WEB_CORS_ORIGINS", raising=False)

    with app.test_client() as client:
        response = client.post(
            "/api/admin/version-update/run",
            headers={
                "Host": "attacker.example",
                "Origin": "http://attacker.example",
            },
        )

    assert response.status_code == 403
    assert response.get_json()["error"] == "cross_site_request_rejected"


def test_loopback_proxy_cannot_authorize_external_host_without_token(monkeypatch):
    monkeypatch.delenv("WEB_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("WEB_HOST", raising=False)

    with app.test_client() as client:
        response = client.get(
            "/api/logs",
            headers={"Host": "public.example"},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )

    assert response.status_code == 403
    assert response.get_json()["error"] == "sensitive_local_only"


@pytest.mark.parametrize("host", ["localhost:5000", "127.0.0.1:5000", "[::1]:5000"])
def test_sensitive_api_accepts_loopback_request_hosts_without_token(monkeypatch, host):
    monkeypatch.delenv("WEB_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("WEB_HOST", raising=False)

    with app.test_client() as client:
        response = client.get(
            "/api/logs",
            headers={"Host": host},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )

    assert response.status_code not in {401, 403, 503}


def test_remote_admin_accepts_valid_token_and_explicit_browser_origin(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_TOKEN", "test-secret")
    monkeypatch.setenv("WEB_HOST", "0.0.0.0")
    monkeypatch.setenv("WEB_CORS_ORIGINS", "https://console.example")
    monkeypatch.setattr(admin_routes, "reject_maintenance_submission_if_busy", lambda: None)
    monkeypatch.setattr(admin_routes, "start_version_update", lambda: True)

    with app.test_client() as client:
        response = client.post(
            "/api/admin/version-update/run",
            headers={
                "Origin": "https://console.example",
                "X-Web-Admin-Token": "test-secret",
            },
        )

    assert response.status_code == 200
    assert response.get_json()["status"] == "started"


def test_tls_proxy_same_origin_uses_only_explicit_public_origin(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_TOKEN", "test-secret")
    monkeypatch.setenv("WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_SERVE_MODE", "production")
    monkeypatch.setenv("WEB_PUBLIC_ORIGIN", "https://console.example")
    monkeypatch.delenv("WEB_CORS_ORIGINS", raising=False)

    with app.test_client() as client:
        response = client.get(
            "/api/backtest/status",
            headers={
                "Host": "127.0.0.1:5000",
                "Origin": "https://console.example",
                "X-Forwarded-Host": "attacker.example",
                "X-Forwarded-Proto": "http",
                "X-Web-Admin-Token": "test-secret",
            },
        )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"


def test_forwarded_headers_do_not_create_an_allowed_browser_origin(monkeypatch):
    monkeypatch.setenv("WEB_ADMIN_TOKEN", "test-secret")
    monkeypatch.setenv("WEB_HOST", "127.0.0.1")
    monkeypatch.delenv("WEB_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("WEB_CORS_ORIGINS", raising=False)

    with app.test_client() as client:
        response = client.get(
            "/api/backtest/status",
            headers={
                "Host": "127.0.0.1:5000",
                "Origin": "https://console.example",
                "X-Forwarded-Host": "console.example",
                "X-Forwarded-Proto": "https",
                "X-Web-Admin-Token": "test-secret",
            },
        )

    assert response.status_code == 403
    assert response.get_json()["error"] == "cross_site_request_rejected"


def test_forwarded_for_does_not_turn_remote_request_into_loopback(monkeypatch):
    monkeypatch.delenv("WEB_ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("WEB_HOST", "127.0.0.1")

    with app.test_client() as client:
        response = client.get(
            "/api/backtest/status",
            headers={
                "Host": "127.0.0.1:5000",
                "X-Forwarded-For": "127.0.0.1",
            },
            environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
        )

    assert response.status_code == 403
    assert response.get_json()["error"] == "sensitive_local_only"


def test_loopback_admin_accepts_same_origin_and_cli_requests(monkeypatch):
    monkeypatch.delenv("WEB_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("WEB_HOST", raising=False)
    monkeypatch.setattr(admin_routes, "reject_maintenance_submission_if_busy", lambda: None)
    monkeypatch.setattr(admin_routes, "start_version_update", lambda: True)

    with app.test_client() as client:
        same_origin = client.post(
            "/api/admin/version-update/run",
            headers={"Origin": "http://localhost"},
        )
        cli_request = client.post("/api/admin/version-update/run")

    assert same_origin.status_code == 200
    assert cli_request.status_code == 200


def test_builtin_server_enables_threads_and_disables_reloader(monkeypatch):
    observed = {}
    monkeypatch.setenv("WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("WEB_PORT", "5100")
    monkeypatch.setenv("WEB_DEBUG", "1")
    monkeypatch.setattr(server, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(server.app, "run", lambda **kwargs: observed.update(kwargs))

    server.main()

    assert observed == {
        "host": "127.0.0.1",
        "port": 5100,
        "debug": True,
        "threaded": True,
        "use_reloader": False,
    }
