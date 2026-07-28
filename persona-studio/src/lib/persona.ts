/**
 * The persona domain model, and the single boundary at which untyped YAML becomes it.
 *
 * Two rules hold this file together:
 *
 *  1. Nothing downstream ever touches `unknown`. `parsePersona` is the only place that
 *     inspects raw YAML, so every component below it can be total — no optional chaining
 *     into nested maps, no `?? "—"` sprinkled through JSX.
 *  2. Illegal states are unrepresentable. `end_when` arrives as a list of single-key maps
 *     (`{turns_over: 14}`), which permits `{turns_over: 14, seconds_over: 300}` and
 *     `{}` — both meaningless. It is parsed into a discriminated union so neither exists.
 */

// ---------------------------------------------------------------------------
// Model
// ---------------------------------------------------------------------------

export type Identity = {
  readonly who: string
  readonly situation: string
}

export type Language = {
  readonly primary: string
  readonly rule: string
}

export type Behaviour = {
  readonly tone: string
  readonly tactics: readonly string[]
  readonly arc: string
  readonly never: readonly string[]
}

export type Goal = {
  readonly wants: string
  readonly accepts: string
  readonly walksAwayAfter: string
}

/**
 * The 11 dynamic variables handed to the target agent. The three
 * `payment_recovery`-only fields are blank on a `win_back` call, so they are modelled as
 * nullable rather than as empty strings the UI has to remember to filter.
 */
export type ScenarioVars = {
  readonly subscriberName: string
  readonly callReason: string
  readonly callIntro: string
  readonly planName: string
  readonly amountInr: string
  readonly expiryDate: string
  readonly contentHook: string
  readonly offerText: string
  readonly renewalDate: string | null
  readonly nextRetryDate: string | null
  readonly failureReason: string | null
}

/** Judge-only. Never rendered into any prompt the persona or the agent can see. */
export type GroundTruth = {
  readonly discountCeilingPct: number
  readonly offerSummary: string
  readonly validPlanNames: readonly string[]
  readonly validPricesInr: readonly number[]
  readonly validDates: readonly string[]
  readonly claimsAgentMayMake: readonly string[]
  readonly claimsAgentMustNotMake: readonly string[]
}

export type Scenario = {
  readonly vars: ScenarioVars
  readonly groundTruth: GroundTruth
  readonly customerBrief: string
}

/** A flag condition that is either present or absent — it carries no payload. */
export type EndFlag =
  | "goal_reached"
  | "agent_offers_human_handoff"
  | "persona_walked_away"

export type EndCondition =
  | { readonly kind: "turns_over"; readonly turns: number }
  | { readonly kind: "seconds_over"; readonly seconds: number }
  | { readonly kind: "flag"; readonly flag: EndFlag }

/** Runner-only. Must never reach the persona model. */
export type EndWhen = {
  readonly any: readonly EndCondition[]
  readonly hardStopTurns: number
}

export type Voice = {
  readonly model: string
  readonly speaker: string
  readonly pace: number
}

