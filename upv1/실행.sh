#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv || { echo 'python3-venv 패키지를 설치한 뒤 다시 실행하세요.'; exit 1; }
fi
if ! .venv/bin/python -c 'import reportlab, serial, bleak' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python experiments/haneulmaru_pro/stream_server.py --local "$@"
