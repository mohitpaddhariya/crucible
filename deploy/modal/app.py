"""Crucible read-only backend on Modal.

Serves the recorded evaluation runs — conversations, scorecards, synthesis and the
stitched conversation audio — so anyone can hear two agents actually talking and read
the evidence, with nothing to install and no API keys.

WHY A SNAPSHOT AND NOT A PORT
-----------------------------
The live API is TypeScript (web/lib/runs.ts merges conversations with scorecards and
reshapes them; web/lib/audio.ts stitches per-turn PCM into one RIFF/WAVE file with
half-second gaps). Rewriting that in Python would be a second implementation to keep in
step with the first, and any drift shows up as a dashboard that renders subtly wrong
numbers. Instead the exact bytes the working server returns are captured into
`snapshot/` and replayed here. The contract cannot drift because it was never
reimplemented.

The trade is honest: this backend is READ-ONLY and serves recorded runs. It cannot start
a new conversation — that needs the live ElevenLabs and Sarvam credentials, and belongs
nowhere near a public endpoint.

WHAT IS DELIBERATELY NOT PUBLISHED
----------------------------------
Only runs recorded against the WHITE-LABEL agent are included. Every earlier run has the
real customer's brand spoken aloud by the agent, and masking a transcript does not
silence a voice. The snapshot step allowlists three run ids and re-scans each payload
for the brand before writing it.
"""

from pathlib import Path

import modal

HERE = Path(__file__).parent
SNAPSHOT = HERE / "snapshot"

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("fastapi[standard]==0.115.6")
    # Baked into the image rather than mounted from a Volume: the payload is ~43 MB of
    # immutable recordings, so there is nothing to write and no cold-start fetch to pay.
    .add_local_dir(SNAPSHOT, remote_path="/snapshot")
)

app = modal.App("crucible-backend", image=image)


@app.function(min_containers=1, timeout=600)
@modal.asgi_app()
def api():
    import json

    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, Response

    ROOT = Path("/snapshot")
    web = FastAPI(
        title="Crucible backend",
        description="Recorded voice-agent evaluation runs: conversations, evidence, audio.",
    )
    # The dashboard is served from a different origin (Vercel, or localhost while
    # developing), and every route here is public read-only data.
    web.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Content-Length", "Content-Range", "Accept-Ranges", "X-Turn-Count"],
    )

    def _safe(seg: str) -> bool:
        """No traversal, no separators. Run and persona ids are flat slugs."""
        return bool(seg) and "/" not in seg and "\\" not in seg and ".." not in seg

    def _json_file(rel: str):
        p = ROOT / rel
        if not p.is_file():
            return None
        return Response(content=p.read_bytes(), media_type="application/json",
                        headers={"Cache-Control": "public, max-age=300"})

    @web.get("/")
    def index():
        runs = json.loads((ROOT / "api" / "runs.json").read_text())
        return {
            "service": "crucible-backend",
            "what": "Recorded evaluation runs against a live voice agent. Read-only.",
            "runs": len(runs),
            "newest": runs[0]["id"] if runs else None,
            "endpoints": [
                "/api/runs",
                "/api/runs/{runId}",
                "/api/audio/{runId}/{personaId}/full",
                "/api/audio/{runId}/{personaId}/full?meta=1",
                "/healthz",
            ],
        }

    @web.get("/healthz")
    def healthz():
        ok = (ROOT / "api" / "runs.json").is_file()
        return JSONResponse({"ok": ok}, status_code=200 if ok else 503)

    @web.get("/api/runs")
    def list_runs():
        r = _json_file("api/runs.json")
        if r is None:
            raise HTTPException(500, "snapshot missing")
        return r

    @web.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        if not _safe(run_id):
            raise HTTPException(400, "invalid run id")
        r = _json_file(f"api/runs/{run_id}.json")
        if r is None:
            raise HTTPException(404, f"run not found: {run_id}")
        return r

    @web.api_route("/api/audio/{run_id}/{persona_id}/full", methods=["GET", "HEAD"])
    def audio_full(run_id: str, persona_id: str, request: Request):
        if not (_safe(run_id) and _safe(persona_id)):
            raise HTTPException(400, "invalid run or persona id")

        # `?meta=1` returns the timeline instead of audio, so the player can highlight the
        # turn currently sounding. Same contract as the Next route it replaces.
        if request.query_params.get("meta") is not None:
            r = _json_file(f"api/audio/{run_id}/{persona_id}/full.meta.json")
            if r is None:
                raise HTTPException(404, f"no audio for {persona_id} in run {run_id}")
            return r

        wav = ROOT / "api" / "audio" / run_id / persona_id / "full.wav"
        if not wav.is_file():
            raise HTTPException(404, f"no audio for {persona_id} in run {run_id}")

        turns = 0
        meta = wav.with_name("full.meta.json")
        if meta.is_file():
            turns = len(json.loads(meta.read_text()).get("turns") or [])

        # FileResponse gives Content-Length and byte-range support, which is what lets
        # Safari scrub the file instead of refusing to seek.
        return FileResponse(
            wav,
            media_type="audio/wav",
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=3600",
                "X-Turn-Count": str(turns),
                "Content-Disposition":
                    f'inline; filename="{run_id}_{persona_id}_full.wav"',
            },
        )

    return web
