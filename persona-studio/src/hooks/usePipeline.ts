import { useCallback, useEffect, useRef, useState } from "react"

import { generatedPersona, transcript as fixtureTranscript } from "@/data"
import type { Persona } from "@/lib/persona"
import type { Transcript } from "@/lib/transcript"

/** An MP3 the user handed us. `url` is an object URL owned by this hook. */
export type AudioSource = {
  readonly name: string
  readonly sizeBytes: number
  readonly url: string
}

/**
 * The pipeline as a state machine rather than a bag of booleans.
 *
 * The payload rides along with the state, so a component that renders the transcript can
 * only be reached from a state that has one. There is no `transcript: Transcript | null`
 * anywhere, and therefore no "cannot read property of null" to defend against.
 */
export type Stage =
  | { readonly kind: "awaiting-audio"; readonly rejected: string | null }
  | { readonly kind: "transcribing"; readonly audio: AudioSource }
  | {
      readonly kind: "transcribed"
      readonly audio: AudioSource
      readonly transcript: Transcript
    }
  | {
      readonly kind: "generating"
      readonly audio: AudioSource
      readonly transcript: Transcript
    }
  | {
      readonly kind: "ready"
      readonly audio: AudioSource
      readonly transcript: Transcript
      readonly persona: Persona
    }
  | {
      readonly kind: "failed"
      readonly audio: AudioSource
      readonly transcript: Transcript
      readonly error: string
    }

/**
 * How long the two fake steps take. Real work will replace these with real awaits; the
 * durations exist so the stage UI is legible rather than a flash.
 */
const TRANSCRIBE_MS = 2200
const GENERATE_MS = 2600

const isMp3 = (file: File): boolean =>
  file.type === "audio/mpeg" || file.name.toLowerCase().endsWith(".mp3")

const formatBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export { formatBytes }

export type Pipeline = {
  readonly stage: Stage
  /** Hand the pipeline an MP3. Anything else is refused with a reason. */
  readonly accept: (file: File) => void
  /** Move from `transcribed` to `generating`. No-op from any other state. */
  readonly generate: () => void
  readonly reset: () => void
}

export function usePipeline(): Pipeline {
  const [stage, setStage] = useState<Stage>({ kind: "awaiting-audio", rejected: null })

  // Object URLs are a manual allocation; the ref is what lets `reset` and unmount both
  // free the same one without the effect re-running on every state change.
  const objectUrl = useRef<string | null>(null)
  const release = useCallback(() => {
    if (objectUrl.current !== null) {
      URL.revokeObjectURL(objectUrl.current)
      objectUrl.current = null
    }
  }, [])
  useEffect(() => release, [release])

  const accept = useCallback(
    (file: File) => {
      if (!isMp3(file)) {
        setStage({
          kind: "awaiting-audio",
          rejected: `${file.name} is not an MP3. This studio only reads .mp3 call recordings.`,
        })
        return
      }
      release()
      const url = URL.createObjectURL(file)
      objectUrl.current = url
      setStage({
        kind: "transcribing",
        audio: { name: file.name, sizeBytes: file.size, url },
      })
    },
    [release],
  )

  const generate = useCallback(() => {
    setStage((current) =>
      current.kind === "transcribed"
        ? { kind: "generating", audio: current.audio, transcript: current.transcript }
        : current,
    )
  }, [])

  const reset = useCallback(() => {
    release()
    setStage({ kind: "awaiting-audio", rejected: null })
  }, [release])

  // The two timed steps. Keyed on `stage.kind`, so a reset mid-flight clears the timer
  // instead of resolving into a stale state.
  useEffect(() => {
    if (stage.kind === "transcribing") {
      const timer = window.setTimeout(() => {
        setStage({
          kind: "transcribed",
          audio: stage.audio,
          transcript: fixtureTranscript,
        })
      }, TRANSCRIBE_MS)
      return () => window.clearTimeout(timer)
    }

    if (stage.kind === "generating") {
      const timer = window.setTimeout(() => {
        setStage(
          generatedPersona.ok
            ? {
                kind: "ready",
                audio: stage.audio,
                transcript: stage.transcript,
                persona: generatedPersona.value,
              }
            : {
                kind: "failed",
                audio: stage.audio,
                transcript: stage.transcript,
                error: generatedPersona.error,
              },
        )
      }, GENERATE_MS)
      return () => window.clearTimeout(timer)
    }

    return undefined
  }, [stage])

  return { stage, accept, generate, reset }
}
