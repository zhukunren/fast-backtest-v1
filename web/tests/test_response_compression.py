import gzip

from flask import Flask, request
from werkzeug.wrappers import Response

from web.response_compression import compress_response
from web import server as server_module


def test_compress_response_only_when_gzip_is_accepted():
    app = Flask(__name__)
    payload = ("策略目录" * 800).encode("utf-8")

    with app.test_request_context(headers={"Accept-Encoding": "gzip"}):
        response = compress_response(
            Response(payload, mimetype="application/json"),
            request=request,
        )

    assert response.headers["Content-Encoding"] == "gzip"
    assert response.headers["Vary"] == "Accept-Encoding"
    assert gzip.decompress(response.get_data()) == payload


def test_compress_response_leaves_clients_without_gzip_untouched():
    app = Flask(__name__)
    payload = b"x" * 4096

    with app.test_request_context(headers={"Accept-Encoding": "identity"}):
        response = compress_response(
            Response(payload, mimetype="application/json"),
            request=request,
        )

    assert response.get_data() == payload
    assert "Content-Encoding" not in response.headers


def test_frontend_build_assets_are_compressed_and_cached_immutably(tmp_path, monkeypatch):
    dist_root = tmp_path / "frontend_dist"
    asset_path = dist_root / "assets" / "app-content-hash.js"
    asset_path.parent.mkdir(parents=True)
    payload = ("const strategyDetail = 'ready';\n" * 400).encode("utf-8")
    asset_path.write_bytes(payload)
    monkeypatch.setattr(server_module, "_frontend_dist_root", dist_root)

    with server_module.app.test_client() as client:
        response = client.get(
            "/__frontend/assets/app-content-hash.js",
            headers={"Accept-Encoding": "gzip"},
        )

    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "gzip"
    assert response.headers["Vary"] == "Accept-Encoding"
    assert "public" in response.headers["Cache-Control"]
    assert "max-age=31536000" in response.headers["Cache-Control"]
    assert "immutable" in response.headers["Cache-Control"]
    assert gzip.decompress(response.get_data()) == payload


def test_compressed_etag_supports_conditional_requests():
    app = Flask(__name__)
    payload = ("strategy-catalog" * 500).encode("utf-8")

    @app.get("/catalog")
    def catalog():
        response = Response(payload, mimetype="application/json")
        response.set_etag("catalog-revision")
        return compress_response(response, request=request)

    with app.test_client() as client:
        first = client.get("/catalog", headers={"Accept-Encoding": "gzip"})
        second = client.get(
            "/catalog",
            headers={
                "Accept-Encoding": "gzip",
                "If-None-Match": first.headers["ETag"],
            },
        )

    assert first.status_code == 200
    assert first.headers["ETag"] == '\"catalog-revision-gzip\"'
    assert second.status_code == 304
    assert second.get_data() == b""
