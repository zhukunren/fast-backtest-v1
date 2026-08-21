"""HTTP response compression for browser-readable static and JSON payloads."""
from __future__ import annotations

import gzip

from flask import Request, Response


_MIN_COMPRESS_BYTES = 1024
_COMPRESSIBLE_MIME_TYPES = frozenset({
    "application/javascript",
    "application/json",
    "application/manifest+json",
    "application/xml",
    "image/svg+xml",
    "text/css",
    "text/html",
    "text/javascript",
    "text/plain",
    "text/xml",
})


def _make_conditional_if_tagged(response: Response, request: Request) -> Response:
    if response.status_code == 200 and response.get_etag()[0]:
        return response.make_conditional(request)
    return response


def compress_response(response: Response, *, request: Request) -> Response:
    """Gzip compress eligible responses when the client explicitly accepts it."""
    if (
        response.direct_passthrough
        or request.method == "HEAD"
        or response.status_code != 200
        or response.headers.get("Content-Encoding")
        or response.mimetype not in _COMPRESSIBLE_MIME_TYPES
        or request.accept_encodings.best_match(["gzip"]) != "gzip"
    ):
        return _make_conditional_if_tagged(response, request)

    payload = response.get_data()
    if len(payload) < _MIN_COMPRESS_BYTES:
        return _make_conditional_if_tagged(response, request)
    compressed = gzip.compress(payload, compresslevel=6, mtime=0)
    if len(compressed) >= len(payload):
        return _make_conditional_if_tagged(response, request)

    response.set_data(compressed)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = str(len(compressed))
    response.vary.add("Accept-Encoding")
    etag, is_weak = response.get_etag()
    if etag:
        response.set_etag(f"{etag}-gzip", weak=is_weak)
    return _make_conditional_if_tagged(response, request)


__all__ = ["compress_response"]
