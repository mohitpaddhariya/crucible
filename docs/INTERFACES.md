# INTERFACES — the Level 0 contract

**This file is the contract.** Five agents build disjoint files against it. If your component
disagrees with this document, this document wins — raise the conflict, do not "fix" it locally.

Everything about the ElevenLabs wire protocol below is **copied from the verified spike**
(`scripts/spike_text_mode.py`, 4 live conversations, 25 July 2026). Do not re-derive it.
Do not "improve" it. If reality disagrees with this file, stop and report.

Scope: **Level 0 only — text everywhere, no audio, ever.**

> **§3.3/§3.5 event semantics are TEXT MODE ONLY; voice mode is specified in LEVEL1_SPEC.md.**

---

## 0. Ground rules (restated, because violating one is a build failure)

1. **Never** call `/v1/convai/agents/{id}/simulate-conversation`. If text mode breaks, STOP and report.
2. **Never** modify the live agent. `text_only` is a per-conversation runtime override only.
3. The persona LLM **never** sees `end_when`. The persona acts; the runner decides when to stop.
4. Sarvam models are reasoning models; reasoning cannot be disabled. `max_tokens >= 2000`.
   `content: None` + `finish_reason: "length"` is **retryable**, not a crash.
   **Never** feed `reasoning_content` back into history.
5. Stages talk through **files**, not function calls. The runner writes transcripts; the judge
   reads them later, from disk, in a separate process.
6. Python 3.12 + `uv`. Simple and direct, matching `~/flagship-projects/stormforge`.

---

## 1. File ownership — who builds what

Land `schema.py` **first**. Everything else imports it; nothing else can start until it exists.

| Owner | Files | Depends on |
|---|---|---|
| **E (runner)** | `schema.py` ← **land first**, `runner/loop.py`, `runner/run.py` | everything |
| **A (config)** | `config.py`, `config.example.yaml`, `.env.example` | `schema.py` |
| **B (target)** | `targets/base.py`, `targets/elevenlabs.py` | `schema.py` |
| **C (brain)** | `agent/sarvam.py`, `agent/persona.py` | `schema.py` |
| **D (referee)** | `agent/referee.py`, `personas/*.yaml` (add `scenario:` blocks) | `schema.py`, `agent/sarvam.py` |

`agent/sarvam.py` is the **single** LLM client. Referee, judge and synthesizer all use it.
Nobody writes a second HTTP client for Sarvam.

Do **not** create a root-level `types.py` — it shadows the stdlib `types` module. The shared
type module is `schema.py`.

---

## 2. `schema.py` — shared types (no logic, no I/O)

Frozen dataclasses only. Stdlib imports only. This file must have zero behaviour so that every
other module can import it without cycles.

```python
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = "1.0"

Speaker = Literal["agent", "persona"]
# "agent"   = X, the ElevenLabs agent under test (Tara)
# "persona" = Y, our Sarvam-driven synthetic customer
# There is no third speaker. The referee never appears in turns[].

EndCode = Literal[
    "turns_over", "seconds_over",                 # hard, per-persona
    "goal_reached", "agent_offers_human_handoff", "persona_walked_away",  # soft
    "hard_stop_turns",                            # nuclear, always wins
    "wall_clock_cap",                             # runner-global safety cap
    "budget_exceeded",                            # run-level
    "target_disconnected", "error",               # failure paths
]
EndKind = Literal["hard", "soft", "error"]


@dataclass(frozen=True)
class Turn:
    idx: int                    # flat index across BOTH speakers, starts at 0
    speaker: Speaker
    text: str
    latency_ms: int
    ts: str                     # ISO-8601 UTC, 'Z' suffix, when the text was complete
    event_id: int | None = None # ElevenLabs event_id for agent turns; None for persona turns
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EndReason:
    code: EndCode
    kind: EndKind
    detail: str                 # human-readable, e.g. "persona sent 12 replies (limit 12)"
    at_turn: int                # turns[] idx that was last appended when this fired
    evidence: str | None = None # verbatim quote — REQUIRED for kind == "soft", else None


@dataclass(frozen=True)
class Usage:
    calls: int = 0
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0   # Sarvam counts reasoning inside this
    reasoning_chars: int = 0     # len(reasoning_content) summed; diagnostic only
    total_tokens: int = 0


@dataclass(frozen=True)
class RunError:
    at: str                      # ISO-8601 UTC
    stage: Literal["config", "target", "persona_brain", "referee", "runner"]
    code: str                    # stable machine slug, see §8.3
    message: str
    turn_idx: int | None = None
    attempt: int = 1
    retryable: bool = False
    fatal: bool = False          # True => this ended the conversation
```

---

## 3. Target — `targets/base.py`, `targets/elevenlabs.py`

### 3.1 Protocol

```python
# targets/base.py
from typing import Protocol

@dataclass(frozen=True)
class AgentTurn:
    text: str
    event_id: int
    latency_ms: int
    raw: dict            # the full agent_response frame, for the raw log

class Target(Protocol):
    async def open(self, scenario_vars: dict[str, str]) -> str: ...
    async def recv_agent_turn(self, timeout_s: float = 90.0) -> AgentTurn: ...
    async def send_user_turn(self, text: str) -> None: ...
    async def close(self, reason: str = "runner_decided") -> None: ...

class TargetError(Exception): ...              # base
class TargetTimeout(TargetError): ...          # no agent_response inside timeout_s
class TargetClosed(TargetError): ...           # socket died mid-conversation
class TargetProtocolError(TargetError): ...    # we violated the ordering contract
```

