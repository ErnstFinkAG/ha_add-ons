from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Flask, jsonify, render_template

from models import ConfigError, load_raw_options, normalize_config

PORT = int(os.environ.get("PORT", "8099"))
HOST = "0.0.0.0"

app = Flask(__name__)


@app.get("/")
def index():
    raw = load_raw_options()
    try:
        normalized = normalize_config(raw).to_dict()
        error = None
    except ConfigError as exc:
        normalized = None
        error = str(exc)
    return render_template(
        "index.html",
        raw_json=json.dumps(raw, indent=2, ensure_ascii=False),
        normalized_json=json.dumps(normalized, indent=2, ensure_ascii=False) if normalized else "",
        error=error,
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/options")
def api_options():
    return jsonify(load_raw_options())


@app.get("/api/normalized")
def api_normalized():
    try:
        return jsonify(normalize_config().to_dict())
    except ConfigError as exc:
        return jsonify({"error": str(exc)}), 400


if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=False)
