import { Check, CircleDashed, LoaderCircle, TriangleAlert } from "lucide-react"

import type { Stage } from "@/hooks/usePipeline"
import { cn } from "@/lib/utils"

type StepState = "todo" | "running" | "done" | "failed"

type Step = {
  readonly id: string
  readonly title: string
  readonly running: string
  readonly done: string
}

const STEPS: readonly Step[] = [
  {
    id: "audio",
    title: "Recording",
    running: "Reading the file",
    done: "Loaded and playable",
  },
  {
    id: "transcript",
    title: "Transcript",
    running: "Transcribing the call",
    done: "Diarised, with timestamps",
  },
  {
    id: "persona",
    title: "Persona",
    running: "Reading the customer off the call",
    done: "Grounded in what was said",
  },
]

/** Where each of the three steps stands, derived from the one source of truth. */
function statesFor(stage: Stage): readonly [StepState, StepState, StepState] {
  switch (stage.kind) {
    case "awaiting-audio":
      return ["todo", "todo", "todo"]
    case "transcribing":
      return ["done", "running", "todo"]
    case "transcribed":
      return ["done", "done", "todo"]
    case "generating":
      return ["done", "done", "running"]
    case "ready":
      return ["done", "done", "done"]
    case "failed":
      return ["done", "done", "failed"]
  }
}

export function StageRail({ stage }: { stage: Stage }) {
  const states = statesFor(stage)

  return (
    <ol className="space-y-1">
      {STEPS.map((step, index) => {
        const state = states[index] ?? "todo"
        return (
          <li key={step.id} className="flex gap-3 py-2">
            <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center">
              <StepIcon state={state} />
            </span>
            <div className="min-w-0">
              <p
                className={cn(
                  "text-[0.8125rem] font-medium transition-colors",
                  state === "todo" ? "text-muted-foreground" : "text-foreground",
                )}
              >
                {step.title}
              </p>
              <p className="text-xs text-muted-foreground">
                {state === "running"
                  ? step.running
                  : state === "done"
                    ? step.done
                    : state === "failed"
                      ? "Could not be built"
                      : "Waiting"}
              </p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

function StepIcon({ state }: { state: StepState }) {
  switch (state) {
    case "done":
      return <Check className="size-3.5 text-emerald-500" />
    case "running":
      return <LoaderCircle className="size-3.5 animate-spin text-primary" />
    case "failed":
      return <TriangleAlert className="size-3.5 text-destructive" />
    case "todo":
      return <CircleDashed className="size-3.5 text-muted-foreground/50" />
  }
}