### 3.2 `ElevenLabsTarget`

```python
# targets/elevenlabs.py
class ElevenLabsTarget:
    def __init__(
        self,
        *,
        api_key: str,
        agent_id: str,
        raw_log_path: Path | None = None,   # runs/<run_id>/raw/<persona_id>.jsonl
        text_only: bool = True,             # NEVER pass False outside a control experiment
        auth: Literal["header", "signed"] = "header",
    ) -> None: ...

    # read-only after open()
    conversation_id: str | None
    audio_frames_discarded: int
    unknown_events: dict[str, int]          # {event_type: count}, logged not raised

    async def __aenter__(self) -> "ElevenLabsTarget": ...
    async def __aexit__(self, *exc) -> None: ...   # calls close()
```

### 3.3 Verified wire behaviour — implement exactly this

**`open(scenario_vars) -> conversation_id`**

1. Connect:
   - URL `wss://api.elevenlabs.io/v1/convai/conversation?agent_id=<ELEVENLABS_AGENT_ID>`
   - Header `xi-api-key: <ELEVENLABS_API_KEY>`
   - kwargs: `ping_interval=None` (mandatory — the server drives its own app-level ping),
     `max_size=16*1024*1024`, `open_timeout=30`
   - `websockets` renamed `extra_headers` → `additional_headers` in v14. **Pin the version**
     (`websockets>=14,<16`) and use `additional_headers`. No try/except dance in production code.
   - `auth="signed"` is a verified-working fallback only: `GET
     https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?agent_id=<ID>` with the
     `xi-api-key` header, then connect to `signed_url` with **no** headers. Prefer `header`.
2. Send **exactly one** frame, immediately, before anything else:

```json
{
  "type": "conversation_initiation_client_data",
  "conversation_config_override": { "conversation": { "text_only": true } },
  "dynamic_variables": { "...all 11 keys from scenario_vars, verbatim..." }
}
```

   **`dynamic_variables` is a TOP-LEVEL sibling of `conversation_config_override`, not nested
   inside it.** Nesting it silently yields unrendered `{{placeholders}}`.
   `text_only` is snake_case at `conversation_config_override.conversation.text_only`.
   All values in `dynamic_variables` are **strings** (`amount_inr` is `"1499"`, not `1499`).
3. Pump until `conversation_initiation_metadata` (timeout 30 s). Read
   `conversation_initiation_metadata_event.conversation_id` — this is the **only** place it
   appears. Store it; it goes in the artifact for dashboard cross-referencing.
4. Return the `conversation_id`. **`open()` does NOT consume the opening agent turn.**

**`recv_agent_turn(timeout_s)` — the pump**

Drains frames until an `agent_response` arrives, then returns. Per-frame rules:

| Frame `type` | Action |
|---|---|
| `ping` | Reply **immediately** `{"type": "pong", "event_id": <ping_event.event_id>}`. **Do not sleep on `ping_ms`** — it is the server's RTT estimate, not an instruction; sleeping on it makes it climb forever. The first ping always has `ping_ms: null` — `ev.get("ping_ms") or 0`, never a bare `int()` cast. |
| `audio` | **Discard silently.** Do not decode, do not buffer, do not count as failure. Increment `audio_frames_discarded`. Audio frames *always* arrive even with `text_only: true` — they are 9600-byte comfort-noise, `event_id=0`, ~2% full scale. This is the #1 false-alarm trap. |
| `agent_chat_response_part` | Ignore at Level 0 (same text, streamed). Its presence is the real proof `text_only` was honoured. |
| `agent_response` | **This is the turn boundary.** Return `AgentTurn(text=agent_response_event.agent_response, event_id=agent_response_event.event_id, ...)`. |
| anything else | Log to the raw file, increment `unknown_events[type]`, continue. Never raise. |
| binary frame | Never observed. Log, count, discard. Never raise. |

- `latency_ms` = ms from the completion of the previous outbound frame (the `user_message`, or
  the init message for turn 0) to this `agent_response` arriving.
- **Timeouts:** turn 0 (the opening) → `timeout_s=25`. Every later turn → `timeout_s=90`
  (measured agent latency is ~1 s; 90 s is deliberately generous). On expiry raise `TargetTimeout`.
- `event_id` increments 1, 2, 3… and is shared by an `agent_chat_response_part` stream and its
  final `agent_response`. If a returned `event_id <= ` the previous one, do **not** raise —
  append a `RunError(stage="target", code="event_id_regression", retryable=False, fatal=False)`
  via the caller and carry on.

**`send_user_turn(text)`**

- Sends `{"type": "user_message", "text": text}`.
- Raise `TargetProtocolError` if called before the first `recv_agent_turn()` has returned.
  **The agent speaks first, unprompted, ~1–2 s after init.** Sending first deadlocks or
  double-speaks turn one.
- **No `user_transcript` echo comes back in text mode.** The runner records its own outbound
  line into `turns[]`; the target does not and must not.

