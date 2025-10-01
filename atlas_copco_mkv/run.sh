#!/usr/bin/env bash
set -euo pipefail

echo "[run.sh] Starting Atlas Copco MKV add-on..."
python3 /usr/src/app/atlas_copco_parser.py
