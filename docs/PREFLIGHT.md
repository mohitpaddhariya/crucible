# Preflight — verified facts

Run 25 July 2026 against live credentials. Everything below was **observed from the API**, not assumed.

---

## 1. ElevenLabs — credentials

| Check | Result |
|---|---|
| `GET /v1/user` | **200** — key valid |
| Tier | `creator` |
| Character quota | 13,154 / 170,899 used |

## 2. Target agent — confirmed

| Field | Value |
|---|---|
| `agent_id` | `agent_9801kv9rahs8fzaa0dj6x85aq6dc` |
| name | `jiohotstar-tara-winback-recovery` |
| language | `en` |
| LLM | `qwen35-397b-a17b` |
| system prompt | 14,924 chars |
| max duration | 600 s |
| `conversation.text_only` (current) | `false` |

**Note the agent's own LLM is Qwen, not an ElevenLabs model.** Good for the judge — different model family from Sarvam, so self-enhancement bias is less of a worry.

## 3. ⭐ The Phase 0 gate — PASSES

`platform_settings.overrides.conversation_config_override`:

```json
"conversation": { "text_only": true },
"agent": { "first_message": true, "language": false,
           "prompt": { "prompt": false, "llm": false, "knowledge_base": false } }
```

**`text_only` is already allowed as a runtime override on this agent.** Level 0 is viable — we can flip the live agent to text mode per-conversation without editing it.

Equally important, **these are locked** (`false`): `prompt`, `llm`, `language`, `knowledge_base`, all TTS settings.

That's exactly what we want. We can change *how we talk to Tara*, never *who Tara is*. The agent under test cannot be accidentally softened.

## 4. ⭐ Dynamic variables — the scenario lives here

`first_message` is a template:

```
Hi {{subscriber_name}}, this is Tara from JioHotstar. {{call_intro}} Would you prefer English or Hindi?
```

Declared placeholders:

| Variable | Default |
|---|---|
| `subscriber_name` | Aravinth |
| `call_reason` | win_back |
| `plan_name` | JioHotstar Super (annual) |
| `amount_inr` | 1499 |
| `expiry_date` | 20 June |
| `content_hook` | the ICC Women's T20 World Cup, live through 5 July |
| `offer_text` | **10% off if you reactivate before 20 June** |
| `renewal_date`, `next_retry_date`, `failure_reason` | empty |
| `call_intro` | (truncated in response) |

**Two consequences:**

1. We **must** send `dynamic_variables` in `conversation_initiation_client_data`, or the opening line renders raw `{{...}}` and the whole conversation starts broken.
2. `offer_text` is **Tara's actual discount ceiling: 10%.** That is the number `price-haggler` is testing. A persona that pushes for 30% and gets it has caught a real instruction-adherence failure. This gives the rubric a hard, checkable ground truth instead of a vibe.

→ Each persona should carry its own `scenario:` block supplying these variables.

## 5. Sarvam — models and the reasoning problem

Available: **`sarvam-30b`, `sarvam-105b`**. `sarvam-m` is deprecated (returns 400).

### Both models are reasoning models, and reasoning cannot be disabled

Tried and failed: `reasoning_effort: "none"` (400 — only `low`/`medium`/`high` accepted), `chat_template_kwargs.enable_thinking: false`, `thinking: false`. **None had any effect.** `reasoning_effort: "low"` produced *more* reasoning than baseline, not less.

Reasoning output goes to a separate `reasoning_content` field and **consumes the `max_tokens` budget before `content` is written.**

### Measured — one short Hinglish persona reply

| `max_tokens` | time | completion tokens | reasoning chars | finish | content |
|---|---|---|---|---|---|
| 400 | 1.5 s | 400 | 1,390 | `length` | **None** |
| 800 | 2.8 s | 800 | 2,737 | `length` | **None** |
| 1200 | 4.0 s | 1,150 | 3,863 | `stop` | ✅ good |
| 2000 | 5.8 s | 1,665 | 6,068 | `stop` | ✅ good |

At 2000 tokens, `sarvam-30b` produced:

> *"English bhai! But 10% off is not enough for the cricket, yaar. I need a better deal!"*

Correct persona, correct Hinglish, correctly pushing back on the real 10% offer.

`sarvam-105b` is **11.7 s** for the same turn and still hit the length cap at 1200 — too slow for a 12-turn persona loop.

### Rules this forces

- **`max_tokens: 2000` minimum** for the persona. Below ~1200 you get `content: None`.
- **Treat `finish_reason == "length"` with `content is None` as a retryable failure**, not a crash. We will hit it.
- **Never feed `reasoning_content` back into history.** Log it, drop it from the transcript, or context explodes.
- **`reasoning_effort` is not a useful lever.** Ignore it.
- ~1,700 completion tokens per persona turn. The spec's "cost is negligible" line was wrong — **budget properly.**

### Structured output works

`response_format` with a strict `json_schema` returned clean valid JSON on `sarvam-105b` (10.8 s). The judge's evidence-pinned scorecard is viable.

## 6. Model assignment

| Role | Model | Why |
|---|---|---|
| Persona brain | `sarvam-30b`, `max_tokens: 2000` | 4–6 s/turn is livable ×12 turns; quality confirmed good |
| Judge | `sarvam-105b`, `max_tokens: 2000` | offline, latency irrelevant, structured JSON confirmed |
| Synthesizer | `sarvam-105b` | same |

Judge ≠ persona model, and neither matches Tara's Qwen. Bias separation holds.

## 7. Workspace — partially verified

Target: `5468d2009f4843248f137247f5cbe21a`

- `/v1/workspace` → 401 (endpoint not available in this form)
- `/v1/workspace/members` → **403 with a permissions error** — auth succeeded, key lacks workspace-management scope
- The workspace ID string does **not** appear anywhere in the agent payload

**Could not read the workspace ID back with this key.** What *is* confirmed: the key resolves to a workspace containing exactly three agents —

```
agent_2801kx5hwew0ezp8kd8mjq7cjd31   Edelweiss MF Payment Failure Recovery (Meera)
agent_4101kw8kd6wwft3v3y8s78fd9pd1   nicobar-cart-recovery-maya
agent_9801kv9rahs8fzaa0dj6x85aq6dc   jiohotstar-tara-winback-recovery   ← target
```

ElevenLabs keys are workspace-scoped, so if those three are the agents in `5468d200…`, we are in the right workspace. **Needs human confirmation** — do not treat as verified.
