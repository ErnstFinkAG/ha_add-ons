#!/usr/bin/env python3
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request

LOG = logging.getLogger("public_sensors.web")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

CACHE_PATH = Path("/run/public-sensors/cache.json")
PUBLIC_PATH_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

if os.environ.get("SUPERVISOR_TOKEN"):
    raise RuntimeError(
        "Security error: public web process must not receive SUPERVISOR_TOKEN"
    )

APP = Flask(__name__, static_folder="static", template_folder="templates")


def load_cache() -> dict[str, Any]:
    try:
        with CACHE_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        raise RuntimeError("Waiting for collector cache")

    if not isinstance(payload, dict):
        raise RuntimeError("Invalid collector cache")
    return payload


@APP.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    return response


@APP.before_request
def allow_read_only():
    if request.method not in ("GET", "HEAD"):
        return jsonify({"error": "read_only"}), 405
    return None


@APP.get("/")
def root():
    try:
        cache = load_cache()
    except RuntimeError:
        abort(503)

    if not cache.get("show_index", True):
        abort(404)

    pages = cache.get("pages")
    if not isinstance(pages, list):
        pages = []

    return render_template("directory.html", pages=pages)


@APP.get("/<public_path>/")
def page_view(public_path: str):
    public_path = public_path.lower()
    if not PUBLIC_PATH_RE.fullmatch(public_path):
        abort(404)

    try:
        cache = load_cache()
    except RuntimeError:
        abort(503)

    pages = cache.get("pages")
    known = (
        {
            str(item.get("path"))
            for item in pages
            if isinstance(item, dict)
        }
        if isinstance(pages, list)
        else set()
    )

    if public_path not in known:
        abort(404)

    return render_template(
        "index.html",
        show_index=bool(cache.get("show_index", True)),
    )


@APP.get("/<public_path>/data")
def page_data(public_path: str):
    public_path = public_path.lower()
    if not PUBLIC_PATH_RE.fullmatch(public_path):
        return jsonify({"error": "not_found"}), 404

    try:
        cache = load_cache()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503

    pages = cache.get("pages")
    known = (
        {
            str(item.get("path"))
            for item in pages
            if isinstance(item, dict)
        }
        if isinstance(pages, list)
        else set()
    )

    if public_path not in known:
        return jsonify({"error": "not_found"}), 404

    data = cache.get("data")
    errors = cache.get("errors")
    data = data if isinstance(data, dict) else {}
    errors = errors if isinstance(errors, dict) else {}

    payload = data.get(public_path)
    if not isinstance(payload, dict):
        return jsonify(
            {
                "error": errors.get(public_path)
                or "Data is not available yet"
            }
        ), 503

    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@APP.get("/healthz")
def healthz():
    try:
        cache = load_cache()
    except RuntimeError as exc:
        return jsonify(
            {"status": "starting", "error": str(exc)}
        ), 503

    pages = cache.get("pages")
    data = cache.get("data")
    errors = cache.get("errors")

    total = len(pages) if isinstance(pages, list) else 0
    ready = len(data) if isinstance(data, dict) else 0
    error_count = len(errors) if isinstance(errors, dict) else 0

    if total == 0:
        return jsonify(
            {
                "status": "unconfigured",
                "ready": 0,
                "total": 0,
                "errors": 0,
            }
        ), 200

    return jsonify(
        {
            "status": (
                "ok"
                if ready == total and error_count == 0
                else "degraded"
            ),
            "ready": ready,
            "total": total,
            "errors": error_count,
        }
    ), 200 if ready else 503
