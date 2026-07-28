# Crucible backend on Modal

Read-only API serving the recorded evaluation runs, so anyone can hear two agents talking
and read the evidence with nothing installed and no API keys.

**Live:** https://mohit-paddhariya--crucible-backend-api.modal.run

| endpoint | returns |
|---|---|
| `/` | service index |
| `/healthz` | liveness |
| `/api/runs` | published runs, newest first |
| `/api/runs/{runId}` | conversations + scorecards + synthesis for one run |
| `/api/audio/{runId}/{personaId}/full` | the whole call as one seekable WAV |
| `/api/audio/{runId}/{personaId}/full?meta=1` | turn timeline for the player |

## Deploy

    uv run modal deploy deploy/modal/app.py

## Refresh the data

`snapshot/` holds the exact bytes the Next.js API returns. It is captured from a running
local server rather than reimplemented, because `web/lib/runs.ts` and `web/lib/audio.ts`
do real work — merging scorecards into conversations, stitching per-turn PCM into one
RIFF/WAVE file — and a second implementation in Python would be a second thing to keep in
step. Any drift would surface as a dashboard rendering subtly wrong numbers.

To refresh: run the UI locally (`./scripts/dev_ui.sh`), re-run the snapshot step, redeploy.

## What is deliberately not published

Only runs recorded against the **white-label agent** are included. Every earlier run has
the real customer's brand spoken aloud by the agent, and masking a transcript does not
silence a voice. The snapshot allowlists run ids and re-scans each payload for the brand
before writing it; withheld runs 404.

This backend cannot start a new conversation. That needs live ElevenLabs and Sarvam
credentials, which belong nowhere near a public endpoint.
