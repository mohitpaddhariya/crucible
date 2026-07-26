#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dashboard_pid=""
website_pid=""

cleanup() {
  if [[ -n "$dashboard_pid" ]]; then
    kill "$dashboard_pid" 2>/dev/null || true
  fi
  if [[ -n "$website_pid" ]]; then
    kill "$website_pid" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

npm --prefix "$repo_root/web" run dev -- --hostname 127.0.0.1 --port 3000 &
dashboard_pid=$!

npm --prefix "$repo_root/website" run dev -- --port 4173 &
website_pid=$!

wait -n "$dashboard_pid" "$website_pid"
