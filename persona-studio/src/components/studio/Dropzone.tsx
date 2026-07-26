import { useCallback, useRef, useState } from "react"
import { AlertCircle, FileAudio, UploadCloud } from "lucide-react"

import { Button } from "@/components/ui/button"
import { FIXTURE } from "@/data"
import { cn } from "@/lib/utils"

/**
 * Accepts one MP3, by drop or by file picker.
 *
 * The hidden `<input type="file">` is the real control — the visible surface is a label
 * bound to it — so keyboard and screen-reader users get the native picker for free
 * instead of a div pretending to be a button.
 */
export function Dropzone({
  onFile,
  rejected,
}: {
  onFile: (file: File) => void
  rejected: string | null
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const takeFirst = useCallback(
    (files: FileList | null) => {
      const file = files?.item(0)
      if (file !== null && file !== undefined) onFile(file)
    },
    [onFile],
  )

  return (
    <div>
      <label
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragging(false)
          takeFirst(event.dataTransfer.files)
        }}
        className={cn(
          "flex cursor-pointer flex-col items-center rounded-xl border border-dashed px-8 py-16 text-center transition-colors",
          dragging
            ? "border-primary bg-primary/5"
            : "border-border hover:border-foreground/25 hover:bg-muted/40",
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept="audio/mpeg,.mp3"
          className="sr-only"
          onChange={(event) => {
            takeFirst(event.target.files)
            // Let the same file be chosen twice in a row after a reset.
            event.target.value = ""
          }}
        />

        <div className="mb-5 flex size-11 items-center justify-center rounded-full bg-muted text-muted-foreground">
          {dragging ? (
            <FileAudio className="size-5" />
          ) : (
            <UploadCloud className="size-5" />
          )}
        </div>

        <p className="text-[0.9375rem] font-medium">
          Drop a call recording to build a persona
        </p>
        <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">
          One MP3 of a real customer call. We transcribe it, then turn the customer in it
          into a persona you can run against your agent.
        </p>

        <Button variant="outline" size="sm" className="mt-6" render={<span />}>
          Choose an MP3
        </Button>
      </label>

      {rejected === null ? (
        <p className="mt-3 text-xs text-muted-foreground">
          Reference recording: {FIXTURE.audioFileName}. {FIXTURE.note}
        </p>
      ) : (
        <p className="mt-3 flex items-start gap-2 text-xs text-destructive">
          <AlertCircle className="mt-px size-3.5 shrink-0" />
          <span>{rejected}</span>
        </p>
      )}
    </div>
  )
}
