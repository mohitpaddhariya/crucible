"""voice-spar configuration — the single loader for `.env` + `config.yaml` + `personas/*.yaml`.

Contract: `docs/INTERFACES.md` §9. If this module and that document disagree, the document
wins — raise the conflict, do not "fix" it here.

Three jobs, in this order:

1. **Merge.** Secrets come *only* from the environment (`.env`, then `os.environ` on top so CI
   can inject). Everything else comes *only* from `config.yaml`. Neither half may hold the
   other's data: a secret-looking key in the YAML is an immediate hard failure.
2. **Validate everything, then raise once.** Never fail on the first problem, never
   warn-and-continue on a real one. A broken config produces exactly one `ConfigError` whose
   message lists every problem found, numbered, with the fix.
3. **Validate the persona YAMLs** (pydantic) before a single API call is made — including that
   `end_when.hard_stop.turns` exists on every persona. Without it two bots talk forever.

Nothing in this module ever logs, prints or returns a secret value. `Secrets.__repr__` masks;
`Config.redacted()` is the only form allowed into `run.json`.

Run it directly for a readable preflight:

    uv run --python 3.12 python config.py
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# --------------------------------------------------------------------------------------
# LLMConfig — defined by agent/sarvam.py (INTERFACES.md §4.5), which is another agent's file.
#
# config.py builds LLMConfig objects and hands them to SarvamClient(cfg=...), so the real class
# is imported when it exists. Until agent/sarvam.py lands, a structurally identical fallback
# keeps this module importable and testable. The two definitions must never diverge: if you
# change a field here, change it there, and prefer deleting this fallback once sarvam.py exists.
# --------------------------------------------------------------------------------------
try:  # pragma: no cover - depends on sibling agent's file landing
    from agent.sarvam import LLMConfig  # type: ignore[attr-defined]

    LLMCONFIG_SOURCE = "agent.sarvam"
except Exception:  # ImportError, or sarvam.py's own deps missing

    @dataclass(frozen=True)
    class LLMConfig:  # type: ignore[no-redef]
        """Mirror of `agent/sarvam.py`'s LLMConfig (INTERFACES.md §4.5)."""

        provider: str
        model: str
        temperature: float
        max_tokens: int
        timeout_s: float = 120.0

    LLMCONFIG_SOURCE = "config.py fallback"


# ======================================================================================
# Constants — the facts these checks are made of
# ======================================================================================

#: Live Sarvam chat models (docs/PREFLIGHT.md §5). `sarvam-m` is deprecated and returns 400.
SARVAM_MODELS: frozenset[str] = frozenset({"sarvam-30b", "sarvam-105b"})
DEAD_MODELS: frozenset[str] = frozenset({"sarvam-m"})

#: Hard rule 4. Sarvam models are reasoning models; reasoning cannot be disabled and it eats
#: the token budget *before* `content` is written. Below ~1200 the API returns `content: None`.
MIN_MAX_TOKENS = 2000

#: MEASURED 25 July 2026 against the live key. Sarvam rejects anything above this with a 400:
#: "max_tokens (6000) exceeds the maximum allowed for sarvam-30b for your subscription tier
#: (starter): 4096." Not recorded in PREFLIGHT — it only shows up once you try to give the
#: reasoning more headroom. Caught here so it is a startup error, not a mid-conversation 400.
MAX_MAX_TOKENS = 4096

#: The agent's own server-side cap (docs/PREFLIGHT.md §2). Our wall-clock cap must stay under it.
AGENT_SERVER_CAP_S = 600

SUPPORTED_ADAPTERS: frozenset[str] = frozenset({"elevenlabs"})
SUPPORTED_AUTH: frozenset[str] = frozenset({"header", "signed"})

#: `text` is Level 0 and stays the default; `audio` is Level 1 half-duplex (LEVEL1_SPEC §6).
#: Every line of audio code sits behind this switch — `mode: text` is byte-for-byte Level 0.
SUPPORTED_MODES: frozenset[str] = frozenset({"text", "audio"})


# --------------------------------------------------------------------------------------
# Level 1 speech constants — every one of these is a MEASUREMENT from
# `scripts/spike_sarvam_speech.py` against the live Sarvam key (26 July 2026), not a guess.
# Raw evidence: runs/_spike/sarvam_speech_result{,_phase2,_phase3}.json.
# --------------------------------------------------------------------------------------

#: The only live `/speech-to-text` model. Probe G_stt: 200 with `saarika:v2.5`, and the
#: model list has no other member that transcribes.
STT_MODELS: frozenset[str] = frozenset({"saarika:v2.5"})

#: `saaras` is a *different family on a different endpoint* (`/speech-to-text-translate`).
#: It answers 200 — and TRANSLATES: "Arre yaar, 10% off..." came back "But man, 10% off...".
#: Silently anglicising the Hinglish is the exact opposite of what the §2.1 fidelity
#: cross-check exists to measure, so it is rejected here rather than quietly accepted.
STT_TRANSLATE_MODELS: frozenset[str] = frozenset({"saaras:v2.5"})

#: Live Bulbul TTS models. `bulbul:v1` resolves but is an alias with no separate roster.
TTS_MODELS: frozenset[str] = frozenset({"bulbul:v2", "bulbul:v3"})

#: Per-model speaker rosters. These are NOT interchangeable: asking bulbul:v2 for a v3
#: speaker is an HTTP 400 ("Speaker 'aditya' is not compatible with model bulbul:v2"), and
#: the generic "Available speakers are: ..." message the API returns on an unknown name
#: lists all 44 regardless of model, which is how you end up debugging a 400 for an hour.
BULBUL_SPEAKERS: dict[str, frozenset[str]] = {
    "bulbul:v2": frozenset({"anushka", "abhilash", "manisha", "vidya", "arya", "karun", "hitesh"}),
    "bulbul:v3": frozenset({
        "aditya", "ritu", "ashutosh", "priya", "neha", "rahul", "pooja", "rohan", "simran",
        "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun", "manan", "sumit", "roopa",
        "kabir", "aayan", "shubh", "advait", "anand", "tanya", "tarun", "sunny", "mani", "gokul",
        "vijay", "shruti", "suhani", "mohit", "kavitha", "rehan", "soham", "rupali", "niharika",
    }),
}

#: The agent negotiates `pcm_16000` in both directions (`conversation_initiation_metadata`
#: echoes `agent_output_audio_format == user_input_audio_format == "pcm_16000"`). Bulbul's
#: `speech_sample_rate` DEFAULTS TO 22050 and happily returns 200 at any of
#: 8000/16000/22050/24000/44100/48000 — so getting this wrong is not an error, it is
#: wrong-speed audio that Tara transcribes into nonsense with nothing logged anywhere.
REQUIRED_SAMPLE_RATE_HZ = 16000
_SAMPLE_RATE_KEYS: tuple[str, ...] = ("speech_sample_rate", "input_sample_rate", "output_sample_rate")

#: LEVEL1_SPEC §4.3. Safe window ~[3 s, 8 s]: below it the mic shuts before `user_transcript`
#: and turn 2 deadlocks forever; above it Tara's ~10 s `turn_timeout` starts endpointing empty
#: user turns, she nudges twice and hangs up at 59 s. 8.0 is the ceiling, not a suggestion.
MIC_HOLD_MAX_S = 8.0
MIC_HOLD_MIN_SAFE_S = 3.0

#: LEVEL1_SPEC §0.2 / §9.4. Calibrated over 8 turns, confirmed over 11 more.
TURN_DETECTOR_DEFAULTS: dict[str, float] = {
    "speech_peak_min": 3000,
    "quiet_frames": 5,
    "quiet_wall_s": 1.5,
}

#: LEVEL1_SPEC §4.4: ~200 chars ≈ 12 s of playout at the measured 17 chars/s.
PERSONA_CHAR_CAP_DEFAULT = 200

#: LEVEL1_SPEC §1.2: worst measured turn-end-to-turn-end cycle in audio mode.
AUDIO_CYCLE_WORST_S = 24.0

SPEECH_DEFAULTS: dict[str, Any] = {
    "stt": "saarika:v2.5",
    "stt_cross_check": True,
    "tts": "bulbul:v2",
    "tts_speaker": "anushka",
    "speech_sample_rate": REQUIRED_SAMPLE_RATE_HZ,
    "input_sample_rate": REQUIRED_SAMPLE_RATE_HZ,
    "output_sample_rate": REQUIRED_SAMPLE_RATE_HZ,
    "mic_hold_bound_s": MIC_HOLD_MAX_S,
    "persona_char_cap": PERSONA_CHAR_CAP_DEFAULT,
}
SPEECH_KNOWN_KEYS: frozenset[str] = frozenset(set(SPEECH_DEFAULTS) | {"turn_detector"})

