#!/usr/bin/env bash
# One server, one port. http://localhost:3000
#
#   /            the landing page
#   /dashboard   the dashboard, already on the newest run
#   /api/**      run artifacts, read off disk
#
# This used to start TWO servers: Next on :3000 serving the dashboard at `/`, and a second
# Vite server on :4173 serving the landing and proxying /dashboard, /api and /_next back to
# Next. A visitor had to know which port was which, and the landing's own "Go to dashboard"
# link only worked when entered through the proxy. The landing now lives inside the Next app
# (web/app/landing/), so there is one origin and nothing to proxy.
#
# website/ remains the SOURCE of the landing page, and its own dev server is still the right
# way to iterate on it in isolation:  npm --prefix website run dev
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Crucible UI  ->  http://localhost:3000"
echo "  /            landing"
echo "  /dashboard   dashboard"
echo

exec npm --prefix "$repo_root/web" run dev -- --hostname 127.0.0.1 --port 3000
