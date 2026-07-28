#!/usr/bin/env bash
# Verify Crucible's claims offline: no keys, no network, not one rupee spent.
#
# This is the command the README dares you to run. It executes every offline
# suite in the repo against the recorded artifacts and the code as checked out.
# Nothing here calls ElevenLabs or Sarvam; a fake key satisfies the config
# loader, which refuses to start without one so a REAL run can never begin
# against credentials nobody chose.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo "config.yaml created from config.example.yaml (offline defaults)"
fi
if [ ! -f .env ]; then
  cat > .env <<'EOF'
# Placeholders written by scripts/verify.sh so the OFFLINE suites can load
# config. They are not credentials and no network call is made with them.
ELEVENLABS_API_KEY=sk_offline_verification_only_not_a_real_key_0000000000
ELEVENLABS_AGENT_ID=agent_0000offline0000verification0000
SARVAM_API_KEY=sk_offline_verification_only_0000
EOF
  echo ".env created with offline placeholders (not credentials)"
fi

total=0
fail=0
run() {
  echo
  echo "== $1 =="
  if PYTHONPATH=. uv run --python 3.12 python "$1"; then
    echo "-- ok"
  else
    fail=$((fail+1))
    echo "-- FAILED"
  fi
}

run scripts/smoke_loop_offline.py        # 11 scenarios, the whole text loop
run scripts/smoke_audio_offline.py       # 14 checks, the voice loop offline
run scripts/regress_audit.py             # 183 checks, evidence + trust chain
run scripts/regress_checks.py            # 256 assertions, the numeric checks
run scripts/regress_checks_provenance.py # 93+ assertions, ASR provenance
echo
PYTHONPATH=. uv run --python 3.12 python -m synth.patterns >/dev/null && echo "== synth.patterns selftest == ok" || { fail=$((fail+1)); echo "== synth.patterns selftest == FAILED"; }
PYTHONPATH=. uv run --python 3.12 python -m synth.report >/dev/null 2>&1 && echo "== synth.report selftest == ok" || { fail=$((fail+1)); echo "== synth.report selftest == FAILED"; }

echo
if [ "$fail" -eq 0 ]; then
  echo "ALL SUITES PASSED. Zero API calls. Zero credentials. Zero rupees."
else
  echo "$fail suite(s) failed."
  exit 1
fi