REQUIRED_ENV: tuple[str, ...] = ("ELEVENLABS_API_KEY", "ELEVENLABS_AGENT_ID", "SARVAM_API_KEY")

KNOWN_TOP_LEVEL: frozenset[str] = frozenset(
    {"target", "persona_brain", "referee", "judge", "synthesizer", "speech", "run", "rubric", "pricing"}
)

#: The 17 dynamic_variables the live agent's templates declare (docs/PREFLIGHT.md §4).
#: Exactly these — no more, no fewer. ElevenLabs silently ignores unknown keys, which hides typos.
#:
#: The last six arrived with the white-label agent (28 July 2026). They are what makes the
#: target generic: the brand, the voice's name and the service category are injected per call
#: instead of being written into the agent, so no real customer's identity ever reaches an
#: artifact. The three offer bounds are the agent's negotiating limits, and the ceiling is the
#: same number a persona's ground_truth.discount_ceiling_pct asserts — one source of truth for
#: what the agent was told and what the judge checks it against.
SCENARIO_VAR_KEYS: tuple[str, ...] = (
    "subscriber_name",
    "call_reason",
    "call_intro",
    "plan_name",
    "amount_inr",
    "expiry_date",
    "content_hook",
    "offer_text",
    "renewal_date",
    "next_retry_date",
    "failure_reason",
    "agent_name",
    "brand_name",
    "service_type",
    "offer_floor_pct",
    "offer_default_pct",
    "offer_ceiling_pct",
)
#: Verified accepted as "" by the live agent. The other eight must be non-empty.
SCENARIO_VARS_MAY_BE_EMPTY: frozenset[str] = frozenset({"renewal_date", "next_retry_date", "failure_reason"})

END_WHEN_HARD_KEYS: frozenset[str] = frozenset({"turns_over", "seconds_over"})
END_WHEN_SOFT_KEYS: frozenset[str] = frozenset(
    {"goal_reached", "agent_offers_human_handoff", "persona_walked_away"}
)

PERSONA_TOP_LEVEL_REQUIRED: tuple[str, ...] = (
    "id",
    "name",
    "identity",
    "language",
    "behaviour",
    "goal",
    "scenario",
    "end_when",
)
PERSONA_TOP_LEVEL_KNOWN: frozenset[str] = frozenset(
    set(PERSONA_TOP_LEVEL_REQUIRED) | {"stresses", "control", "voice"}
)

_KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: A key in config.yaml matching this is a hard failure — secrets belong in .env only.
_SECRET_KEY_RE = re.compile(r"(api[_-]?key|secret|token|password|passwd|credential)", re.IGNORECASE)
#: ...except these, which merely *contain* the substring "token". The naive regex from the
#: contract matches `max_tokens` on every single config; it must not.
_SECRET_KEY_ALLOWLIST: frozenset[str] = frozenset(
    {"max_tokens", "prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens"}
)
#: Obvious credential *values* pasted into the YAML by mistake.
_SECRET_VALUE_RE = re.compile(r"^(sk[-_][A-Za-z0-9]{12,}|xi[-_][A-Za-z0-9]{12,})$")


# ======================================================================================
# Errors
# ======================================================================================


class ConfigError(Exception):
    """Raised once, with every problem found. Never one problem at a time."""

    def __init__(self, message: str, *, problems: list[str] | None = None, warnings: list[str] | None = None):
        super().__init__(message)
        self.problems: list[str] = list(problems or [])
        self.warnings: list[str] = list(warnings or [])


# ======================================================================================
# Config dataclasses (INTERFACES.md §9)
# ======================================================================================


def _mask(value: str, keep: int = 3) -> str:
    """`sk_abc123...` -> `sk_***`. Never returns any part of the entropy."""
    if not value:
        return "<empty>"
    head = value[:keep] if len(value) > keep else ""
    return f"{head}***"


@dataclass(frozen=True)
class Secrets:
    """Environment-only. Never sourced from config.yaml, never written to disk unmasked."""

    elevenlabs_api_key: str
    elevenlabs_agent_id: str
    sarvam_api_key: str

    def __repr__(self) -> str:
        return (
            f"Secrets(elevenlabs_api_key='{_mask(self.elevenlabs_api_key)}', "
            f"elevenlabs_agent_id='{_mask(self.elevenlabs_agent_id, keep=6)}', "
            f"sarvam_api_key='{_mask(self.sarvam_api_key)}')"
        )

    __str__ = __repr__


@dataclass(frozen=True)
class TargetConfig:
    adapter: str
    mode: str
    agent_id: str  # from .env — echoed into every artifact per §8.2; an identifier, not a credential
    auth: Literal["header", "signed"] = "header"


@dataclass(frozen=True)
class RunConfig:
    personas: list[str] | Literal["all"]
    max_parallel: int
    budget_inr: float
    out_dir: Path
    max_conversation_seconds: int = 540


@dataclass(frozen=True)
class RefereeConfig:
    provider: str
    model: str
    temperature: float
    max_tokens: int
    window_turns: int = 6
    enabled: bool = True


@dataclass(frozen=True)
class Config:
    target: TargetConfig
    persona_brain: LLMConfig
    referee: RefereeConfig
    judge: LLMConfig
    synthesizer: LLMConfig
    run: RunConfig
    rubric: dict[str, int]
    #: INR per 1M tokens, `{model: {"input": float, "output": float}}` — the §9.3 YAML shape.
    pricing: dict[str, dict[str, float]]
    secrets: Secrets
    config_path: Path
    personas_dir: Path
    judge_require_evidence: bool = True
    speech: dict[str, Any] = field(default_factory=dict)  # Level 1 only, unused here
    warnings: tuple[str, ...] = ()

    # -- helpers -----------------------------------------------------------------

    def price_per_1m(self, model: str, direction: Literal["input", "output"]) -> float | None:
        """INR per 1M tokens, or None when unpriced. Never guess — None becomes `null` in the
        artifact plus a warning (§8.3). Silently zeroing a cost hides an overspend.

        A rate of exactly 0.0 counts as UNPRICED, not as free. Nothing here is free, and the
        shipped `pricing:` block is 0.0 precisely because the real rates were never read. As
        a number it was worse than useless: it made every cost compute to a confident 0.0, so
        `spent_inr` never moved and `run.budget_inr` could not fire no matter what was spent.
        As None it disables the guard loudly (see BudgetTracker.inert) instead of silently.
        """
        rates = self.pricing.get(model)
        if not isinstance(rates, dict):
            return None
        value = rates.get(direction)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return None
        return float(value) if float(value) != 0.0 else None

    def redacted(self) -> dict[str, Any]:
        """The only form of this object allowed into `run.json`.

        `speech` appears only in audio mode. A text run's `run.json` is byte-identical to
        Level 0's — the §7 backward-compatibility contract applies to what we WRITE, not
        just to what we read.
        """
        extra: dict[str, Any] = {"speech": dict(self.speech)} if self.target.mode == "audio" else {}
        return {
            "config_path": str(self.config_path),
            "personas_dir": str(self.personas_dir),
            "target": {
                "adapter": self.target.adapter,
                "mode": self.target.mode,
                "agent_id": self.target.agent_id,  # §8.2 requires this verbatim in the artifact
                "auth": self.target.auth,
            },
            "persona_brain": _llm_dict(self.persona_brain),
            "referee": {
                "provider": self.referee.provider,
                "model": self.referee.model,
                "temperature": self.referee.temperature,
                "max_tokens": self.referee.max_tokens,
                "window_turns": self.referee.window_turns,
                "enabled": self.referee.enabled,
            },
            "judge": {**_llm_dict(self.judge), "require_evidence": self.judge_require_evidence},
            "synthesizer": _llm_dict(self.synthesizer),
            "run": {
                "personas": self.run.personas,
                "max_parallel": self.run.max_parallel,
                "budget_inr": self.run.budget_inr,
                "out_dir": str(self.run.out_dir),
                "max_conversation_seconds": self.run.max_conversation_seconds,
            },
            "rubric": dict(self.rubric),
            "pricing": {k: dict(v) for k, v in self.pricing.items()},
            "secrets": {
                "elevenlabs_api_key": "***",
                "elevenlabs_agent_id": "***",
                "sarvam_api_key": "***",
            },
            "warnings": list(self.warnings),
            **extra,
        }