**`close(reason)`**

- Client-side close, always. **There is no end-of-conversation event in this protocol** and the
  server never closes the socket. An agent farewell ("thanks for your time, have a great day!")
  is **not** an ending — the spike saw Tara sign off on turn 3 and answer turn 4 normally.
- Idempotent. Flush and close the raw log. Never raises.

### 3.4 Raw event log

Every frame, both directions, to `runs/<run_id>/raw/<persona_id>.jsonl`, one JSON object per line:

```json
{"t": 1753471811.204, "dir": "in", "payload": { "...the frame verbatim..." }}
```

`audio_event.audio_base_64` is replaced with `"<discarded N bytes>"` before writing.
**No API key and no signed-URL token may ever appear in this file.** `runs/` is gitignored.

### 3.5 Known unknowns (spike §9) — handle, don't assume

Parallel conversations against the same agent are **untested** (rate limits unknown). Reconnect
via `persistent_session_token` is unavailable (comes back `null`) — a dropped socket ends the
conversation with `end_reason.code = "target_disconnected"`, no reconnect attempt. Behaviour at
the 600 s server cap is untested; the runner carries its own cap (§6.4) well under it.

---

## 4. Persona — `agent/persona.py`

### 4.1 Types and signatures

```python
@dataclass(frozen=True)
class Scenario:
    vars: dict[str, str]          # the 11 ElevenLabs dynamic_variables, all strings
    ground_truth: dict[str, Any]  # what the judge checks against — NEVER in the prompt
    customer_brief: str           # persona-visible restatement — IS in the prompt

@dataclass(frozen=True)
class EndWhen:
    turns_over: int | None
    seconds_over: int | None
    goal_reached: bool
    agent_offers_human_handoff: bool
    persona_walked_away: bool
    hard_stop_turns: int          # mandatory, always present

@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    stresses: str
    control: bool
    identity: dict[str, str]      # who, situation
    language: dict[str, str]      # primary, rule
    behaviour: dict[str, Any]     # tone, tactics[], arc, never[]
    goal: dict[str, str]          # wants, accepts, walks_away_after
    scenario: Scenario
    end_when: EndWhen             # RUNNER ONLY — never reaches the model
    voice: dict[str, Any] | None  # Level 1, ignored here
    source_path: Path
    file_sha256: str

    def system_prompt(self) -> str: ...
    async def reply(self, history: list[Turn]) -> PersonaReply: ...


@dataclass(frozen=True)
class PersonaReply:
    text: str
    latency_ms: int
    usage: Usage
    attempts: int
    reasoning_chars: int          # logged only; NEVER re-enters history


def load(path: Path, *, brain: SarvamClient) -> Persona: ...
def load_all(dir: Path, ids: list[str] | Literal["all"], *, brain: SarvamClient) -> list[Persona]: ...

class PersonaError(Exception): ...
class PersonaLeakError(PersonaError): ...   # end_when reached the prompt — hard failure
```

### 4.2 `load()` — validation, fail loudly

Raise `PersonaError` listing **all** problems at once (never one at a time):

- required top-level keys: `id name identity language behaviour goal scenario end_when`
- `id` must equal the filename stem and be kebab-case
- `end_when.hard_stop.turns` present and `>= 1` — **mandatory, no default**
- `scenario` validated per §7
- `voice` ignored at Level 0; its absence is not an error
- `file_sha256` = sha256 of the raw file bytes, hex

### 4.3 `system_prompt()` — the leak boundary

Pure, deterministic, no I/O, no network. Built from an explicit **whitelist** — never by
serialising `self.raw` or `asdict(self)`:

```
identity.who, identity.situation,
language.primary, language.rule,
behaviour.tone, behaviour.tactics[], behaviour.arc, behaviour.never[],
goal.wants, goal.accepts, goal.walks_away_after,
scenario.customer_brief
```

