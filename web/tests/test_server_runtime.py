from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from web.server_runtime import serve_web_app


pytestmark = pytest.mark.unit


class _App:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


def _logger(*args: object, **kwargs: object) -> None:
    return None


def test_development_launcher_is_threaded_without_reloader() -> None:
    app = _App()

    serve_web_app(
        app,
        event_logger=_logger,
        environ={"WEB_HOST": "127.0.0.1", "WEB_PORT": "5050", "WEB_DEBUG": "1"},
    )

    assert app.calls == [
        {
            "host": "127.0.0.1",
            "port": 5050,
            "debug": True,
            "use_reloader": False,
            "threaded": True,
        }
    ]


def test_development_launcher_rejects_non_loopback_binding() -> None:
    with pytest.raises(RuntimeError, match="loopback"):
        serve_web_app(
            _App(),
            event_logger=_logger,
            environ={"WEB_HOST": "0.0.0.0"},
        )


def test_production_launcher_uses_configured_waitress_threads(monkeypatch) -> None:
    calls: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setitem(
        sys.modules,
        "waitress",
        SimpleNamespace(serve=lambda app, **kwargs: calls.append((app, dict(kwargs)))),
    )
    app = _App()

    serve_web_app(
        app,
        event_logger=_logger,
        environ={
            "WEB_SERVE_MODE": "production",
            "WEB_HOST": "127.0.0.1",
            "WEB_ADMIN_TOKEN": "x" * 32,
            "WEB_PUBLIC_ORIGIN": "https://backtest.example.com",
            "WEB_THREADS": "6",
        },
    )

    assert calls == [
        (
            app,
            {
                "host": "127.0.0.1",
                "port": 5000,
                "threads": 6,
                "clear_untrusted_proxy_headers": True,
            },
        )
    ]
    assert app.calls == []


def test_production_launcher_defaults_to_four_waitress_threads(monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setitem(
        sys.modules,
        "waitress",
        SimpleNamespace(serve=lambda _app, **kwargs: calls.append(dict(kwargs))),
    )

    serve_web_app(
        _App(),
        event_logger=_logger,
        environ={
            "WEB_SERVE_MODE": "production",
            "WEB_HOST": "127.0.0.1",
            "WEB_ADMIN_TOKEN": "x" * 32,
        },
    )

    assert calls[0]["threads"] == 4


@pytest.mark.parametrize("value", ["0", "-1", "65", "not-an-integer"])
def test_launcher_rejects_invalid_thread_count(value: str) -> None:
    with pytest.raises(RuntimeError, match="WEB_THREADS must be an integer between 1 and 64"):
        serve_web_app(
            _App(),
            event_logger=_logger,
            environ={"WEB_HOST": "127.0.0.1", "WEB_THREADS": value},
        )


@pytest.mark.parametrize(
    "name",
    [
        "FLASK_RUN_RELOAD",
        "FLASK_RUN_THREADED",
        "WEB_RELOADER",
        "WEB_THREADED",
        "WEB_USE_RELOADER",
    ],
)
def test_launcher_ignores_reloader_flags_and_stays_threaded(name: str) -> None:
    app = _App()

    serve_web_app(
        app,
        event_logger=_logger,
        environ={"WEB_HOST": "127.0.0.1", name: "true"},
    )

    assert app.calls[0]["use_reloader"] is False
    assert app.calls[0]["threaded"] is True


def test_launcher_accepts_external_worker_configuration() -> None:
    app = _App()

    serve_web_app(
        app,
        event_logger=_logger,
        environ={
            "WEB_HOST": "127.0.0.1",
            "GUNICORN_WORKERS": "4",
            "WEB_WORKERS": "4",
            "WSGI_THREADS": "8",
            "GUNICORN_CMD_ARGS": "--workers 4",
        },
    )

    assert len(app.calls) == 1


@pytest.mark.parametrize(
    ("environment", "message"),
    [
        ({"WEB_SERVE_MODE": "production", "WEB_ADMIN_TOKEN": "short"}, "at least 32"),
        (
            {
                "WEB_SERVE_MODE": "production",
                "WEB_ADMIN_TOKEN": "x" * 32,
                "WEB_DEBUG": "1",
            },
            "disabled",
        ),
        (
            {
                "WEB_SERVE_MODE": "production",
                "WEB_ADMIN_TOKEN": "x" * 32,
                "WEB_VUE_DEV_SERVER": "http://127.0.0.1:5173",
            },
            "not allowed",
        ),
    ],
)
def test_production_launcher_fails_closed_on_unsafe_settings(
    environment: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        serve_web_app(_App(), event_logger=_logger, environ=environment)
