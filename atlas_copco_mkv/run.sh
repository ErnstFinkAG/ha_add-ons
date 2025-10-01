#!/usr/bin/env bash
set -euo pipefail

echo "[atlas_copco_mkv] starting..."

python3 /usr/src/app/main.py
status=$?

if [ $status -eq 0 ]; then
  echo "[atlas_copco_parser] finished successfully (exit code 0)"
else
  echo "[atlas_copco_parser] failed with exit code $status"
fi

exit $status