def _llm_dict(cfg: LLMConfig) -> dict[str, Any]:
    return {
        "provider": cfg.provider,
        "model": cfg.model,
        "temperature": cfg.temperature,
        "max_tokens": cfg.max_tokens,
        "timeout_s": cfg.timeout_s,
    }


# ======================================================================================
# Persona YAML validation (pydantic) — INTERFACES.md §4.2 and §7.3
#
# These models are the structural gate only. agent/persona.py builds the live Persona object
# and may import these to avoid a second, divergent definition of the same rules.
# ======================================================================================


class IdentityModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    who: str = Field(min_length=1)
    situation: str = Field(min_length=1)


class LanguageModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    primary: str = Field(min_length=1)
    rule: str = Field(min_length=1)


class BehaviourModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    tone: str = Field(min_length=1)
    tactics: list[str] = Field(min_length=1)
    arc: str = Field(min_length=1)
    never: list[str] = Field(default_factory=list)


class GoalModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    wants: str = Field(min_length=1)
    accepts: str = Field(min_length=1)
    walks_away_after: str = Field(min_length=1)


class ScenarioVarsModel(BaseModel):
    """The 17 ElevenLabs dynamic_variables. Exactly these keys; every value a string.

    `extra="forbid"` is load-bearing: ElevenLabs ignores unknown keys silently, so a typo
    would render as an unfilled `{{placeholder}}` in the agent's opening line and nowhere else.
    A MISSING key is worse than a wrong one — the agent speaks the literal braces aloud.
    """

    model_config = ConfigDict(extra="forbid")

    subscriber_name: str
    call_reason: str
    call_intro: str
    plan_name: str
    amount_inr: str  # STRING. `1499` (int) is an error — pydantic will not coerce it.
    expiry_date: str
    content_hook: str
    offer_text: str
    renewal_date: str
    next_retry_date: str
    failure_reason: str
    # White-label identity. The agent under test ships with no brand of its own; these three
    # decide who it claims to be on this call, which is why no real customer appears anywhere.
    agent_name: str
    brand_name: str
    service_type: str
    # Offer bounds. STRINGS, like every other dynamic variable. offer_ceiling_pct is the
    # agent's absolute arithmetic limit and must equal ground_truth.discount_ceiling_pct.
    offer_floor_pct: str
    offer_default_pct: str
    offer_ceiling_pct: str

    @model_validator(mode="after")
    def _non_empty(self) -> "ScenarioVarsModel":
        empty = [
            k
            for k in SCENARIO_VAR_KEYS
            if k not in SCENARIO_VARS_MAY_BE_EMPTY and not str(getattr(self, k)).strip()
        ]
        if empty:
            raise ValueError(
                f"must be non-empty: {', '.join(empty)} "
                f"(only {', '.join(sorted(SCENARIO_VARS_MAY_BE_EMPTY))} may be \"\")"
            )
        return self


class GroundTruthModel(BaseModel):
    """Judge-only. Never rendered into any prompt sent during the conversation."""

    model_config = ConfigDict(extra="allow")

    discount_ceiling_pct: int = Field(ge=0, le=100)
    offer_summary: str = Field(min_length=1)
    valid_plan_names: list[str] = Field(min_length=1)
    valid_prices_inr: list[int] = Field(min_length=1)
    valid_dates: list[str] = Field(default_factory=list)
    claims_agent_may_make: list[str] = Field(default_factory=list)
    claims_agent_must_not_make: list[str] = Field(min_length=1)


class ScenarioModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vars: ScenarioVarsModel
    ground_truth: GroundTruthModel
    customer_brief: str = Field(min_length=1, max_length=400)


class HardStopModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turns: int = Field(ge=1)


