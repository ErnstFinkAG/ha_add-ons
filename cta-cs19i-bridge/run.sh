#!/usr/bin/env bash
set -euo pipefail

# main.py reads /data/options.json itself, so the controller password never appears on a command line
exec python3 /app/main.py /data/options.json
