/**
 * Persona → English.
 *
 * The point of the studio is that nobody should have to read YAML to know what was
 * generated. Every function here is pure and returns display text; none of them return
 * markup, so the phrasing stays testable and the components stay about layout.
 *
 * Where a value is already prose in the source file (`identity.who`, `behaviour.arc`) it
 * is passed through untouched — rewording an author's sentence would be lying about what
 * the file says. Only machine-shaped values (`end_when`, `scenario.vars`, enum-ish keys)
 * get turned into sentences here.
 */

import { formatSeconds } from "./transcript"
import type { EndCondition, EndWhen, Persona, ScenarioVars, Voice } from "./persona"

/** `instruction_adherence` → `Instruction adherence`. */
export function humanise(key: string): string {
  const spaced = key.replace(/[_-]+/g, " ").trim()
  const head = spaced.charAt(0)
  return head === "" ? "" : head.toUpperCase() + spaced.slice(1)
}

/**
 * What each evaluation dimension actually means, in the reviewer's language.
 * Unknown dimensions degrade to the humanised key rather than to a wrong explanation.
 */
const STRESS_BLURBS: Readonly<Record<string, string>> = {
  instruction_adherence:
    "whether the agent holds the limits it was given when the customer pushes against them",
  hallucination:
    "whether the agent invents facts about the plan, the price or the offer",
  escalation_safety:
    "whether the agent stays safe and in policy when the call turns hostile",
  goal_outcome:
    "whether a cooperative customer can actually get to the end of the journey",
}

export function describeStress(stresses: string | null): string | null {
  if (stresses === null) return null
  const blurb = STRESS_BLURBS[stresses]
  return blurb === undefined
    ? `Stresses ${humanise(stresses).toLowerCase()}.`
    : `Built to test ${blurb}.`
}

/** `win_back` → `win-back`, for use inside a sentence. */
const readableCallReason = (reason: string): string => reason.replace(/_/g, "-")

/**
 * The scenario the *target agent* is handed, as a paragraph. This is the only place the
 * eleven dynamic variables are stitched together, so the sentence order is the sentence
 * order everywhere in the app.
 */
export function describeScenario(vars: ScenarioVars): string {
  const parts = [
    `A ${readableCallReason(vars.callReason)} call to ${vars.subscriberName} about ${vars.planName}, priced at ₹${vars.amountInr} and expiring on ${vars.expiryDate}.`,
    `The agent leads with ${vars.contentHook}, and the offer it is allowed to put on the table is ${vars.offerText}.`,
  ]
  if (vars.failureReason !== null) {
    parts.push(`The payment on file failed because ${vars.failureReason}.`)
  }
  if (vars.nextRetryDate !== null) {
    parts.push(`It will be retried on ${vars.nextRetryDate}.`)
  }
  if (vars.renewalDate !== null) {
    parts.push(`The plan renews on ${vars.renewalDate}.`)
  }
  return parts.join(" ")
}

/** One end condition as a clause: "the call passes 14 turns". */
export function describeEndCondition(condition: EndCondition): string {
  switch (condition.kind) {
    case "turns_over":
      return `the call passes ${condition.turns} turns`
    case "seconds_over":
      return `the call runs past ${formatSeconds(condition.seconds)}`
    case "flag":
      switch (condition.flag) {
        case "goal_reached":
          return "the customer gets what they came for"
        case "agent_offers_human_handoff":
          return "the agent offers to hand over to a human"
        case "persona_walked_away":
          return "the customer walks away"
      }
  }
}

/** The whole stop policy as one sentence, hard stop included. */
export function describeEndWhen(endWhen: EndWhen): string {
  const clauses = endWhen.any.map(describeEndCondition)
  const hardStop = `A hard stop cuts it off at ${endWhen.hardStopTurns} turns regardless.`
  if (clauses.length === 0) return hardStop
  return `The call ends as soon as any of these happen — ${joinList(clauses)}. ${hardStop}`
}

export function describeVoice(voice: Voice): string {
  const pace =
    voice.pace === 1
      ? "at normal pace"
      : voice.pace > 1
        ? `${Math.round((voice.pace - 1) * 100)}% faster than normal`
        : `${Math.round((1 - voice.pace) * 100)}% slower than normal`
  return `Spoken by ${voice.speaker} on ${voice.model}, ${pace}.`
}

/** A one-line summary for the top of the card. */
export function describePersona(persona: Persona): string {
  const role = persona.control
    ? "a control persona"
    : persona.stresses === null
      ? "a persona"
      : `a persona that stresses ${humanise(persona.stresses).toLowerCase()}`
  return `${persona.name} is ${role}, speaking ${persona.language.primary}, on ${readableCallReason(persona.scenario.vars.callReason)} for ${persona.scenario.vars.planName}.`
}

/** `["a", "b", "c"]` → `"a, b, or c"`. Oxford-free, matches how the clauses read aloud. */
function joinList(items: readonly string[]): string {
  if (items.length <= 1) return items.join("")
  const head = items.slice(0, -1)
  const tail = items[items.length - 1]
  return `${head.join(", ")}, or ${tail ?? ""}`
}
