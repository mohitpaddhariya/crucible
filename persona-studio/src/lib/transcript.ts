/**
 * Parser for the timestamped two-speaker call transcript.
 *
 * The wire format is one turn per paragraph:
 *
 *     [  0s] TARA : Hi Dhruvil, this is Tara calling from StreamNest...
 *
 *     [ 15s] DHRUVIL: हाँ, मैं हिंग्लिश पसंद करूँगा।
 *
 * Speaker labels are whatever the transcription tool emitted, so they are not hardcoded.
 * Role is assigned positionally: on an outbound win-back call the agent always speaks
 * first, so the first label seen is the agent and every other label is the customer.
 */

export type Speaker = "agent" | "customer"

export type Turn = {
  readonly index: number
  /** Offset from the start of the call, in seconds. */
  readonly atSeconds: number
  readonly speaker: Speaker
  /** The label exactly as it appeared in the transcript, e.g. `TARA`. */
  readonly label: string
  readonly text: string
}

export type Transcript = {
  readonly turns: readonly Turn[]
  /** The label of whoever opened the call. */
  readonly agentLabel: string
  readonly customerLabel: string
  /** Timestamp of the last turn. The transcript carries no explicit end time. */
  readonly durationSeconds: number
}

const HEADER = /^\[\s*(\d+)s\]\s*([^:]+?)\s*:\s*(.*)$/

type Draft = { atSeconds: number; label: string; lines: string[] }

/**
 * Never throws. A transcript is display material, not a contract — a line that does not
 * parse is dropped rather than being allowed to blank the whole screen.
 */
export function parseTranscript(source: string): Transcript {
  const drafts: Draft[] = []

  for (const rawLine of source.split("\n")) {
    const line = rawLine.trimEnd()
    if (line.trim() === "") continue

    const match = HEADER.exec(line)
    if (match === null) {
      // A wrapped continuation of the turn above it.
      const current = drafts[drafts.length - 1]
      if (current !== undefined) current.lines.push(line.trim())
      continue
    }

    const [, seconds, label, head] = match
    if (seconds === undefined || label === undefined || head === undefined) continue

    drafts.push({
      atSeconds: Number.parseInt(seconds, 10),
      label: label.trim(),
      lines: head.trim() === "" ? [] : [head.trim()],
    })
  }

  const first = drafts[0]
  const agentLabel = first === undefined ? "AGENT" : first.label
  const customerLabel =
    drafts.find((d) => d.label !== agentLabel)?.label ?? "CUSTOMER"

  const turns = drafts.map((draft, index) => ({
    index,
    atSeconds: draft.atSeconds,
    speaker: draft.label === agentLabel ? ("agent" as const) : ("customer" as const),
    label: draft.label,
    text: draft.lines.join(" "),
  }))

  const last = turns[turns.length - 1]

  return {
    turns,
    agentLabel,
    customerLabel,
    durationSeconds: last === undefined ? 0 : last.atSeconds,
  }
}

/** `243` → `4:03`. */
export function formatSeconds(total: number): string {
  const safe = Math.max(0, Math.floor(total))
  const minutes = Math.floor(safe / 60)
  const seconds = safe % 60
  return `${minutes}:${seconds.toString().padStart(2, "0")}`
}
