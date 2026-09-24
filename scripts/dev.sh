#!/bin/sh
set -eu

uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &
api_pid=$!
trap 'kill "$api_pid" 2>/dev/null || true' EXIT INT TERM

API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8000}" \
  uv run streamlit run app/ui/streamlit_app.py \
    --server.address 127.0.0.1 --server.port 8501
