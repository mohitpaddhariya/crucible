"""runner/run.py — `spar run`. Orchestrates n personas against the live agent.

Contract: docs/INTERFACES.md §6.3, §6.4, §8.1.

What this file owns:
  * the run id and the run directory
  * one shared httpx.AsyncClient; one SarvamClient per role per conversation
  * asyncio.Semaphore(run.max_parallel)
  * the shared BudgetTracker — it ABORTS the run mid-flight, it does not warn at the end
  * run.json

The rule that matters most here: ONE PERSONA FAILING MUST NOT KILL THE RUN. Three good
conversations beat zero. Every conversation is caught individually, its error is recorded
in its own artifact, and the others carry on.

Plain readable log lines only. No dashboard.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make `spar` runnable from anywhere: the project root must be importable.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from agent import persona as persona_mod  # noqa: E402
from agent import sarvam as sarvam_mod  # noqa: E402  — read AUTH_STYLE_USED at WRITE time
from agent.sarvam import LLMConfig, SarvamClient  # noqa: E402
from config import Config, ConfigError, load_config  # noqa: E402
from runner.loop import BudgetTracker, ConversationResult, run_conversation, utc_now  # noqa: E402

log = logging.getLogger("voice_spar")

ELEVENLABS_AGENT_URL = "https://api.elevenlabs.io/v1/convai/agents/{agent_id}"


# ======================================================================================
# logging — plain, readable, one line per event
# ======================================================================================


class _Fmt(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now().strftime("%H:%M:%S")
        return f"{ts}  {record.getMessage()}"


def setup_logging(verbose: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_Fmt())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    # httpx logs a line per request at INFO; that is noise next to our own turn log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)


def _snip(text: str, n: int = 110) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= n else flat[: n - 1] + "…"


# ======================================================================================
# run id + directory
# ======================================================================================


def new_run_id() -> str:
    """YYYYMMDD-HHMMSS-<6 lowercase hex>, UTC (§8.1)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


