#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f "app/radio_control_app.py" ]]; then
  echo "App file not found: app/radio_control_app.py"
  exit 1
fi
if command -v python3 >/dev/null 2>&1; then
  nohup python3 app/radio_control_app.py >/dev/null 2>&1 &
elif command -v python >/dev/null 2>&1; then
  nohup python app/radio_control_app.py >/dev/null 2>&1 &
else
  echo "Python interpreter not found in PATH"
  exit 1
fi
