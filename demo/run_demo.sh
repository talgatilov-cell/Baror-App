#!/bin/bash
# Starts the app with demo data on port 8501. Usage:  demo/run_demo.sh [lan]
#   (no argument)  reachable from this computer only   http://localhost:8501
#   lan            also reachable from your phone on the same Wi-Fi   http://<this computer's address>:8501
# Stop with Ctrl+C. Delete demo/demo.db to start again with fresh data. Do NOT run this on a public server.
set -e
cd "$(dirname "$0")/.."
[ -f demo/demo.db ] || .venv/bin/python demo/demo_seed.py demo/demo.db
ADDR=127.0.0.1; [ "$1" = "lan" ] && ADDR=0.0.0.0 && echo "LAN mode: open  http://$(hostname -I | awk '{print $1}'):8501  on the phone (same Wi-Fi)"
DB_PATH="$PWD/demo/demo.db" BAROR_COHORT_VIEWS=1 exec .venv/bin/python -m streamlit run app.py \
  --server.port=8501 --server.address=$ADDR --server.headless=true --browser.gatherUsageStats=false