def make_run_dir(out_dir: Path, run_id: str) -> Path:
    run_dir = Path(out_dir) / run_id
    for sub in ("conversations", "raw", "prompts", "scorecards"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    return run_dir


# ======================================================================================
# agent metadata — one read-only GET so the artifact can carry agent_name / agent_llm
# ======================================================================================


async def fetch_agent_info(http: httpx.AsyncClient, api_key: str, agent_id: str) -> dict[str, Any]:
    """Read-only. Never modifies the agent. Failure is a warning, never fatal."""
    info: dict[str, Any] = {"name": None, "llm": None, "text_only_overridable": None}
    try:
        r = await http.get(
            ELEVENLABS_AGENT_URL.format(agent_id=agent_id),
            headers={"xi-api-key": api_key},
            timeout=30.0,
        )
        if r.status_code != 200:
            info["error"] = f"HTTP {r.status_code}"
            return info
        body = r.json()
    except Exception as exc:  # noqa: BLE001
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info

    info["name"] = body.get("name")
    conv = (body.get("conversation_config") or {}).get("agent") or {}
    info["llm"] = ((conv.get("prompt") or {}).get("llm"))
    overrides = (
        ((body.get("platform_settings") or {}).get("overrides") or {})
        .get("conversation_config_override") or {}
    ).get("conversation") or {}
    info["text_only_overridable"] = overrides.get("text_only")
    return info


# ======================================================================================
# the run
# ======================================================================================


class BudgetGuardInert(RuntimeError):
    """The run budget cannot be enforced, and nobody said that was acceptable.

    Raised BEFORE any live API call. `run.budget_inr` is the only thing standing between a
    stuck retry loop and a real bill; with a model priced at 0.0/absent its spend is
    invisible, so the cap can never fire. Fail closed: either fill in `pricing:` or say
    explicitly that this run has no budget guard (`--allow-inert-budget`).
    """


async def execute_run(cfg: Config, *, only: list[str] | None = None,
                      run_id: str | None = None,
                      allow_inert_budget: bool = False) -> dict[str, Any]:
    run_id = run_id or new_run_id()
    run_dir = make_run_dir(cfg.run.out_dir, run_id)

    selection: list[str] | str = only if only else cfg.run.personas
    budget = BudgetTracker(cfg)

    # The budget guard is DELIBERATELY NOT ENFORCED at Level 0 (operator decision, 26 July
    # 2026): no INR rates have been supplied, and blocking runs over a cost cap nobody has
    # calibrated was getting in the way. The machinery is intact and still charges every call —
    # fill in `pricing:` with real INR-per-1M-token rates and the cap starts biting again with
    # no code change. Until then this is a warning, not a gate.
    if budget.inert:
        log.warning(
            "budget guard INERT — run.budget_inr = Rs %s is NOT enforced this run "
            "(no pricing rates for %s). Costs will be reported as null, not 0.0.",
            budget.limit_inr, ", ".join(budget.models_in_use),
        )

    log.info("=" * 78)
    log.info("voice-spar run %s   (LEVEL 0 — text only, no audio anywhere)", run_id)
    log.info("  artifacts   : %s", run_dir)
    log.info("  target      : %s  agent=%s  auth=%s", cfg.target.adapter,
             cfg.target.agent_id, cfg.target.auth)
    log.info("  persona     : %s  t=%s  max_tokens=%s", cfg.persona_brain.model,
             cfg.persona_brain.temperature, cfg.persona_brain.max_tokens)
    log.info("  referee     : %s  enabled=%s  window=%s", cfg.referee.model,
             cfg.referee.enabled, cfg.referee.window_turns)
    log.info("  limits      : parallel=%s  budget=Rs %s  conversation_cap=%ss",
             cfg.run.max_parallel, cfg.run.budget_inr, cfg.run.max_conversation_seconds)
    log.info("=" * 78)

    http = httpx.AsyncClient(timeout=120.0)
    warnings: list[str] = list(cfg.warnings)
    results: list[ConversationResult] = []
    skipped: list[dict[str, str]] = []
    started_at = utc_now()
    wall_start = time.monotonic()

    try:
        # ── personas load first: an invalid YAML must fail before any live API call
        brain_template = cfg.persona_brain
        try:
            personas = persona_mod.load_all(cfg.personas_dir, selection, brain=None)
        except persona_mod.PersonaError as exc:
            log.error("persona load failed:\n%s", exc)
            raise

        log.info("loaded %d persona(s): %s", len(personas), ", ".join(p.id for p in personas))
        for p in personas:
            for w in p.warnings:
                log.warning("  ! %s: %s", p.id, w)

        agent_info = await fetch_agent_info(http, cfg.secrets.elevenlabs_api_key,
                                            cfg.target.agent_id)
        if agent_info.get("error"):
            warnings.append(f"agent_metadata_unavailable: {agent_info['error']}")
            log.warning("could not read agent metadata (%s) — artifacts will carry nulls",
                        agent_info["error"])
        else:
            log.info("agent        : %s  llm=%s  text_only overridable=%s",
                     agent_info.get("name"), agent_info.get("llm"),
                     agent_info.get("text_only_overridable"))
            if agent_info.get("text_only_overridable") is False:
                warnings.append("text_only is NOT listed as an allowed runtime override")

        # Reaching here with an inert guard means the operator explicitly accepted it.
        # It is still recorded in run.json — a run with no budget guard must never look
        # like a run with one.
        if budget.inert:
            msg = ("budget guard is INERT and the run was started anyway "
                   "(--allow-inert-budget): pricing rates for "
                   f"{', '.join(budget.models_in_use)} are 0.0/absent, so cost always "
                   "computes to nothing and run.budget_inr can never fire. Nothing in this "
                   "run was cost-capped.")
            warnings.append(f"budget_guard_inert: {msg}")
            log.warning("! %s", msg)

        semaphore = asyncio.Semaphore(cfg.run.max_parallel)

        def on_event(pid: str, kind: str, message: str) -> None:
            if kind == "open":
                log.info("[%s] connected  %s", pid, message)
            elif kind == "agent":
                log.info("[%s] AGENT     %s", pid, _snip(message))
            elif kind == "persona":
                log.info("[%s] CUSTOMER  %s", pid, _snip(message))

        async def one(p) -> ConversationResult | None:
            async with semaphore:
                # Budget is checked at the gate too: a persona that has not started yet
                # when the money runs out is SKIPPED and recorded, never half-run.
                if budget.exceeded:
                    skipped.append({"persona_id": p.id, "reason": "budget_exceeded"})
                    log.warning("[%s] SKIPPED — run budget exhausted before it started", p.id)
                    return None

                brain = SarvamClient(cfg.secrets.sarvam_api_key, brain_template,
                                     http=http, label=f"persona:{p.id}")
                referee_llm = SarvamClient(
                    cfg.secrets.sarvam_api_key,
                    LLMConfig(provider=cfg.referee.provider, model=cfg.referee.model,
                              temperature=cfg.referee.temperature,
                              max_tokens=cfg.referee.max_tokens,
                              timeout_s=cfg.persona_brain.timeout_s),
                    http=http, label=f"referee:{p.id}",
                ) if cfg.referee.enabled else None

                object.__setattr__(p, "brain", brain)
                log.info("[%s] starting  (%s)", p.id, p.name)
                t0 = time.monotonic()
                try:
                    result = await run_conversation(
                        p, cfg=cfg, run_id=run_id, run_dir=run_dir, budget=budget,
                        referee_llm=referee_llm, agent_info=agent_info, on_event=on_event,
                    )
                except Exception as exc:  # noqa: BLE001 — run_conversation should not raise
                    log.exception("[%s] FAILED outside the conversation loop: %s", p.id, exc)
                    skipped.append({"persona_id": p.id, "reason": f"runner_error: {exc}"})
                    return None
                finally:
                    await brain.aclose()
                    if referee_llm is not None:
                        await referee_llm.aclose()

                log.info(
                    "[%s] DONE in %.1fs — %s (%s) · %d turns (%d agent / %d customer) · "
                    "%d persona calls, %d retries · Rs %s",
                    p.id, time.monotonic() - t0, result.end_reason.code,
                    result.end_reason.kind, result.turn_count["total"],
                    result.turn_count["agent"], result.turn_count["persona"],
                    result.usage["persona_brain"].calls, result.usage["persona_brain"].retries,
                    "n/a" if result.cost_inr is None else f"{result.cost_inr:.4f}",
                )
                if result.end_reason.evidence:
                    log.info("[%s]   evidence: %s", p.id, _snip(result.end_reason.evidence))
                for e in result.errors:
                    if e.fatal:
                        log.error("[%s]   fatal %s: %s", p.id, e.code, _snip(e.message, 200))
                log.info("[%s] artifact  %s", p.id, result.artifact_path)
                return result

        gathered = await asyncio.gather(*(one(p) for p in personas), return_exceptions=True)
        for item in gathered:
            if isinstance(item, ConversationResult):
                results.append(item)
            elif isinstance(item, BaseException):
                log.error("conversation task raised: %r", item)

    finally:
        await http.aclose()

    ended_at = utc_now()
    duration_s = round(time.monotonic() - wall_start, 2)

    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "level": 0,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_s": duration_s,
        "config": cfg.redacted(),
        # Read off the module, not a from-import: the from-import binds the value at import
        # time (None) and never sees the client discover which auth header actually works.
        "sarvam_auth_style": sarvam_mod.AUTH_STYLE_USED,
        "personas": [
            {
                "persona_id": r.persona_id,
                "ok": r.ok,
                "end_reason": r.end_reason.code,
                "end_kind": r.end_reason.kind,
                "conversation_id": r.conversation_id,
                "turn_count": r.turn_count,
                "duration_s": r.duration_s,
                "cost_inr": r.cost_inr,
                "errors": len(r.errors),
                "artifact": str(r.artifact_path.relative_to(run_dir)),
            }
            for r in results
        ],
        "skipped": skipped,
        "totals": {
            "conversations": len(results),
            "ok": sum(1 for r in results if r.ok),
            "failed": sum(1 for r in results if not r.ok),
            "turns": sum(r.turn_count["total"] for r in results),
            **budget.summary(),
        },
        "warnings": list(dict.fromkeys(warnings)),
    }
    (run_dir / "run.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                      encoding="utf-8")

    log.info("=" * 78)
    log.info("run %s finished in %.1fs — %d ok, %d failed, %d skipped",
             run_id, duration_s, manifest["totals"]["ok"], manifest["totals"]["failed"],
             len(skipped))
    log.info("  spent Rs %.4f of Rs %s%s", budget.spent_inr, budget.limit_inr,
             "  (rates are 0.0 — figure is not meaningful)" if budget.unpriced_models
             or budget.spent_inr == 0.0 else "")
    log.info("  run.json    : %s", run_dir / "run.json")
    for r in results:
        log.info("  %-18s %-22s %s", r.persona_id, r.end_reason.code, r.artifact_path)
    log.info("=" * 78)
    return manifest


# ======================================================================================
# CLI
# ======================================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spar",
        description="voice-spar — synthetic Sarvam personas vs a live ElevenLabs agent (Level 0, text only)",
    )
    sub = parser.add_subparsers(dest="command")

    run_cmd = sub.add_parser("run", help="hold the conversations and write the transcripts")
    run_cmd.add_argument("--personas", default=None,
                         help="comma-separated persona ids (default: run.personas from config.yaml)")
    run_cmd.add_argument("--config", default=None, type=Path, help="path to config.yaml")
    run_cmd.add_argument("--env", default=None, type=Path, help="path to .env")
    run_cmd.add_argument("--max-parallel", type=int, default=None,
                         help="override run.max_parallel")
    run_cmd.add_argument("--budget-inr", type=float, default=None, help="override run.budget_inr")
    run_cmd.add_argument(
        "--allow-inert-budget", action="store_true",
        help="run even though pricing: has no usable rates and run.budget_inr therefore "
             "cannot be enforced. Nothing will be cost-capped.",
    )
    run_cmd.add_argument("--verbose", "-v", action="store_true")

    # `judge` deliberately takes a run_id and NOT a persona set to converse with: it reads
    # transcripts off disk and opens no socket to the target. Re-judging is therefore free of
    # ElevenLabs quota and reproducible against byte-identical input, which is the entire
    # reason the stages talk through files (INTERFACES §1 rule 5).
    judge_cmd = sub.add_parser(
        "judge", help="score the transcripts of an existing run (no conversations, no target)")
    judge_cmd.add_argument("run_id", nargs="?", default=None,
                           help="run id or path under runs/ (default: the most recent run)")
    judge_cmd.add_argument("--personas", default=None,
                           help="comma-separated persona ids (default: every transcript in the run)")
    judge_cmd.add_argument("--config", default=None, type=Path)
    judge_cmd.add_argument("--env", default=None, type=Path)
    judge_cmd.add_argument("--verbose", "-v", action="store_true")

    # `report` is the third and last stage, and it is the cheapest: it reads scorecards,
    # transcripts and run.json off disk and writes report.md + synthesis.json. No socket is
    # opened to the target, no ElevenLabs client is constructed on this path, and with
    # --no-llm it makes no network call at all (SYNTH_SPEC §0.1, §5).
    rep_cmd = sub.add_parser(
        "report", help="synthesise all scorecards of a run into report.md (reads files only)")
    rep_cmd.add_argument("run_id", nargs="?", default=None,
                         help="run id or path; defaults to the newest run")
    rep_cmd.add_argument("--personas", default=None,
                         help="comma-separated persona ids to include in the report")
    rep_cmd.add_argument("--no-llm", action="store_true",
                         help="skip the narrative LLM call; deterministic report only")
    rep_cmd.add_argument("--config", default=None, type=Path)
    rep_cmd.add_argument("--env", default=None, type=Path)
    rep_cmd.add_argument("--verbose", "-v", action="store_true")

    cfg_cmd = sub.add_parser("config", help="validate config + personas, make no API call")
    cfg_cmd.add_argument("--config", default=None, type=Path)
    cfg_cmd.add_argument("--env", default=None, type=Path)
    return parser


