import { formatSeconds, type Transcript, type Turn } from "@/lib/transcript"
import { cn } from "@/lib/utils"

/**
 * The diarised call.
 *
 * Set in `transcript-type`, never monospace — these calls are Hindi and Hinglish and the
 * script does not survive a code face. Timestamps are the one thing that stays tabular,
 * because they are the anchor a reviewer scans down.
 */
export function TranscriptView({ transcript }: { transcript: Transcript }) {
  return (
    <div className="space-y-5">
      {transcript.turns.map((turn) => (
        <TurnRow key={turn.index} turn={turn} />
      ))}
    </div>
  )
}

function TurnRow({ turn }: { turn: Turn }) {
  const isAgent = turn.speaker === "agent"
  return (
    <article className="grid gap-1 sm:grid-cols-[3.25rem_1fr] sm:gap-4">
      <span className="pt-1 text-xs tabular-nums text-muted-foreground/70 sm:text-right">
        {formatSeconds(turn.atSeconds)}
      </span>
      <div className="min-w-0">
        <span
          className={cn(
            "text-[0.6875rem] font-semibold tracking-wide uppercase",
            isAgent ? "text-primary/80" : "text-muted-foreground",
          )}
        >
          {turn.label}
          <span className="ml-1.5 font-normal tracking-normal normal-case opacity-60">
            {isAgent ? "agent" : "customer"}
          </span>
        </span>
        <p className="transcript-type mt-0.5 max-w-[62ch] text-foreground/90">
          {turn.text}
        </p>
      </div>
    </article>
  )
}