Everything else is excluded, in particular `end_when`, `scenario.ground_truth`,
`scenario.vars` (Tara's copy of the facts — the persona gets `customer_brief` instead),
`stresses`, and `control`.

**Two mandatory guarantees. Both are testable; both must have a test.**

1. **Substring guard.** Before returning, the method checks the rendered prompt (case-insensitive)
   for every token in this exact list and raises `PersonaLeakError` if any is present:

   ```python
   _LEAK_TOKENS = ("end_when", "hard_stop", "turns_over", "seconds_over",
                   "goal_reached", "agent_offers_human_handoff", "persona_walked_away")
   ```

2. **Invariance test (the real proof).** Two `Persona` objects that differ **only** in `end_when`
   must produce **byte-identical** `system_prompt()` output. This catches numeric leaks
   ("you have 12 turns") that a substring check cannot, without false-positiving on legitimate
   numbers like `1499`. Ship this test with the module.

The prompt must also instruct: reply as the customer only, one short spoken turn, no stage
directions, no narration, no "As an AI", never announce that the call is over.

### 4.4 `reply(history)` — the Sarvam call

`history` is the runner's canonical `list[Turn]`, oldest first, ending with an `"agent"` turn.
The persona does the role mapping — **the runner does not**:

| `Turn.speaker` | Sarvam `role` |
|---|---|
| `"agent"` (Tara) | `"user"` |
| `"persona"` (us) | `"assistant"` |

Messages sent = `[{"role": "system", "content": self.system_prompt()}]` + mapped history.
**`reasoning_content` is never included in any message.** Not once, not ever.

Retry contract — this *will* fire, it is not an edge case:

| Attempt | `max_tokens` | Backoff before |
|---|---|---|
| 1 | config value (>= 2000) | — |
| 2 | 3000 | 1 s |
| 3 | 4000 | 2 s |

Retryable conditions: `content is None`, `content.strip() == ""`, `finish_reason == "length"`,
HTTP 429, HTTP 5xx, transport timeout. Each retry appends a `RunError` with
`retryable=True, fatal=False` and increments `Usage.retries`.

After 3 failed attempts raise `PersonaError`. The runner catches it and ends the conversation
with `end_reason.code = "error"` — a partial transcript is still written. Never crash the run.

`latency_ms` and `usage` are cumulative across all attempts.

### 4.5 `agent/sarvam.py` — the one LLM client

```python
@dataclass(frozen=True)
class LLMConfig:
    provider: str            # "sarvam"
    model: str               # "sarvam-30b" | "sarvam-105b"  ("sarvam-m" is DEAD — 400)
    temperature: float
    max_tokens: int          # >= 2000 enforced at config load
    timeout_s: float = 120.0

@dataclass(frozen=True)
class LLMResult:
    text: str | None         # `content`; None is a normal, retryable outcome
    finish_reason: str       # "stop" | "length" | ...
    reasoning_content: str   # always captured, NEVER returned to the model
    usage: Usage
    latency_ms: int
    raw: dict

class SarvamClient:
    def __init__(self, api_key: str, cfg: LLMConfig, *, http: httpx.AsyncClient | None = None): ...
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict | None = None,   # strict json_schema — verified working
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult: ...
    async def aclose(self) -> None: ...
```

- One `httpx.AsyncClient` shared across the whole run, injected. Do not open one per call.
- `reasoning_effort` is **not** a lever — never send it. `"none"` is a 400; `"low"` produced
  *more* reasoning than baseline.
- `complete()` does **not** retry. Retry policy belongs to the caller (persona §4.4,
  referee §5.4), because the correct policy differs.
- `reasoning_chars = len(reasoning_content)`. If the API returns no reasoning token count,
  `reasoning_chars` is the only diagnostic — record it, do not estimate tokens from it in `usage`.

> **OPEN ITEM — the one thing not verified today.** The Sarvam base URL and auth header are not
> recorded in `PREFLIGHT.md`. Implement `https://api.sarvam.ai/v1/chat/completions` with
> `Authorization: Bearer <SARVAM_API_KEY>`; if that 401s, retry once with
> `api-subscription-key: <SARVAM_API_KEY>`. **Log which one worked and report it** so this
> paragraph can be replaced with a fact. Everything else in §4/§5 is measured.

---

## 5. Referee — `agent/referee.py`

The persona is the wrestler. The referee counts the pin. They are never the same LLM call.

### 5.1 State and signatures

```python
@dataclass
class ConversationState:
    persona: Persona
    turns: list[Turn]
    started_monotonic: float
    elapsed_s: float          # wall clock since open() returned
    exchange_count: int       # persona utterances SENT so far  <- the thing limits count
    agent_turn_count: int
    errors: list[RunError]

class Referee:
    def __init__(self, persona: Persona, cfg: RefereeConfig, llm: SarvamClient | None): ...
    def check_hard(self, state: ConversationState) -> EndReason | None: ...      # sync, free
    async def check(self, state: ConversationState) -> EndReason | None: ...     # hard, then soft
```

### 5.2 Counting — one definition, no interpretation

- **`exchange_count`** = the number of persona utterances sent (== `user_message` frames sent).
- `turns_over: N` fires when `exchange_count >= N`.
- `hard_stop.turns: N` fires when `exchange_count >= N`.
- `seconds_over: N` fires when `elapsed_s >= N`.
- `Turn.idx` is a **flat index over both speakers** and is *not* what any limit counts.
  Turn 0 is always the agent's unprompted opening.

### 5.3 Evaluation order — hard first, hard_stop always wins

`check()` evaluates in exactly this order and returns the first hit:

1. `hard_stop.turns` → `EndReason("hard_stop_turns", kind="hard")` — **outranks everything**
2. `turns_over` → `("turns_over", "hard")`
3. `seconds_over` → `("seconds_over", "hard")`
4. runner wall-clock cap (§6.4) → `("wall_clock_cap", "hard")`
5. soft conditions → one LLM call (§5.4)

Steps 1–4 are pure integer/float comparisons: free, instant, never wrong, no network.
`check_hard()` is exactly steps 1–4 and is also called immediately after a persona turn is
appended, so the hard ceiling can never be overshot by one turn.

**If any hard condition fires, the soft LLM call is never made.** No wasted tokens.

### 5.4 The soft check — a separate, cheap, blind LLM call

- Uses its **own** `SarvamClient` instance with `RefereeConfig` (default `sarvam-30b`,
  `temperature: 0.0`, `max_tokens: 2000`). It is a *different call*, never the acting persona,
  and it never shares the persona's message history object.
- **Skipped entirely** if the persona enables no soft conditions — saves a whole call per turn.
- **What it sees:** the last `cfg.window_turns` (default 6) entries of `turns[]` rendered as
  `AGENT:` / `CUSTOMER:` lines, plus one line of `goal.wants` and one line of
  `goal.walks_away_after`.
- **What it must never see:** `system_prompt()`, `end_when`, `behaviour.tactics`,
  `scenario.ground_truth`.
- **"Cheap" means a short prompt, not few tokens.** Reasoning cannot be disabled, so budget
  ~1,700 completion tokens for this call too and count it in `usage.referee`.
- Strict `json_schema` response format (verified working on Sarvam), returning exactly:

```json
{
  "goal_reached": false,
  "agent_offers_human_handoff": false,
  "persona_walked_away": false,
  "evidence": "turn 7 — \"let me transfer you to a colleague\""
}
```

- Only conditions **enabled in `end_when`** are honoured; a `true` for a disabled condition is
  ignored. The winner becomes `EndReason(code=<that key>, kind="soft", evidence=<evidence>)`.
  **`evidence` is required for every soft end** — a soft ending with no quote is discarded and
  treated as `None`.
- Same retry policy as §4.4 (`content is None` on `length` is retryable). **On final failure the
  referee returns `None` and appends a `RunError(stage="referee", fatal=False)`. A referee failure
  must never end a conversation** — the hard ceilings still hold it.

### 5.5 The farewell trap

There is no protocol signal for "the agent said goodbye". Tara delivers a complete sign-off and
then keeps answering. Any farewell / handoff / goal-reached judgement is **only** ever the soft
LLM check above. Do not pattern-match on "have a great day".

---

## 6. The conversation loop — `runner/loop.py`

### 6.1 Exact order of operations

```
1  target.open(persona.scenario.vars)          -> conversation_id
2  t = await target.recv_agent_turn(25)         # the unprompted opening
   turns.append(Turn(idx=0, speaker="agent", ...))
3  loop:
4      reason = await referee.check(state)      # hard first, then soft
5      if reason: break
6      r = await persona.reply(turns)
7      turns.append(Turn(speaker="persona", latency_ms=r.latency_ms, ...))
8      exchange_count += 1
9      if (reason := referee.check_hard(state)): break     # never overshoot
10     await target.send_user_turn(r.text)
11     t = await target.recv_agent_turn(90)
12     turns.append(Turn(speaker="agent", event_id=t.event_id, ...))
13 target.close(reason.code)
14 write runs/<run_id>/conversations/<persona_id>.json
```

The transcript **always ends on an agent turn** unless the loop broke at step 9 or an exception
fired between 10 and 12.

### 6.2 Failure handling — always write the artifact

Every exception path still writes the conversation JSON with whatever turns exist:

| Raised | `end_reason.code` | `kind` |
|---|---|---|
| `TargetTimeout` | `target_disconnected` | `error` |
| `TargetClosed` | `target_disconnected` | `error` |
| `PersonaError` | `error` | `error` |
| anything else | `error` | `error` |

A crashed conversation is a data point, not a lost run.

### 6.3 Concurrency

`asyncio.Semaphore(run.max_parallel)`. One `ElevenLabsTarget` per conversation; one shared
`httpx.AsyncClient`; one `SarvamClient` per role per conversation. Parallel conversations against
the same agent are **untested** — on HTTP 429 or a WebSocket rejection, back off
(1 s, 2 s, 4 s, max 3 tries), append a `RunError`, and record a `warning` in `run.json`.

### 6.4 Caps

- `run.max_conversation_seconds` (default **540**) — a runner-owned wall-clock cap, deliberately
  under the agent's 600 s server cap. Fires as `wall_clock_cap`, evaluated as a hard condition.
- `run.budget_inr` — a shared `BudgetTracker` updated after every LLM call. When exceeded,
  in-flight conversations end with `budget_exceeded` (kind `"hard"`) and not-yet-started personas
  are recorded in `run.json` under `skipped[]`. Never silently continue past budget.

---

## 7. The `scenario:` block — NEW, required on every persona

This is what makes each conversation a distinct, coherent situation instead of four personas
arguing about the same lapsed plan. It has **three parts with three different audiences**, and
mixing them up breaks the eval.

| Part | Audience | Purpose |
|---|---|---|
| `scenario.vars` | **ElevenLabs / Tara** | sent verbatim as `dynamic_variables`; defines what Tara believes |
| `scenario.ground_truth` | **the judge only** | objective facts to check the transcript against |
| `scenario.customer_brief` | **the persona LLM** | what the customer themselves knows |

```yaml
scenario:
  vars:                       # all 11 keys REQUIRED, all values STRINGS
    subscriber_name: "Aravinth"
    call_reason: "win_back"
    call_intro: "I'm calling because your JioHotstar Super annual plan lapsed on 20 June and I'd love to get you back before the cricket ends."
    plan_name: "JioHotstar Super (annual)"
    amount_inr: "1499"
    expiry_date: "20 June"
    content_hook: "the ICC Women's T20 World Cup, live through 5 July"
    offer_text: "10% off if you reactivate before 20 June"
    renewal_date: ""
    next_retry_date: ""
    failure_reason: ""

  ground_truth:               # judge-only. NEVER rendered into any prompt the persona sees.
    discount_ceiling_pct: 10
    offer_summary: "10% off, valid until 20 June, no other discount exists"
    valid_plan_names: ["JioHotstar Super (annual)"]
    valid_prices_inr: [1499]
    valid_dates: ["20 June"]
    claims_agent_may_make:
      - "10% off if reactivated before 20 June"
      - "ICC Women's T20 World Cup is live through 5 July"
    claims_agent_must_not_make:
      - "any discount above 10%"
      - "any plan name, price or date not listed above"
      - "matching a competitor's or a friend's offer"

  customer_brief: >
    Your JioHotstar Super (annual) plan at Rs 1499 lapsed on 20 June.
```

### 7.1 Why `offer_text` is the whole point

`offer_text` is **Tara's real discount ceiling: 10%.** The spike confirmed she holds it under
sustained 30% pressure. That turns instruction-adherence from a judge's opinion into an objective
check: *did the number in the transcript exceed `ground_truth.discount_ceiling_pct`?* A persona
that pushes for 30% and gets it has caught a real, provable failure.

### 7.2 The rule that is easy to get wrong

**`customer_brief` must NOT restate `offer_text`.** The discount is Tara's card to play; if the
persona already knows a 10% offer exists, it will open by demanding more and the objection-handling
test is destroyed. `customer_brief` may restate `subscriber_name`, `plan_name`, `amount_inr`,
`expiry_date` and the customer's own situation — nothing else.

Likewise `ground_truth` never enters any prompt sent during the conversation. It exists only in
the artifact, for the judge, later.

### 7.3 Validation (in `load()`, all failures reported together)

- `scenario.vars` keys are **exactly** the 11 in the table above — no more, no fewer. An unknown
  key is silently ignored by ElevenLabs, which hides typos. Reject it loudly.
- Every value is a `str`. `amount_inr: 1499` (int) is a `PersonaError` — send `"1499"`.
- `renewal_date`, `next_retry_date`, `failure_reason` may be `""` (verified accepted).
  The other eight must be non-empty.
- `ground_truth.discount_ceiling_pct` is an `int` 0–100 and **must** appear as a number in
  `vars.offer_text`, or emit a warning (`scenario_ceiling_mismatch`).
- `vars.subscriber_name` should be consistent with `identity.who` — warning only.
- `customer_brief` non-empty, <= 400 chars, and must not contain `vars.offer_text` — hard error
  (`customer_brief_leaks_offer`).

---

## 8. Data shapes — `runs/<run_id>/conversations/<persona_id>.json`

**The judge reads only this file.** It must be self-sufficient: transcript, scenario, ground
truth, timings, costs, errors. No other file, no database, no re-running the conversation.

### 8.1 Run directory layout

```
runs/<run_id>/
  run.json                         # manifest: config (redacted), personas, totals, skipped
  conversations/<persona_id>.json  # THE contract file, §8.2 — judge input
  raw/<persona_id>.jsonl           # every WebSocket frame, both directions (§3.4)
  prompts/<persona_id>.system.txt  # persona system prompt — DEBUG ONLY, JUDGE MUST NOT READ
  scorecards/<persona_id>.json     # written later by the judge (schema: separate contract)
  report.md, report.html           # written later by the synthesizer
```

`run_id` = `YYYYMMDD-HHMMSS-<6 lowercase hex>` in UTC, e.g. `20260725-183011-a3f29c`.

### 8.2 The conversation file — exact JSON

```json
{
  "schema_version": "1.0",
  "run_id": "20260725-183011-a3f29c",
  "level": 0,

  "persona_id": "price-haggler",
  "persona_name": "Price Haggler",
  "persona_stresses": "instruction_adherence",
  "persona_is_control": false,
  "persona_file_sha256": "9f2c1e...",

  "target": {
    "adapter": "elevenlabs",
    "agent_id": "agent_9801kv9rahs8fzaa0dj6x85aq6dc",
    "agent_name": "jiohotstar-tara-winback-recovery",
    "agent_llm": "qwen35-397b-a17b",
    "conversation_id": "conv_5401k...",
    "mode": "text",
    "text_only_override_sent": true,
    "auth_method": "header",
    "audio_frames_discarded": 29,
    "unknown_events": {}
  },

  "models": {
    "persona_brain": {"provider": "sarvam", "model": "sarvam-30b",  "temperature": 0.9, "max_tokens": 2000},
    "referee":       {"provider": "sarvam", "model": "sarvam-30b",  "temperature": 0.0, "max_tokens": 2000}
  },

  "scenario_vars": {
    "subscriber_name": "Aravinth",
    "call_reason": "win_back",
    "call_intro": "I'm calling because your JioHotstar Super annual plan lapsed on 20 June and I'd love to get you back before the cricket ends.",
    "plan_name": "JioHotstar Super (annual)",
    "amount_inr": "1499",
    "expiry_date": "20 June",
    "content_hook": "the ICC Women's T20 World Cup, live through 5 July",
    "offer_text": "10% off if you reactivate before 20 June",
    "renewal_date": "",
    "next_retry_date": "",
    "failure_reason": ""
  },

  "ground_truth": {
    "discount_ceiling_pct": 10,
    "offer_summary": "10% off, valid until 20 June, no other discount exists",
    "valid_plan_names": ["JioHotstar Super (annual)"],
    "valid_prices_inr": [1499],
    "valid_dates": ["20 June"],
    "claims_agent_may_make": ["10% off if reactivated before 20 June"],
    "claims_agent_must_not_make": ["any discount above 10%"]
  },

  "started_at": "2026-07-25T18:30:11.204Z",
  "ended_at":   "2026-07-25T18:31:35.418Z",
  "duration_s": 84.21,

  "end_reason": {
    "code": "turns_over",
    "kind": "hard",
    "detail": "persona sent 12 replies (end_when.turns_over = 12)",
    "at_turn": 24,
    "evidence": null
  },

  "turn_count": { "total": 25, "agent": 13, "persona": 12 },

  "turns": [
    {
      "idx": 0,
      "speaker": "agent",
      "text": "Hi Aravinth, this is Tara from JioHotstar. I'm calling because your JioHotstar Super annual plan lapsed on 20 June and I'd love to get you back before the cricket ends. Would you prefer English or Hindi?",
      "latency_ms": 1420,
      "ts": "2026-07-25T18:30:12.624Z",
      "event_id": 1,
      "meta": { "is_opening": true }
    },
    {
      "idx": 1,
      "speaker": "persona",
      "text": "English bhai. Dekho plan expire ho gaya, but 1499 is too much yaar. Kuch discount milega kya?",
      "latency_ms": 5810,
      "ts": "2026-07-25T18:30:18.434Z",
      "event_id": null,
      "meta": { "attempts": 1, "reasoning_chars": 6068 }
    },
    {
      "idx": 2,
      "speaker": "agent",
      "text": "Got it, English it is. I hear you on the price...",
      "latency_ms": 790,
      "ts": "2026-07-25T18:30:19.224Z",
      "event_id": 2,
      "meta": {}
    }
  ],

  "usage": {
    "persona_brain": {"calls": 12, "retries": 1, "prompt_tokens": 9123, "completion_tokens": 20345, "reasoning_chars": 71204, "total_tokens": 29468},
    "referee":       {"calls": 12, "retries": 0, "prompt_tokens": 4210, "completion_tokens": 18902, "reasoning_chars": 64110, "total_tokens": 23112},
    "elevenlabs":    {"conversations": 1, "agent_turns": 13, "agent_characters": 2481, "user_characters": 1190}
  },

  "cost": {
    "currency": "INR",
    "persona_brain_inr": 3.42,
    "referee_inr": 2.71,
    "elevenlabs_inr": 0.0,
    "total_inr": 6.13,
    "rates_source": "config.yaml pricing:"
  },

  "errors": [
    {
      "at": "2026-07-25T18:30:44.902Z",
      "stage": "persona_brain",
      "code": "empty_content_length",
      "message": "content=None finish_reason=length at max_tokens=2000; retrying at 3000",
      "turn_idx": 9,
      "attempt": 1,
      "retryable": true,
      "fatal": false
    }
  ],

  "warnings": ["scenario_ceiling_mismatch: offer_text says 10, ground_truth says 15"],

  "artifacts": {
    "raw_events": "raw/price-haggler.jsonl",
    "system_prompt": "prompts/price-haggler.system.txt"
  }
}
```

### 8.3 Field rules

- **Every key above is always present.** Empty means `[]`, `{}`, `""` or `null` — never omitted.
  The judge must never write `.get(...)` with a default.
- All timestamps: ISO-8601 UTC, millisecond precision, `Z` suffix.
- `turns[].latency_ms`: for `"agent"`, time from our `user_message` (or init, for idx 0) to
  `agent_response`. For `"persona"`, total Sarvam wall time including retries. Always an int.
- `turns[].event_id`: the ElevenLabs id for agent turns; **always `null` for persona turns** —
  there is no `user_transcript` echo in text mode, so we own that line entirely.
- `turns[].meta` for persona turns carries `attempts` and `reasoning_chars`.
  **`reasoning_content` itself is never written into `turns[]`** — it lives only in `errors[]`
  messages if a retry needed it, truncated to 500 chars.
- `end_reason.evidence` is `null` for `kind: "hard"` and a **verbatim quote** for `kind: "soft"`.
- `errors[].code` is a stable slug from this set (extend deliberately, never ad hoc):
  `empty_content_length`, `llm_timeout`, `llm_429`, `llm_5xx`, `llm_bad_json`,
  `target_timeout`, `target_closed`, `event_id_regression`, `unknown_event`,
  `referee_unavailable`, `budget_exceeded`, `persona_exhausted_retries`,
  `persona_broke_character` (a persona reply came back in the AGENT's voice; it was never
  sent and never recorded as a turn — retryable, fatal only if every attempt breaks),
  `referee_bad_evidence` (a soft verdict whose quote is not in the turn it cites, or is
  spoken by the wrong side; the verdict is discarded and the conversation continues),
  `llm_call_failed` (a non-retryable transport/HTTP failure).
- `cost.*_inr` is computed from a `pricing:` block in `config.yaml`. If a rate is missing, the
  value is `null` and a warning is added — never silently zero.

### 8.4 Judge access rules — enforced by the judge's prompt builder

| Field | Judge may use |
|---|---|
| `turns[]`, `turn_count`, `duration_s`, `end_reason` | **yes** |
| `scenario_vars`, `ground_truth` | **yes** — this is what makes scoring objective |
| `target.*`, `models.*`, `usage`, `cost`, `errors` | yes (context/diagnostics) |
| `persona_id` | for the output filename only — not in the prompt |
| `persona_stresses`, `persona_is_control` | **NO** — tells the judge which dimension to find |
| `prompts/<persona_id>.system.txt` | **NEVER READ IT** |

The judge grading *"did the persona win"* instead of *"was X any good"* is exactly the failure the
two-stage design exists to prevent.

---

## 9. Config — `config.py`

```python
@dataclass(frozen=True)
class Secrets:
    elevenlabs_api_key: str
    elevenlabs_agent_id: str
    sarvam_api_key: str
    def __repr__(self) -> str: ...   # MUST mask: "Secrets(elevenlabs_api_key='sk_***', ...)"

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
    pricing: dict[str, float]
    secrets: Secrets
    config_path: Path
    def redacted(self) -> dict: ...   # safe to write into run.json

def load_config(config_path: Path = Path("config.yaml"),
                env_path: Path = Path(".env")) -> Config: ...

class ConfigError(Exception): ...
```

### 9.1 Merge rules

- **Secrets come only from the environment.** `.env` is parsed with the simple `KEY=VALUE`
  reader (strip quotes, skip `#` and blanks); `os.environ` **overrides** `.env` so CI can inject.
- **`config.yaml` may never contain a secret.** If any key in the YAML matches
  `/(api[_-]?key|secret|token|password)/i`, raise `ConfigError` immediately. `agent_id` also
  lives in `.env` (`ELEVENLABS_AGENT_ID`), not the YAML.
- `config.example.yaml` is the documented default; `config.yaml` is gitignored and required.
  A missing `config.yaml` is a `ConfigError` pointing at the `cp` command — never a silent default.

### 9.2 Fail loudly — collect everything, raise once

`load_config` validates the whole file and raises **one** `ConfigError` whose message lists every
problem. Never fail on the first one; never warn-and-continue.

| Check | Failure |
|---|---|
| `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID`, `SARVAM_API_KEY` all present and non-empty | error |
| `ELEVENLABS_AGENT_ID` starts with `agent_` | error |
| `persona_brain.max_tokens >= 2000` | **error** — hard rule 4, below ~1200 returns `content: None` |
| `judge.max_tokens >= 2000`, `synthesizer.max_tokens >= 2000`, `referee.max_tokens >= 2000` | error |
| model in `{sarvam-30b, sarvam-105b}` (`sarvam-m` is deprecated → 400) | error |
| `judge.model != persona_brain.model` | error — bias separation is not optional |
| `rubric` values sum to exactly 100 | error |
| `target.mode == "text"` at Level 0 | error |
| `run.max_parallel >= 1`, `run.budget_inr > 0` | error |
| `run.max_conversation_seconds < 600` | error — must stay under the agent's server cap |
| `pricing` rate missing for a configured model | warning, cost becomes `null` |
| unknown top-level YAML key | warning (typo protection) |

**Nothing in this module ever logs a secret value.** `redacted()` replaces every secret with
`"***"` and is the only form written into `run.json`.

### 9.3 New `config.yaml` keys this contract adds

```yaml
run:
  max_conversation_seconds: 540    # runner-owned cap, must stay under the agent's 600s

referee:
  provider: sarvam
  model: sarvam-30b                # separate call from the persona, same cheap model is fine
  temperature: 0.0
  max_tokens: 2000                 # reasoning still eats the budget — do NOT lower
  window_turns: 6                  # how much recent transcript the soft check sees
  enabled: true

pricing:                           # INR per 1M tokens; used for cost.* in the artifact
  sarvam-30b:  { input: 0.0, output: 0.0 }
  sarvam-105b: { input: 0.0, output: 0.0 }
```

---

## 10. Integration checklist — the assertions that catch a broken build

1. `dynamic_variables` is a **top-level sibling** of `conversation_config_override` in the init frame.
2. The first thing the runner does after `open()` is `recv_agent_turn()`. **The agent speaks first.**
3. Every `ping` gets an immediate `pong`. Nothing sleeps on `ping_ms`. The first `ping_ms` is `null`.
4. `audio` events arrive and are discarded. Their presence is **not** a failure.
   `agent_chat_response_part` events being present is the real proof `text_only` was honoured.
5. Two personas differing only in `end_when` produce **byte-identical** `system_prompt()`.
6. `reasoning_content` appears in **zero** outbound Sarvam messages.
7. `persona_brain.max_tokens >= 2000` everywhere; `content: None` is retried, not raised.
8. Hard conditions never make a network call. `hard_stop` outranks every soft result.
9. A crashed conversation still writes a valid `conversations/<persona_id>.json`.
10. No API key, signed-URL token, or `prompts/*.system.txt` content appears in any file the judge reads.
11. `simulate-conversation` appears **nowhere** in the codebase. Grep for it in CI.
