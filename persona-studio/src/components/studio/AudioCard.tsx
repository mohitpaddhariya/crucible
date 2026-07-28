import { useEffect, useRef, useState } from "react"
import { Pause, Play, RotateCcw } from "lucide-react"

import { Button } from "@/components/ui/button"
import { formatBytes, type AudioSource } from "@/hooks/usePipeline"
import { formatSeconds } from "@/lib/transcript"

/**
 * The uploaded recording, played back for real.
 *
 * This is the one part of the studio that is not a fixture — the audio element is
 * pointed at an object URL for the file the user actually dropped. Native `controls`
 * would have done the job, but its chrome is opaque in dark mode on most browsers and
 * cannot be made to line up with the rest of the panel, so the three controls we need
 * are driven directly off the media element's events.
 */
export function AudioCard({
  audio,
  onReplace,
}: {
  audio: AudioSource
  onReplace: () => void
}) {
  const ref = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [duration, setDuration] = useState<number | null>(null)

  // A new file means a new element state; reset rather than inherit the old position.
  useEffect(() => {
    setPlaying(false)
    setElapsed(0)
    setDuration(null)
  }, [audio.url])

  const toggle = () => {
    const element = ref.current
    if (element === null) return
    if (element.paused) void element.play()
    else element.pause()
  }

  const progress = duration === null || duration === 0 ? 0 : elapsed / duration

  return (
    <div className="rounded-xl bg-muted/40 p-4 ring-1 ring-foreground/10">
      <audio
        ref={ref}
        src={audio.url}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(event) => setElapsed(event.currentTarget.currentTime)}
        onLoadedMetadata={(event) => {
          const value = event.currentTarget.duration
          setDuration(Number.isFinite(value) ? value : null)
        }}
      />

      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-[0.8125rem] font-medium" title={audio.name}>
            {audio.name}
          </p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {formatBytes(audio.sizeBytes)}
            {duration === null ? "" : ` · ${formatSeconds(duration)}`}
          </p>
        </div>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={onReplace}
          aria-label="Start over with a different recording"
        >
          <RotateCcw />
        </Button>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <Button
          variant="secondary"
          size="icon-sm"
          onClick={toggle}
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? <Pause /> : <Play />}
        </Button>

        <input
          type="range"
          min={0}
          max={1000}
          value={Math.round(progress * 1000)}
          aria-label="Seek"
          onChange={(event) => {
            const element = ref.current
            if (element === null || duration === null) return
            const next = (Number(event.target.value) / 1000) * duration
            element.currentTime = next
            setElapsed(next)
          }}
          className="h-1 w-full grow appearance-none rounded-full bg-foreground/15 accent-primary outline-none [&::-webkit-slider-thumb]:size-2.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-foreground"
        />

        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
          {formatSeconds(elapsed)}
        </span>
      </div>
    </div>
  )
}