class EndWhenModel(BaseModel):
    """RUNNER ONLY. This block must never reach the persona LLM (INTERFACES.md §4.3)."""

    model_config = ConfigDict(extra="forbid")

    any: list[dict[str, Any]] = Field(default_factory=list)
    hard_stop: HardStopModel  # mandatory, no default — without it two bots talk forever

    @field_validator("any")
    @classmethod
    def _check_any(cls, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = END_WHEN_HARD_KEYS | END_WHEN_SOFT_KEYS
        for i, item in enumerate(items):
            if not isinstance(item, dict) or len(item) != 1:
                raise ValueError(f"any[{i}] must be a single-key mapping, e.g. `- turns_over: 12`")
            (key, value), = item.items()
            if key not in allowed:
                raise ValueError(f"any[{i}]: unknown condition '{key}' (allowed: {', '.join(sorted(allowed))})")
            if key in END_WHEN_HARD_KEYS:
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError(f"any[{i}].{key} must be an integer >= 1, got {value!r}")
            elif not isinstance(value, bool):
                raise ValueError(f"any[{i}].{key} must be true or false, got {value!r}")
        return items


class PersonaFileModel(BaseModel):
    """Structural shape of one `personas/<id>.yaml`."""

    model_config = ConfigDict(extra="allow")  # unknown top-level keys are a warning, not an error

    id: str
    name: str = Field(min_length=1)
    stresses: str | None = None
    control: bool = False
    identity: IdentityModel
    language: LanguageModel
    behaviour: BehaviourModel
    goal: GoalModel
    scenario: ScenarioModel
    end_when: EndWhenModel
    voice: dict[str, Any] | None = None  # Level 1 only; absence is not an error

    @field_validator("id")
    @classmethod
    def _kebab(cls, v: str) -> str:
        if not _KEBAB.match(v):
            raise ValueError(f"'{v}' must be kebab-case, e.g. 'price-haggler'")
        return v


def validate_persona_file(
    path: Path, *, audio_seconds_budget: int | None = None
) -> tuple[list[str], list[str]]:
    """Validate one persona YAML. Returns `(errors, warnings)` — every problem, never just the first.

    Guarantees checked here that nothing downstream re-checks:
      * `end_when.hard_stop.turns` exists and is >= 1 (mandatory on EVERY persona)
      * `scenario.vars` is exactly the 11 declared keys, all strings
      * `scenario.customer_brief` does not leak `scenario.vars.offer_text`
      * `voice.model` / `voice.speaker` are a live, *compatible* Bulbul pair

    `audio_seconds_budget` is `run.max_conversation_seconds` in audio mode and `None`
    everywhere else; passing None skips the audio turn-budget check entirely, which is how
    text mode stays byte-identical (LEVEL1_SPEC §4.4, §7).
    """
    errors: list[str] = []
    warnings: list[str] = []
    where = path.name

    if not path.is_file():
        return ([f"{where}: persona file not found at {path}"], warnings)

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return ([f"{where}: not valid YAML — {exc}"], warnings)

    if not isinstance(raw, dict):
        return ([f"{where}: top level must be a mapping, got {type(raw).__name__}"], warnings)

    for key in sorted(set(raw) - PERSONA_TOP_LEVEL_KNOWN):
        warnings.append(f"{where}: unknown top-level key '{key}' (typo? it will be ignored)")

    try:
        persona = PersonaFileModel.model_validate(raw)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "<root>"
            errors.append(f"{where}: {loc}: {err['msg']}")
        # hard_stop is mandatory and its absence is the single most dangerous omission — say so.
        if not isinstance(raw.get("end_when"), dict) or "hard_stop" not in (raw.get("end_when") or {}):
            errors.append(
                f"{where}: end_when.hard_stop.turns is MANDATORY on every persona. "
                f"Without it there is no nuclear stop and two bots talk forever."
            )
        return (errors, warnings)

    # ---- cross-field rules that pydantic cannot express (INTERFACES.md §7.3) ----
    if persona.id != path.stem:
        errors.append(f"{where}: id '{persona.id}' must equal the filename stem '{path.stem}'")

    vars_ = persona.scenario.vars
    brief = persona.scenario.customer_brief
    offer = vars_.offer_text.strip()

    # The discount is Tara's card to play. A persona that already knows a 10% offer exists
    # opens by demanding more, and the objection-handling test is destroyed.
    if offer and offer.lower() in brief.lower():
        errors.append(
            f"{where}: customer_brief_leaks_offer — customer_brief restates scenario.vars.offer_text "
            f"({offer!r}). The customer must not know the offer before the agent makes it."
        )

    ceiling = persona.scenario.ground_truth.discount_ceiling_pct
    if str(ceiling) not in offer:
        warnings.append(
            f"{where}: scenario_ceiling_mismatch — ground_truth.discount_ceiling_pct is {ceiling} "
            f"but that number does not appear in vars.offer_text ({offer!r}); "
            f"instruction adherence cannot be scored objectively"
        )

    who = persona.identity.who.lower()
    subscriber = vars_.subscriber_name.strip()
    if subscriber and subscriber.lower() not in who and subscriber.lower() not in persona.identity.situation.lower():
        warnings.append(
            f"{where}: scenario.vars.subscriber_name '{subscriber}' is not mentioned in identity — "
            f"check the agent and the persona are talking about the same person"
        )

    hard_stop = persona.end_when.hard_stop.turns
    for item in persona.end_when.any:
        (key, value), = item.items()
        if key == "turns_over" and isinstance(value, int) and value > hard_stop:
            warnings.append(
                f"{where}: end_when.any.turns_over ({value}) exceeds hard_stop.turns ({hard_stop}) — "
                f"hard_stop always wins, so turns_over can never fire"
            )

    # ---- Level 1: casting (LEVEL1_SPEC §6). Absence is still not an error. ----
    errors.extend(_voice_errors(persona.voice, where))
    warnings.extend(_voice_warnings(persona.voice, where))

    # ---- Level 1: turn budget, not second budget (LEVEL1_SPEC §4.4) ----
    if audio_seconds_budget is not None:
        turns_over = next(
            (v for it in persona.end_when.any for k, v in it.items() if k == "turns_over"), None
        )
        budget_turns = turns_over if isinstance(turns_over, int) else hard_stop
        needed = budget_turns * AUDIO_CYCLE_WORST_S
        if needed > audio_seconds_budget:
            warnings.append(
                f"{where}: {budget_turns} turns x {AUDIO_CYCLE_WORST_S:.0f}s (worst measured audio "
                f"cycle) = {needed:.0f}s, over run.max_conversation_seconds "
                f"({audio_seconds_budget}s). ~22 s per cycle is irreducible realtime audio — "
                f"listening and talking — so no LLM speed-up buys it back. This conversation "
                f"will be cut off mid-argument. Budget turns, not seconds (LEVEL1_SPEC §4.4)."
            )

    return (errors, warnings)


def _voice_errors(voice: dict[str, Any] | None, where: str) -> list[str]:
    """Hard problems in a persona's `voice:` block. An absent block is not one of them."""
    errors: list[str] = []
    if not voice:
        return errors
    if not isinstance(voice, dict):
        return [f"{where}: voice: must be a mapping of {{model, speaker}}, got {type(voice).__name__}"]

    model = voice.get("model")
    if model is not None and (not isinstance(model, str) or model not in TTS_MODELS):
        errors.append(
            f"{where}: voice.model {model!r} is not a live Bulbul model. "
            f"Allowed: {', '.join(sorted(TTS_MODELS))}"
        )
        model = None

    speaker = voice.get("speaker")
    if speaker is not None and not isinstance(speaker, str):
        errors.append(f"{where}: voice.speaker must be a string or null, got {type(speaker).__name__}")
    elif isinstance(speaker, str) and speaker.strip() and model is not None:
        roster = BULBUL_SPEAKERS.get(model, frozenset())
        if speaker not in roster:
            other = sorted(m for m, names in BULBUL_SPEAKERS.items() if m != model and speaker in names)
            hint = (
                f" It is a {other[0]} speaker; the rosters are disjoint and Sarvam answers HTTP 400 "
                f"'Speaker \\'{speaker}\\' is not compatible with model {model}'."
                if other else ""
            )
            errors.append(
                f"{where}: voice.speaker '{speaker}' is not in the {model} roster.{hint} "
                f"{model} speakers: {', '.join(sorted(roster))}"
            )

    pace = voice.get("pace")
    if pace is not None and (isinstance(pace, bool) or not isinstance(pace, (int, float)) or pace <= 0):
        errors.append(f"{where}: voice.pace must be a positive number, got {pace!r}")
    return errors


def _voice_warnings(voice: dict[str, Any] | None, where: str) -> list[str]:
    warnings: list[str] = []
    if not isinstance(voice, dict):
        return warnings

    speaker = voice.get("speaker")
    uncast = speaker is None or (isinstance(speaker, str) and not speaker.strip())
    if voice.get("model") is not None and uncast:
        warnings.append(
            f"{where}: voice.model is set but voice.speaker is not — audio mode will fall back to "
            f"speech.tts_speaker and every uncast persona will sound like the same person."
        )

    pace = voice.get("pace")
    if isinstance(pace, (int, float)) and not isinstance(pace, bool) and not (0.3 <= float(pace) <= 3.0):
        warnings.append(
            f"{where}: voice.pace {pace} is outside Bulbul's documented 0.3-3.0 range "
            f"(UNVERIFIED — our spike never probed the bounds, so this is a doc claim, not a "
            f"measurement). Out-of-range values may 400 at synthesis time, mid-conversation."
        )
    return warnings


def validate_persona_dir(
    personas_dir: Path,
    ids: list[str] | Literal["all"],
    *,
    audio_seconds_budget: int | None = None,
) -> tuple[list[str], list[str]]:
    """Validate every selected persona YAML. Returns `(errors, warnings)`.

    `audio_seconds_budget` is forwarded to `validate_persona_file`; None (the default, and
    what text mode passes) disables the audio-only turn-budget check.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not personas_dir.is_dir():
        return ([f"personas directory not found: {personas_dir}"], warnings)

    if ids == "all":
        paths = sorted(p for p in personas_dir.glob("*.yaml") if not p.name.startswith("_"))
        if not paths:
            return ([f"no persona YAMLs found in {personas_dir} (run.personas is 'all')"], warnings)
    else:
        paths = []
        for pid in ids:
            path = personas_dir / f"{pid}.yaml"
            if not path.is_file():
                errors.append(f"run.personas lists '{pid}' but {path} does not exist")
            else:
                paths.append(path)

    for path in paths:
        file_errors, file_warnings = validate_persona_file(
            path, audio_seconds_budget=audio_seconds_budget
        )
        errors.extend(file_errors)
        warnings.extend(file_warnings)

    return (errors, warnings)


# ======================================================================================
# .env + config.yaml readers
# ======================================================================================


def _read_env(env_path: Path) -> dict[str, str]:
    """`.env` -> dict, then `os.environ` on top so CI can inject without editing a file.

    Interpolation is off: a key containing `$` must survive verbatim.
    """
    values: dict[str, str] = {}
    if env_path.is_file():
        for key, value in dotenv_values(env_path, interpolate=False).items():
            if value is not None:
                values[key] = value
    for key in REQUIRED_ENV:
        from_environ = os.environ.get(key)
        if from_environ:
            values[key] = from_environ  # os.environ wins
    return {k: v.strip() for k, v in values.items()}


def _scan_for_secrets(node: Any, path: str = "") -> list[str]:
    """Find anything that looks like a credential inside config.yaml.

    config.yaml is a shared, reviewable, *committable-looking* file — .gitignore is the only
    thing keeping it out of git. A key pasted here would leak the first time someone relaxes
    that. Matching on both key names and obvious value formats.
    """
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            key_s = str(key)
            if key_s not in _SECRET_KEY_ALLOWLIST and _SECRET_KEY_RE.search(key_s):
                hits.append(f"key '{here}' looks like a secret")
            hits.extend(_scan_for_secrets(value, here))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            hits.extend(_scan_for_secrets(item, f"{path}[{i}]"))
    elif isinstance(node, str) and _SECRET_VALUE_RE.match(node.strip()):
        hits.append(f"value at '{path}' looks like an API key")
    return hits


def _load_yaml(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        raise ConfigError(
            f"config.yaml not found at {config_path}\n\n"
            f"  Create it from the documented defaults:\n\n"
            f"      cp config.example.yaml config.yaml\n\n"
            f"  config.yaml is gitignored on purpose. There is no silent default — a run must\n"
            f"  never start against a config nobody chose."
        )
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{config_path} is not valid YAML:\n\n  {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path}: top level must be a mapping, got {type(raw).__name__}")

    leaks = _scan_for_secrets(raw)
    if leaks:
        bullets = "\n".join(f"    - {h}" for h in leaks)
        raise ConfigError(
            f"SECRET IN config.yaml — refusing to load {config_path}\n\n"
            f"{bullets}\n\n"
            f"  Secrets live in .env ONLY ({', '.join(REQUIRED_ENV)}).\n"
            f"  Remove the value from config.yaml, then rotate it — assume it is compromised."
        )
    return raw


# ======================================================================================
# Section parsers — each appends to `problems` and returns a best-effort object
# ======================================================================================


def _section(raw: dict[str, Any], name: str, problems: list[str]) -> dict[str, Any]:
    node = raw.get(name)
    if node is None:
        problems.append(f"config.yaml: missing required section '{name}:'")
        return {}
    if not isinstance(node, dict):
        problems.append(f"config.yaml: '{name}:' must be a mapping, got {type(node).__name__}")
        return {}
    return node


def _req(node: dict[str, Any], section: str, key: str, kind: type, problems: list[str], default: Any) -> Any:
    """Required, typed scalar. `True` is never an int and never a float — YAML makes that easy to hit."""
    if key not in node:
        problems.append(f"config.yaml: {section}.{key} is required (expected {kind.__name__})")
        return default

    value = node[key]
    if isinstance(value, bool) and kind is not bool:
        ok = False
    elif kind is float:
        ok = isinstance(value, (int, float))
    else:
        ok = isinstance(value, kind)

    if not ok:
        problems.append(
            f"config.yaml: {section}.{key} must be {kind.__name__}, got {type(value).__name__} ({value!r})"
        )
        return default
    return float(value) if kind is float else value


def _llm_section(
    raw: dict[str, Any], name: str, problems: list[str], *, default_timeout: float = 120.0
) -> LLMConfig:
    node = _section(raw, name, problems)
    provider = _req(node, name, "provider", str, problems, "sarvam") if node else "sarvam"
    model = _req(node, name, "model", str, problems, "sarvam-30b") if node else "sarvam-30b"
    temperature = _req(node, name, "temperature", float, problems, 0.0) if node else 0.0
    max_tokens = _req(node, name, "max_tokens", int, problems, MIN_MAX_TOKENS) if node else MIN_MAX_TOKENS
    timeout_s = node.get("timeout_s", default_timeout)

    if provider != "sarvam":
        problems.append(f"config.yaml: {name}.provider must be 'sarvam' at Level 0, got {provider!r}")
    if model in DEAD_MODELS:
        problems.append(
            f"config.yaml: {name}.model '{model}' is DEPRECATED and returns HTTP 400 "
            f"(docs/PREFLIGHT.md §5). Use one of: {', '.join(sorted(SARVAM_MODELS))}"
        )
    elif model not in SARVAM_MODELS:
        problems.append(
            f"config.yaml: {name}.model '{model}' is not a live Sarvam model. "
            f"Allowed: {', '.join(sorted(SARVAM_MODELS))}"
        )
    if isinstance(max_tokens, int) and max_tokens < MIN_MAX_TOKENS:
        problems.append(
            f"config.yaml: {name}.max_tokens = {max_tokens} — must be >= {MIN_MAX_TOKENS}. "
            f"Sarvam models are reasoning models and reasoning cannot be disabled; it consumes "
            f"the budget before `content` is written, so below ~1200 the API returns content: None "
            f"on every call (docs/PREFLIGHT.md §5, hard rule 4)."
        )
    if isinstance(max_tokens, int) and max_tokens > MAX_MAX_TOKENS:
        problems.append(
            f"config.yaml: {name}.max_tokens = {max_tokens} — must be <= {MAX_MAX_TOKENS}. "
            f"Sarvam rejects anything higher on this subscription tier with HTTP 400 "
            f"(measured 25 July 2026). Raising the budget is not a lever for the "
            f"content: None problem; changing model is."
        )
    if isinstance(temperature, (int, float)) and not (0.0 <= float(temperature) <= 2.0):
        problems.append(f"config.yaml: {name}.temperature must be between 0.0 and 2.0, got {temperature}")
    if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or timeout_s <= 0:
        problems.append(f"config.yaml: {name}.timeout_s must be a positive number, got {timeout_s!r}")
        timeout_s = default_timeout

    return LLMConfig(
        provider=str(provider),
        model=str(model),
        temperature=float(temperature),
        max_tokens=int(max_tokens),
        timeout_s=float(timeout_s),
    )


def _referee_section(raw: dict[str, Any], problems: list[str]) -> RefereeConfig:
    # Absent is legal: config.example.yaml predates §9.3, so a fresh `cp` must still work.
    node = raw.get("referee")
    if node is None:
        node = {}
    elif not isinstance(node, dict):
        problems.append("config.yaml: 'referee:' must be a mapping")
        node = {}

    provider = node.get("provider", "sarvam")
    model = node.get("model", "sarvam-30b")
    temperature = node.get("temperature", 0.0)
    max_tokens = node.get("max_tokens", MIN_MAX_TOKENS)
    window_turns = node.get("window_turns", 6)
    enabled = node.get("enabled", True)

    if model in DEAD_MODELS or model not in SARVAM_MODELS:
        problems.append(
            f"config.yaml: referee.model '{model}' is not a live Sarvam model. "
            f"Allowed: {', '.join(sorted(SARVAM_MODELS))}"
        )
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < MIN_MAX_TOKENS:
        problems.append(
            f"config.yaml: referee.max_tokens = {max_tokens!r} — must be an int >= {MIN_MAX_TOKENS}. "
            f"The referee is a reasoning call too; lowering it returns content: None."
        )
        max_tokens = MIN_MAX_TOKENS
    elif max_tokens > MAX_MAX_TOKENS:
        problems.append(
            f"config.yaml: referee.max_tokens = {max_tokens} — must be <= {MAX_MAX_TOKENS}, "
            f"the subscription tier's hard ceiling (Sarvam 400s above it)."
        )
        max_tokens = MAX_MAX_TOKENS
    if not isinstance(window_turns, int) or isinstance(window_turns, bool) or window_turns < 1:
        problems.append(f"config.yaml: referee.window_turns must be an int >= 1, got {window_turns!r}")
        window_turns = 6
    if not isinstance(enabled, bool):
        problems.append(f"config.yaml: referee.enabled must be true or false, got {enabled!r}")
        enabled = True

    return RefereeConfig(
        provider=str(provider),
        model=str(model),
        temperature=float(temperature) if isinstance(temperature, (int, float)) else 0.0,
        max_tokens=int(max_tokens),
        window_turns=int(window_turns),
        enabled=bool(enabled),
    )


def _run_section(raw: dict[str, Any], base_dir: Path, problems: list[str]) -> RunConfig:
    node = _section(raw, "run", problems)

    personas: list[str] | Literal["all"] = "all"
    value = node.get("personas", "all")
    if value == "all":
        personas = "all"
    elif isinstance(value, list) and value and all(isinstance(x, str) for x in value):
        personas = list(value)
    else:
        problems.append(
            f"config.yaml: run.personas must be 'all' or a non-empty list of persona ids, got {value!r}"
        )

    max_parallel = node.get("max_parallel", 4)
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or max_parallel < 1:
        problems.append(f"config.yaml: run.max_parallel must be an int >= 1, got {max_parallel!r}")
        max_parallel = 1

    budget_inr = node.get("budget_inr")
    if not isinstance(budget_inr, (int, float)) or isinstance(budget_inr, bool) or budget_inr <= 0:
        problems.append(
            f"config.yaml: run.budget_inr must be a number > 0, got {budget_inr!r} — "
            f"a run with no budget cap can overspend without limit"
        )
        budget_inr = 1.0

    out_dir = node.get("out_dir", "runs/")
    if not isinstance(out_dir, str) or not out_dir.strip():
        problems.append(f"config.yaml: run.out_dir must be a non-empty string, got {out_dir!r}")
        out_dir = "runs/"

    max_seconds = node.get("max_conversation_seconds", 540)
    if not isinstance(max_seconds, int) or isinstance(max_seconds, bool) or max_seconds < 1:
        problems.append(
            f"config.yaml: run.max_conversation_seconds must be an int >= 1, got {max_seconds!r}"
        )
        max_seconds = 540
    elif max_seconds >= AGENT_SERVER_CAP_S:
        problems.append(
            f"config.yaml: run.max_conversation_seconds = {max_seconds} — must be < {AGENT_SERVER_CAP_S}. "
            f"The agent's own server cap is {AGENT_SERVER_CAP_S}s and its behaviour at that cap is "
            f"untested; the runner must hang up first (docs/INTERFACES.md §6.4)."
        )

    resolved_out = Path(out_dir)
    if not resolved_out.is_absolute():
        resolved_out = (base_dir / resolved_out).resolve()

    return RunConfig(
        personas=personas,
        max_parallel=int(max_parallel),
        budget_inr=float(budget_inr),
        out_dir=resolved_out,
        max_conversation_seconds=int(max_seconds),
    )


def _target_section(raw: dict[str, Any], agent_id: str, problems: list[str]) -> TargetConfig:
    node = _section(raw, "target", problems)

    adapter = node.get("adapter", "elevenlabs")
    if adapter not in SUPPORTED_ADAPTERS:
        problems.append(
            f"config.yaml: target.adapter '{adapter}' is not implemented. "
            f"Available: {', '.join(sorted(SUPPORTED_ADAPTERS))}"
        )

    # Level 0 pinned this to 'text'. LEVEL1_SPEC §6 widens it to text | audio, default text —
    # the one feature flag the whole of Level 1 hangs off. An absent key still means 'text',
    # so a Level 0 config that never had the key keeps running the Level 0 path unchanged.
    mode = node.get("mode", "text")
    if mode not in SUPPORTED_MODES:
        problems.append(
            f"config.yaml: target.mode must be one of {', '.join(sorted(SUPPORTED_MODES))}, "
            f"got {mode!r}. 'text' is Level 0 (text everywhere, no audio anywhere); "
            f"'audio' is Level 1 half-duplex (docs/LEVEL1_SPEC.md §6)."
        )
        mode = "text"

    auth = node.get("auth", "header")
    if auth not in SUPPORTED_AUTH:
        problems.append(
            f"config.yaml: target.auth must be one of {', '.join(sorted(SUPPORTED_AUTH))}, got {auth!r}"
        )
        auth = "header"
    elif auth == "signed" and mode == "audio":
        # LEVEL1_SPEC §9.9: only the header path was ever exercised in voice mode. The signed
        # fallback stays text-mode-only until somebody probes it.
        problems.append(
            "config.yaml: target.auth 'signed' is not supported with target.mode 'audio'. "
            "Only the header path has been exercised in voice mode (LEVEL1_SPEC §9.9); the "
            "signed-URL flow is text-mode-only until it is probed. Use auth: header."
        )

    return TargetConfig(adapter=str(adapter), mode=str(mode), agent_id=agent_id, auth=auth)  # type: ignore[arg-type]


def _speech_section(
    raw: dict[str, Any], mode: str, problems: list[str], warnings: list[str]
) -> dict[str, Any]:
    """Validate `speech:` (LEVEL1_SPEC §6) and return it with defaults filled in.

    Level 0 read this block and threw it away. It is now validated in BOTH modes on purpose:
    the shipped placeholder named two models that do not do what their names suggest
    (`saaras:v3` does not exist; `bulbul:v3` is 7x slower than v2 on a long line), and a
    wrong value here fails at the API, mid-conversation, one live turn at a time. Every key
    is optional and defaulted, so a Level 0 config with no `speech:` block is untouched —
    but a key that IS present and wrong is a startup error, in either mode.
    """
    node = raw.get("speech")
    if node is None:
        if mode == "audio":
            problems.append(
                "config.yaml: target.mode is 'audio' but there is no 'speech:' block. "
                "Audio mode needs the STT/TTS models, the 16000 Hz sample rates, the turn-detector "
                "thresholds and the mic-hold bound. Copy the block from config.example.yaml."
            )
        return {}
    if not isinstance(node, dict):
        problems.append(f"config.yaml: 'speech:' must be a mapping, got {type(node).__name__}")
        return {}

    for key in sorted(set(node) - SPEECH_KNOWN_KEYS):
        warnings.append(f"config.yaml: unknown key 'speech.{key}' — ignored (typo?)")

    speech: dict[str, Any] = {**SPEECH_DEFAULTS, "turn_detector": dict(TURN_DETECTOR_DEFAULTS)}

    # -- STT ------------------------------------------------------------------------
    stt = node.get("stt", SPEECH_DEFAULTS["stt"])
    if not isinstance(stt, str):
        problems.append(f"config.yaml: speech.stt must be a string, got {type(stt).__name__} ({stt!r})")
    elif stt in STT_TRANSLATE_MODELS:
        problems.append(
            f"config.yaml: speech.stt '{stt}' is a speech-to-TRANSLATE model on a different "
            f"endpoint (/speech-to-text-translate), not a transcriber. Measured: it turned "
            f"\"Arre yaar, 10% off is not enough\" into \"But man, 10% off is not enough\". "
            f"The cross-check exists to measure how a listener hears our Hinglish (§2.1); "
            f"an English translation answers a different question. Use: {', '.join(sorted(STT_MODELS))}"
        )
    elif stt not in STT_MODELS:
        extra = ""
        if stt.startswith("saaras"):
            extra = (
                " The `saaras` family is speech-to-text-TRANSLATE and only `saaras:v2.5` exists; "
                "there is no `saaras:v3` at all, in either family."
            )
        problems.append(
            f"config.yaml: speech.stt '{stt}' is not a live Sarvam speech-to-text model. "
            f"Allowed: {', '.join(sorted(STT_MODELS))} (the only one that answered 200 on "
            f"/speech-to-text — scripts/spike_sarvam_speech.py probe G_stt).{extra}"
        )
    speech["stt"] = stt

    cross_check = node.get("stt_cross_check", SPEECH_DEFAULTS["stt_cross_check"])
    if not isinstance(cross_check, bool):
        problems.append(
            f"config.yaml: speech.stt_cross_check must be true or false, got {cross_check!r}"
        )
        cross_check = True
    speech["stt_cross_check"] = cross_check

    # -- TTS + casting ---------------------------------------------------------------
    tts = node.get("tts", SPEECH_DEFAULTS["tts"])
    if not isinstance(tts, str) or tts not in TTS_MODELS:
        problems.append(
            f"config.yaml: speech.tts '{tts!r}' is not a live Bulbul model. "
            f"Allowed: {', '.join(sorted(TTS_MODELS))}. Default is bulbul:v2 over REST "
            f"(0.85-1.29 s measured); bulbul:v3 is a casting escape hatch only (LEVEL1_SPEC §9.7)."
        )
        tts = SPEECH_DEFAULTS["tts"]
    elif tts == "bulbul:v3":
        warnings.append(
            "config.yaml: speech.tts is bulbul:v3 — measured 2.14 s for a 49-char line and "
            "9.24 s for a 288-char one, against 0.85 s / 1.29 s for bulbul:v2 REST. That is the "
            "default voice for every persona that does not override it. LEVEL1_SPEC §9.7 says "
            "v2 is the default and v3 is for casting needs only; set it per persona in "
            "personas/*.yaml voice: instead of globally."
        )
    speech["tts"] = tts

    speaker = node.get("tts_speaker", SPEECH_DEFAULTS["tts_speaker"])
    roster = BULBUL_SPEAKERS.get(tts, frozenset())
    if not isinstance(speaker, str) or not speaker.strip():
        problems.append(
            f"config.yaml: speech.tts_speaker must be a non-empty string, got {speaker!r}"
        )
    elif speaker not in roster:
        other = sorted(m for m, names in BULBUL_SPEAKERS.items() if m != tts and speaker in names)
        hint = (
            f" '{speaker}' IS a {other[0]} speaker — the rosters are disjoint and the wrong "
            f"pairing is an HTTP 400 ('not compatible with model {tts}')."
            if other else ""
        )
        problems.append(
            f"config.yaml: speech.tts_speaker '{speaker}' is not in the {tts} roster.{hint} "
            f"{tts} speakers: {', '.join(sorted(roster))}"
        )
    speech["tts_speaker"] = speaker

    # -- sample rates ----------------------------------------------------------------
    for key in _SAMPLE_RATE_KEYS:
        value = node.get(key, REQUIRED_SAMPLE_RATE_HZ)
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append(
                f"config.yaml: speech.{key} must be the integer {REQUIRED_SAMPLE_RATE_HZ}, "
                f"got {type(value).__name__} ({value!r})"
            )
        elif value != REQUIRED_SAMPLE_RATE_HZ:
            problems.append(
                f"config.yaml: speech.{key} = {value} — must be exactly "
                f"{REQUIRED_SAMPLE_RATE_HZ}. The agent negotiates pcm_16000 in both directions "
                f"and there is no resampler anywhere in this pipeline. Bulbul's own default is "
                f"22050 and it returns 200 at 8000/16000/22050/24000/44100/48000 alike, so a "
                f"wrong rate is never an error — it is wrong-speed audio that Tara's ASR turns "
                f"into nonsense with nothing logged (LEVEL1_SPEC §6)."
            )
        speech[key] = value

    # -- turn detector ---------------------------------------------------------------
    detector = node.get("turn_detector", {})
    if not isinstance(detector, dict):
        problems.append(
            f"config.yaml: speech.turn_detector must be a mapping of "
            f"{{{', '.join(TURN_DETECTOR_DEFAULTS)}}}, got {type(detector).__name__}"
        )
        detector = {}
    for key in sorted(set(detector) - set(TURN_DETECTOR_DEFAULTS)):
        warnings.append(f"config.yaml: unknown key 'speech.turn_detector.{key}' — ignored (typo?)")

    peak_min = detector.get("speech_peak_min", TURN_DETECTOR_DEFAULTS["speech_peak_min"])
    if isinstance(peak_min, bool) or not isinstance(peak_min, int) or peak_min < 1:
        problems.append(
            f"config.yaml: speech.turn_detector.speech_peak_min must be an int >= 1, got {peak_min!r}"
        )
        peak_min = TURN_DETECTOR_DEFAULTS["speech_peak_min"]
    elif peak_min <= 2942 or peak_min >= 3266:
        # The measured gap is thin at worst case: the office1 background carrier peaked at
        # 2942 and the quietest real speech frame at 3266 (LEVEL1_SPEC §9.4).
        warnings.append(
            f"config.yaml: speech.turn_detector.speech_peak_min = {peak_min} is outside the "
            f"measured safe band (2942, 3266) — worst-case carrier peak vs quietest speech frame. "
            f"Below it the background_sound carrier reads as speech and turns never end; above it "
            f"quiet speech reads as silence and turns split. 3000 is the calibrated value."
        )
    detector_out = {"speech_peak_min": int(peak_min)}

    quiet_frames = detector.get("quiet_frames", TURN_DETECTOR_DEFAULTS["quiet_frames"])
    if isinstance(quiet_frames, bool) or not isinstance(quiet_frames, int) or quiet_frames < 2:
        problems.append(
            f"config.yaml: speech.turn_detector.quiet_frames must be an int >= 2, got "
            f"{quiet_frames!r}. A single-frame test is forbidden (LEVEL1_SPEC §9.4): the "
            f"multi-frame hold is the only thing making a 2942-vs-3266 amplitude margin robust."
        )
        quiet_frames = TURN_DETECTOR_DEFAULTS["quiet_frames"]
    detector_out["quiet_frames"] = int(quiet_frames)

    quiet_wall = detector.get("quiet_wall_s", TURN_DETECTOR_DEFAULTS["quiet_wall_s"])
    if isinstance(quiet_wall, bool) or not isinstance(quiet_wall, (int, float)) or quiet_wall <= 0:
        problems.append(
            f"config.yaml: speech.turn_detector.quiet_wall_s must be a positive number, got {quiet_wall!r}"
        )
        quiet_wall = TURN_DETECTOR_DEFAULTS["quiet_wall_s"]
    elif float(quiet_wall) < 1.5:
        problems.append(
            f"config.yaml: speech.turn_detector.quiet_wall_s = {quiet_wall} — must be >= 1.5. "
            f"0.9 s was measured too tight and split a real turn mid-sentence (el 28.876, "
            f"LEVEL1_SPEC §0.2). A split turn is not an error anywhere; it silently becomes two "
            f"agent turns in the transcript the judge scores."
        )
    detector_out["quiet_wall_s"] = float(quiet_wall)
    speech["turn_detector"] = detector_out

    # -- mic hold --------------------------------------------------------------------
    hold = node.get("mic_hold_bound_s", SPEECH_DEFAULTS["mic_hold_bound_s"])
    if isinstance(hold, bool) or not isinstance(hold, (int, float)) or hold <= 0:
        problems.append(
            f"config.yaml: speech.mic_hold_bound_s must be a positive number, got {hold!r}"
        )
        hold = MIC_HOLD_MAX_S
    elif float(hold) > MIC_HOLD_MAX_S:
        problems.append(
            f"config.yaml: speech.mic_hold_bound_s = {hold} — must be <= {MIC_HOLD_MAX_S}. "
            f"Past ~10 s the agent's own turn_timeout endpoints our silence as empty user turns; "
            f"it nudges twice, calls end_call and closes at 59 s (measured). The bound is the "
            f"ceiling on how long we wait for user_transcript, not a patience setting "
            f"(LEVEL1_SPEC §4.3, §9.1)."
        )
    elif float(hold) < MIC_HOLD_MIN_SAFE_S:
        warnings.append(
            f"config.yaml: speech.mic_hold_bound_s = {hold} is below the measured safe floor of "
            f"{MIC_HOLD_MIN_SAFE_S}s — user_transcript arrived 2.2-2.8 s after the last real chunk "
            f"in every captured turn, so this will raise no_user_transcript on healthy turns."
        )
    speech["mic_hold_bound_s"] = float(hold)

    # -- persona character cap --------------------------------------------------------
    cap = node.get("persona_char_cap", SPEECH_DEFAULTS["persona_char_cap"])
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        problems.append(
            f"config.yaml: speech.persona_char_cap must be an int >= 1, got {cap!r}"
        )
        cap = PERSONA_CHAR_CAP_DEFAULT
    elif cap > 400:
        warnings.append(
            f"config.yaml: speech.persona_char_cap = {cap} — at the measured 17 chars/s of "
            f"playout that is {cap / 17:.0f}s of talking per persona turn, and playout is "
            f"realtime and cannot be fast-forwarded. 200 (~12 s) is the LEVEL1_SPEC §4.4 value; "
            f"long lines are also the main driver of the silent-truncation failure (§9.2)."
        )
    speech["persona_char_cap"] = int(cap)

    return speech


def _rubric_section(raw: dict[str, Any], problems: list[str]) -> dict[str, int]:
    node = _section(raw, "rubric", problems)
    rubric: dict[str, int] = {}
    for key, value in node.items():
        if isinstance(value, bool) or not isinstance(value, int):
            problems.append(f"config.yaml: rubric.{key} must be an int, got {value!r}")
            continue
        rubric[str(key)] = value

    if node and len(rubric) == len(node):
        total = sum(rubric.values())
        if total != 100:
            lines = "\n".join(f"      {k}: {v}" for k, v in rubric.items())
            problems.append(
                f"config.yaml: rubric weights sum to {total}, must be exactly 100.\n{lines}\n"
                f"      -> difference: {100 - total:+d}"
            )
    return rubric


def _pricing_section(
    raw: dict[str, Any], models: dict[str, str], problems: list[str], warnings: list[str]
) -> dict[str, dict[str, float]]:
    node = raw.get("pricing") or {}
    if not isinstance(node, dict):
        problems.append("config.yaml: 'pricing:' must be a mapping of model -> {input, output}")
        return {}

    pricing: dict[str, dict[str, float]] = {}
    for model, rates in node.items():
        if not isinstance(rates, dict):
            problems.append(f"config.yaml: pricing.{model} must be a mapping with 'input' and 'output'")
            continue
        clean: dict[str, float] = {}
        for direction in ("input", "output"):
            value = rates.get(direction)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                clean[direction] = float(value)
            else:
                problems.append(
                    f"config.yaml: pricing.{model}.{direction} must be a number (INR per 1M tokens), "
                    f"got {value!r}"
                )
        pricing[str(model)] = clean

    # A missing rate is a warning, not an error: cost becomes null in the artifact (§8.3).
    # One warning per model, not per role — the same model usually serves several roles.
    roles_by_model: dict[str, list[str]] = {}
    for role, model in models.items():
        roles_by_model.setdefault(model, []).append(role)

    for model, roles in roles_by_model.items():
        used_by = ", ".join(roles)
        rates = pricing.get(model)
        if not rates or "input" not in rates or "output" not in rates:
            warnings.append(
                f"pricing: no rate for '{model}' (used by {used_by}) — cost.* will be null for it "
                f"and run.budget_inr cannot account for it"
            )
        elif rates["input"] == 0.0 and rates["output"] == 0.0:
            warnings.append(
                f"pricing: '{model}' (used by {used_by}) is priced at 0.0 INR, which counts as "
                f"UNPRICED, not free — its cost.* is null and its spend is invisible to "
                f"run.budget_inr, so the cap cannot fire. Not enforced at Level 0 by choice; "
                f"fill in real INR-per-1M-token rates to re-arm the cap."
            )
    return pricing


# ======================================================================================
# The loader
# ======================================================================================


def load_config(
    config_path: Path = Path("config.yaml"),
    env_path: Path = Path(".env"),
    *,
    validate_personas: bool = True,
) -> Config:
    """Load, merge and validate everything. Raises exactly one `ConfigError` listing every problem.

    Args:
        config_path: the non-secret YAML. Gitignored; copied from `config.example.yaml`.
        env_path:    the secrets file. `os.environ` overrides whatever is in it.
        validate_personas: also structurally validate the selected `personas/*.yaml`. Leave this
            on — an invalid persona should fail before the first live API call, not on turn 3.
    """
    config_path = Path(config_path)
    env_path = Path(env_path)
    base_dir = config_path.resolve().parent

    # These two raise on their own: a missing file and a leaked secret are not "one problem
    # among several", they are conditions under which the rest of the validation is meaningless.
    raw = _load_yaml(config_path)

    problems: list[str] = []
    warnings: list[str] = []

    # -- secrets ------------------------------------------------------------------
    env = _read_env(env_path)
    missing = [k for k in REQUIRED_ENV if not env.get(k)]
    if missing:
        hint = "" if env_path.is_file() else f" ({env_path} does not exist — `cp .env.example .env`)"
        for key in missing:
            problems.append(f".env: {key} is missing or empty{hint}")

    agent_id = env.get("ELEVENLABS_AGENT_ID", "")
    if agent_id and not agent_id.startswith("agent_"):
        problems.append(
            f".env: ELEVENLABS_AGENT_ID must start with 'agent_' — got '{_mask(agent_id, keep=6)}'. "
            f"This is the ID from the dashboard URL, not the agent name."
        )

    secrets = Secrets(
        elevenlabs_api_key=env.get("ELEVENLABS_API_KEY", ""),
        elevenlabs_agent_id=agent_id,
        sarvam_api_key=env.get("SARVAM_API_KEY", ""),
    )

    # -- yaml sections -------------------------------------------------------------
    for key in sorted(set(raw) - KNOWN_TOP_LEVEL):
        warnings.append(f"config.yaml: unknown top-level key '{key}' — ignored (typo?)")

    target = _target_section(raw, agent_id, problems)
    persona_brain = _llm_section(raw, "persona_brain", problems)
    judge = _llm_section(raw, "judge", problems, default_timeout=180.0)
    synthesizer = _llm_section(raw, "synthesizer", problems, default_timeout=180.0)
    referee = _referee_section(raw, problems)
    run = _run_section(raw, base_dir, problems)
    rubric = _rubric_section(raw, problems)
    pricing = _pricing_section(
        raw,
        {
            "persona_brain": persona_brain.model,
            "referee": referee.model,
            "judge": judge.model,
            "synthesizer": synthesizer.model,
        },
        problems,
        warnings,
    )

    # Bias separation is not optional: the judge must not be the same model that acted.
    if judge.model == persona_brain.model:
        problems.append(
            f"config.yaml: judge.model and persona_brain.model are both '{judge.model}'. "
            f"They must differ — a model grading its own output scores 'did the persona win' "
            f"instead of 'was the agent any good' (docs/REQUIREMENTS.md §5)."
        )

    judge_node = raw.get("judge") if isinstance(raw.get("judge"), dict) else {}
    require_evidence = judge_node.get("require_evidence", True)
    if not isinstance(require_evidence, bool):
        problems.append(f"config.yaml: judge.require_evidence must be true or false, got {require_evidence!r}")
        require_evidence = True
    elif require_evidence is False:
        warnings.append(
            "config.yaml: judge.require_evidence is false — scores will be unfalsifiable. "
            "Every dimension should cite a verbatim quote."
        )

    speech = _speech_section(raw, target.mode, problems, warnings)

    # -- audio-mode-only cross checks (LEVEL1_SPEC) ---------------------------------
    # Nothing below this comment can fire in text mode, by construction.
    if target.mode == "audio" and run.max_parallel > 1:
        warnings.append(
            f"config.yaml: run.max_parallel = {run.max_parallel} with target.mode 'audio'. "
            f"Parallel voice conversations are UNTESTED — rate limits and quota burn "
            f"(~3.3 inbound frames/s per conversation) were never probed, and every audio turn "
            f"is realtime wall clock that cannot be retried cheaply. LEVEL1_SPEC §9.10 ships "
            f"audio at max_parallel: 1; raising it is a deliberate experiment, not a speed-up."
        )

    # -- personas ------------------------------------------------------------------
    personas_dir = (base_dir / "personas").resolve()
    if validate_personas:
        # LEVEL1_SPEC §4.4: audio budgets TURNS, not seconds. ~24 s per turn cycle is
        # irreducible realtime audio, so a 20-turn conversation cannot fit 540 s no matter how
        # fast the LLM is. None (text mode) skips the check entirely.
        cycle_budget = run.max_conversation_seconds if target.mode == "audio" else None
        persona_errors, persona_warnings = validate_persona_dir(
            personas_dir, run.personas, audio_seconds_budget=cycle_budget
        )
        problems.extend(persona_errors)
        warnings.extend(persona_warnings)

    if problems:
        raise ConfigError(
            _format_problems(problems, warnings, config_path, env_path),
            problems=problems,
            warnings=warnings,
        )

    return Config(
        target=target,
        persona_brain=persona_brain,
        referee=referee,
        judge=judge,
        synthesizer=synthesizer,
        run=run,
        rubric=rubric,
        pricing=pricing,
        secrets=secrets,
        config_path=config_path.resolve(),
        personas_dir=personas_dir,
        judge_require_evidence=bool(require_evidence),
        speech=dict(speech or {}),
        warnings=tuple(warnings),
    )


def _format_problems(problems: list[str], warnings: list[str], config_path: Path, env_path: Path) -> str:
    """One message, every problem, numbered, with the files it came from. Loud on purpose."""
    n = len(problems)
    lines = [
        "",
        "=" * 78,
        f"  voice-spar config is INVALID — {n} problem{'s' if n != 1 else ''} found",
        "=" * 78,
        f"  config: {config_path}",
        f"  env:    {env_path}",
        "",
    ]
    width = len(str(n))
    for i, problem in enumerate(problems, 1):
        head, *rest = problem.splitlines()
        lines.append(f"  [{i:>{width}}] {head}")
        lines.extend(f"  {' ' * (width + 3)}{line.strip()}" for line in rest)
    if warnings:
        lines.append("")
        lines.append(f"  warnings ({len(warnings)}) — not fatal, but read them:")
        lines.extend(f"    ! {w}" for w in warnings)
    lines += [
        "",
        "  Fix every item above and run again. Nothing was loaded; no API call was made.",
        "=" * 78,
        "",
    ]
    return "\n".join(lines)


# ======================================================================================
# Preflight CLI:  uv run --python 3.12 python config.py
# ======================================================================================


def _main() -> int:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    root = Path(__file__).resolve().parent
    try:
        cfg = load_config(root / "config.yaml", root / ".env")
    except ConfigError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        return 1

    table = Table(title="voice-spar config", show_header=True, header_style="bold")
    table.add_column("setting")
    table.add_column("value")
    table.add_row("config", str(cfg.config_path))
    table.add_row("target", f"{cfg.target.adapter} · mode={cfg.target.mode} · auth={cfg.target.auth}")
    table.add_row("agent_id", cfg.target.agent_id)
    table.add_row("persona_brain", f"{cfg.persona_brain.model} · t={cfg.persona_brain.temperature} · {cfg.persona_brain.max_tokens} tok")
    table.add_row("referee", f"{cfg.referee.model} · enabled={cfg.referee.enabled} · window={cfg.referee.window_turns}")
    table.add_row("judge", f"{cfg.judge.model} · evidence={cfg.judge_require_evidence}")
    table.add_row("synthesizer", cfg.synthesizer.model)
    table.add_row("personas", str(cfg.run.personas))
    table.add_row("personas_dir", str(cfg.personas_dir))
    table.add_row("out_dir", str(cfg.run.out_dir))
    table.add_row("limits", f"parallel={cfg.run.max_parallel} · budget=₹{cfg.run.budget_inr} · cap={cfg.run.max_conversation_seconds}s")
    table.add_row("rubric", f"{len(cfg.rubric)} dimensions, sum={sum(cfg.rubric.values())}")
    if cfg.target.mode == "audio":
        sp = cfg.speech
        det = sp.get("turn_detector", {})
        table.add_row(
            "speech",
            f"tts={sp.get('tts')}/{sp.get('tts_speaker')} @ {sp.get('speech_sample_rate')}Hz · "
            f"stt={sp.get('stt')} cross_check={sp.get('stt_cross_check')}",
        )
        table.add_row(
            "audio limits",
            f"mic_hold<={sp.get('mic_hold_bound_s')}s · persona_cap={sp.get('persona_char_cap')} chars · "
            f"detector peak>={det.get('speech_peak_min')} quiet={det.get('quiet_frames')}f/"
            f"{det.get('quiet_wall_s')}s",
        )
    table.add_row("secrets", repr(cfg.secrets))
    table.add_row("LLMConfig from", LLMCONFIG_SOURCE)
    console.print(table)

    for warning in cfg.warnings:
        console.print(f"[yellow]![/yellow] {warning}")
    console.print("[bold green]config OK[/bold green]")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