def _resolve_run_dir(cfg: Config, run_id: str | None) -> Path:
    """A run id, a path, or nothing (meaning: the newest run)."""
    out = cfg.run.out_dir if cfg.run.out_dir.is_absolute() else ROOT / cfg.run.out_dir
    if run_id:
        cand = Path(run_id)
        for p in (cand, out / run_id):
            if (p / "conversations").is_dir():
                return p
        raise FileNotFoundError(f"no run with conversations/ at {run_id!r} (looked in {out})")
    runs = sorted(p for p in out.glob("*/") if (p / "conversations").is_dir())
    if not runs:
        raise FileNotFoundError(f"no runs with conversations/ under {out} — run `spar run` first")
    return runs[-1]


def _load(args) -> Config:
    return load_config(
        Path(args.config) if args.config else ROOT / "config.yaml",
        Path(args.env) if args.env else ROOT / ".env",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not args.command:
        parser.print_help()
        return 2

    setup_logging(getattr(args, "verbose", False))

    try:
        cfg = _load(args)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.command == "config":
        print(f"config OK — {cfg.config_path}")
        for w in cfg.warnings:
            print(f"  ! {w}")
        return 0

    if args.command == "judge":
        from judge.judge import JudgeError, judge_run

        try:
            run_dir = _resolve_run_dir(cfg, args.run_id)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            return 1
        only = [s.strip() for s in args.personas.split(",") if s.strip()] if args.personas else None
        log.info("=" * 78)
        log.info("voice-spar judge   %s", run_dir.name)
        log.info("  judge model : %s  (persona was %s — bias separation)",
                 cfg.judge.model, cfg.persona_brain.model)
        log.info("  evidence    : %s", "verbatim required"
                 if cfg.judge_require_evidence else "NOT required")
        log.info("=" * 78)
        try:
            summary = asyncio.run(judge_run(run_dir, cfg, only=only))
        except JudgeError as exc:
            print(f"judge failed: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return 130
        log.info("-" * 78)
        log.info("scored %d, failed %d -> %s/scorecards/",
                 summary["judged"], len(summary["failed"]), run_dir)
        for f in summary["failed"]:
            log.error("  %s: %s", f["file"], f["error"])
        return 0 if summary["judged"] else 1

    if args.command == "report":
        # Inputs are checked before the import, so a mistyped run id is reported as a
        # mistyped run id rather than as whatever the import happened to hit first.
        try:
            run_dir = _resolve_run_dir(cfg, args.run_id)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            return 1
        # A run with no scorecards is not an empty report, it is a missing stage. Say which
        # stage, rather than letting the synthesizer report "0 personas" as if that were data.
        if not sorted((run_dir / "scorecards").glob("*.json")):
            print(f"no scorecards in {run_dir} — run 'spar judge' first", file=sys.stderr)
            return 1
        only = [s.strip() for s in args.personas.split(",") if s.strip()] if args.personas else None

        # Lazy, and inside the handler: importing synth pulls in agent.sarvam/httpx, and a
        # `spar run` has no business paying for that. The guard is narrow on purpose — only
        # "synth.report itself is absent" gets the friendly message; a ModuleNotFoundError
        # raised from INSIDE synth.report is a real broken dependency and must not be
        # disguised as "not installed yet".
        try:
            from synth.report import ReportError, generate_report
        except ModuleNotFoundError as exc:
            if exc.name not in ("synth", "synth.report"):
                raise
            print("the synthesizer is not installed yet — synth/report.py is missing",
                  file=sys.stderr)
            return 1
        log.info("=" * 78)
        log.info("voice-spar report  %s", run_dir.name)
        log.info("  reads       : scorecards/ + conversations/ + run.json — no target, no socket")
        log.info("  narrative   : %s", "OFF (--no-llm, deterministic only)" if args.no_llm
                 else f"{cfg.synthesizer.model}  t={cfg.synthesizer.temperature}")
        if only:
            # Stated out loud because it is a narrowing a reader could otherwise miss: the
            # filter trims the report's rows, it never trims the control gate or the
            # cross-persona uniqueness the bleed scan is computed from (SYNTH_SPEC §2.1).
            log.info("  personas    : %s  (report rows only — gate and bleed still span the run)",
                     ", ".join(only))
        log.info("=" * 78)
        try:
            summary = asyncio.run(
                generate_report(run_dir, cfg, only=only, use_llm=not args.no_llm))
        except ReportError as exc:
            print(f"report failed: {exc}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("\ninterrupted", file=sys.stderr)
            return 130
        # The gate is the one thing worth reading off the summary line: a run whose control
        # failed is not a weaker run, it is an uninterpretable one, and the reader must see
        # that before they see the path. (SYNTH_SPEC §2.8 / §4.3.)
        gate = summary["control_gate"]
        gate_status = gate["status"] if isinstance(gate, dict) else gate
        log.info("-" * 78)
        if gate_status == "pass":
            log.info("control gate: PASS")
        else:
            # "no control persona" is unanchored, not disproven — the distinction matters to
            # whoever reads this line, so it is not flattened into a single word (§2.8).
            log.error("control gate: %s — run %s; no cross-persona finding below is promoted "
                      "to a defect", str(gate_status).upper(),
                      "UNANCHORED" if gate_status == "no_control" else "INVALID")
        log.info("  findings    : %s", summary["n_findings"])
        for w in summary["warnings"] or ():
            log.warning("  ! %s", w)
        log.info("  report      : %s", summary["report_path"])
        log.info("  synthesis   : %s", summary["synthesis_path"])
        # A failed gate is a reported fact, not a CLI error: report.md was written correctly
        # and says so at the top. Exit 0 — the synthesizer did its job.
        return 0

    # CLI overrides, applied to the frozen dataclasses by rebuilding them
    from dataclasses import replace as _replace

    run_cfg = cfg.run
    if args.max_parallel is not None:
        run_cfg = _replace(run_cfg, max_parallel=max(1, args.max_parallel))
    if args.budget_inr is not None:
        run_cfg = _replace(run_cfg, budget_inr=float(args.budget_inr))
    if run_cfg is not cfg.run:
        cfg = _replace(cfg, run=run_cfg)

    only = [s.strip() for s in args.personas.split(",") if s.strip()] if args.personas else None

    try:
        manifest = asyncio.run(
            execute_run(cfg, only=only,
                        allow_inert_budget=bool(getattr(args, "allow_inert_budget", False)))
        )
    except BudgetGuardInert as exc:
        print(f"\nbudget guard is inert — refusing to start.\n\n  {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except persona_mod.PersonaError:
        return 1

    return 0 if manifest["totals"]["ok"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