export type Persona = {
  readonly id: string
  readonly name: string
  /** The evaluation dimension this persona is built to break. Absent on controls. */
  readonly stresses: string | null
  readonly control: boolean
  readonly identity: Identity
  readonly language: Language
  readonly behaviour: Behaviour
  readonly goal: Goal
  readonly scenario: Scenario
  readonly voice: Voice | null
  readonly endWhen: EndWhen
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

export type ParseResult<T> =
  | { readonly ok: true; readonly value: T }
  | { readonly ok: false; readonly error: string }

/**
 * Thrown and caught entirely inside this module. Every reader below throws on bad input
 * so the happy path reads like a plain object literal; `parsePersona` is the one place
 * that converts the throw back into a value.
 */
class ParseError extends Error {
  constructor(path: string, detail: string) {
    super(`${path}: ${detail}`)
    this.name = "ParseError"
  }
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v)

const describe = (v: unknown): string => {
  if (v === null) return "null"
  if (Array.isArray(v)) return "a list"
  return typeof v
}

/** Read `key` off a mapping, failing loudly if the parent is not a mapping at all. */
const at = (parent: unknown, path: string, key: string): unknown => {
  if (!isRecord(parent)) throw new ParseError(path, `expected a mapping, got ${describe(parent)}`)
  return parent[key]
}

const str = (v: unknown, path: string): string => {
  if (typeof v !== "string") throw new ParseError(path, `expected text, got ${describe(v)}`)
  const trimmed = v.trim()
  if (trimmed === "") throw new ParseError(path, "must not be empty")
  return trimmed
}

/** Blank and absent mean the same thing for the flow-specific scenario vars. */
const nullableStr = (v: unknown, path: string): string | null => {
  if (v === undefined || v === null) return null
  if (typeof v !== "string") throw new ParseError(path, `expected text, got ${describe(v)}`)
  const trimmed = v.trim()
  return trimmed === "" ? null : trimmed
}

const num = (v: unknown, path: string): number => {
  if (typeof v !== "number" || !Number.isFinite(v)) {
    throw new ParseError(path, `expected a number, got ${describe(v)}`)
  }
  return v
}

const bool = (v: unknown, path: string, fallback: boolean): boolean => {
  if (v === undefined || v === null) return fallback
  if (typeof v !== "boolean") throw new ParseError(path, `expected true or false, got ${describe(v)}`)
  return v
}

const list = <T,>(v: unknown, path: string, read: (item: unknown, path: string) => T): T[] => {
  if (v === undefined || v === null) return []
  if (!Array.isArray(v)) throw new ParseError(path, `expected a list, got ${describe(v)}`)
  return v.map((item, i) => read(item, `${path}[${i}]`))
}

const END_FLAGS: readonly EndFlag[] = [
  "goal_reached",
  "agent_offers_human_handoff",
  "persona_walked_away",
]

const isEndFlag = (key: string): key is EndFlag =>
  END_FLAGS.some((flag) => flag === key)

/**
 * One `end_when.any` entry. YAML gives us a mapping of exactly one key; anything else is
 * ambiguous, so it is rejected rather than silently half-read.
 */
const readEndCondition = (raw: unknown, path: string): EndCondition => {
  if (!isRecord(raw)) throw new ParseError(path, `expected a mapping, got ${describe(raw)}`)
  const entries = Object.entries(raw)
  const [entry] = entries
  if (entry === undefined) throw new ParseError(path, "is empty")
  if (entries.length > 1) {
    throw new ParseError(path, `must hold exactly one condition, found ${entries.length}`)
  }
  const [key, value] = entry
  if (key === "turns_over") return { kind: "turns_over", turns: num(value, `${path}.turns_over`) }
  if (key === "seconds_over") {
    return { kind: "seconds_over", seconds: num(value, `${path}.seconds_over`) }
  }
  if (isEndFlag(key)) {
    // `goal_reached: false` is a condition that can never fire — drop the value, keep the
    // flag only when it is actually armed.
    if (bool(value, `${path}.${key}`, true)) return { kind: "flag", flag: key }
    throw new ParseError(path, `${key} is set to false, which can never end a call`)
  }
  throw new ParseError(path, `unknown end condition "${key}"`)
}

const readVoice = (raw: unknown): Voice | null => {
  if (raw === undefined || raw === null) return null
  return {
    model: str(at(raw, "voice", "model"), "voice.model"),
    speaker: str(at(raw, "voice", "speaker"), "voice.speaker"),
    pace: num(at(raw, "voice", "pace"), "voice.pace"),
  }
}

const readPersona = (raw: unknown): Persona => {
  const identity = at(raw, "", "identity")
  const language = at(raw, "", "language")
  const behaviour = at(raw, "", "behaviour")
  const goal = at(raw, "", "goal")
  const scenario = at(raw, "", "scenario")
  const vars = at(scenario, "scenario", "vars")
  const truth = at(scenario, "scenario", "ground_truth")
  const endWhen = at(raw, "", "end_when")

  return {
    id: str(at(raw, "", "id"), "id"),
    name: str(at(raw, "", "name"), "name"),
    stresses: nullableStr(at(raw, "", "stresses"), "stresses"),
    control: bool(at(raw, "", "control"), "control", false),
    identity: {
      who: str(at(identity, "identity", "who"), "identity.who"),
      situation: str(at(identity, "identity", "situation"), "identity.situation"),
    },
    language: {
      primary: str(at(language, "language", "primary"), "language.primary"),
      rule: str(at(language, "language", "rule"), "language.rule"),
    },
    behaviour: {
      tone: str(at(behaviour, "behaviour", "tone"), "behaviour.tone"),
      tactics: list(at(behaviour, "behaviour", "tactics"), "behaviour.tactics", str),
      arc: str(at(behaviour, "behaviour", "arc"), "behaviour.arc"),
      never: list(at(behaviour, "behaviour", "never"), "behaviour.never", str),
    },
    goal: {
      wants: str(at(goal, "goal", "wants"), "goal.wants"),
      accepts: str(at(goal, "goal", "accepts"), "goal.accepts"),
      walksAwayAfter: str(at(goal, "goal", "walks_away_after"), "goal.walks_away_after"),
    },
    scenario: {
      customerBrief: str(at(scenario, "scenario", "customer_brief"), "scenario.customer_brief"),
      vars: {
        subscriberName: str(at(vars, "scenario.vars", "subscriber_name"), "scenario.vars.subscriber_name"),
        callReason: str(at(vars, "scenario.vars", "call_reason"), "scenario.vars.call_reason"),
        callIntro: str(at(vars, "scenario.vars", "call_intro"), "scenario.vars.call_intro"),
        planName: str(at(vars, "scenario.vars", "plan_name"), "scenario.vars.plan_name"),
        amountInr: str(at(vars, "scenario.vars", "amount_inr"), "scenario.vars.amount_inr"),
        expiryDate: str(at(vars, "scenario.vars", "expiry_date"), "scenario.vars.expiry_date"),
        contentHook: str(at(vars, "scenario.vars", "content_hook"), "scenario.vars.content_hook"),
        offerText: str(at(vars, "scenario.vars", "offer_text"), "scenario.vars.offer_text"),
        renewalDate: nullableStr(at(vars, "scenario.vars", "renewal_date"), "scenario.vars.renewal_date"),
        nextRetryDate: nullableStr(at(vars, "scenario.vars", "next_retry_date"), "scenario.vars.next_retry_date"),
        failureReason: nullableStr(at(vars, "scenario.vars", "failure_reason"), "scenario.vars.failure_reason"),
      },
      groundTruth: {
        discountCeilingPct: num(at(truth, "scenario.ground_truth", "discount_ceiling_pct"), "scenario.ground_truth.discount_ceiling_pct"),
        offerSummary: str(at(truth, "scenario.ground_truth", "offer_summary"), "scenario.ground_truth.offer_summary"),
        validPlanNames: list(at(truth, "scenario.ground_truth", "valid_plan_names"), "scenario.ground_truth.valid_plan_names", str),
        validPricesInr: list(at(truth, "scenario.ground_truth", "valid_prices_inr"), "scenario.ground_truth.valid_prices_inr", num),
        validDates: list(at(truth, "scenario.ground_truth", "valid_dates"), "scenario.ground_truth.valid_dates", str),
        claimsAgentMayMake: list(at(truth, "scenario.ground_truth", "claims_agent_may_make"), "scenario.ground_truth.claims_agent_may_make", str),
        claimsAgentMustNotMake: list(at(truth, "scenario.ground_truth", "claims_agent_must_not_make"), "scenario.ground_truth.claims_agent_must_not_make", str),
      },
    },
    voice: readVoice(at(raw, "", "voice")),
    endWhen: {
      any: list(at(endWhen, "end_when", "any"), "end_when.any", readEndCondition),
      hardStopTurns: num(
        at(at(endWhen, "end_when", "hard_stop"), "end_when.hard_stop", "turns"),
        "end_when.hard_stop.turns",
      ),
    },
  }
}

/**
 * Turn a parsed-YAML document into a `Persona`, or explain in one line why it is not one.
 * `raw` is deliberately `unknown` — callers must not pre-narrow it.
 */
export function parsePersona(raw: unknown): ParseResult<Persona> {
  try {
    return { ok: true, value: readPersona(raw) }
  } catch (error) {
    if (error instanceof ParseError) return { ok: false, error: error.message }
    throw error
  }
}
