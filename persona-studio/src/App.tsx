import type { ReactNode } from "react"
import { ArrowRight, Sparkles, TriangleAlert } from "lucide-react"

import { AudioCard } from "@/components/studio/AudioCard"
import { Dropzone } from "@/components/studio/Dropzone"
import { PersonaReport } from "@/components/studio/PersonaReport"
import { StageRail } from "@/components/studio/StageRail"
import { TranscriptView } from "@/components/studio/TranscriptView"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { usePipeline, type Stage } from "@/hooks/usePipeline"
import { formatSeconds } from "@/lib/transcript"

export default function App() {
  const { stage, accept, generate, reset } = usePipeline()

  return (
    <div className="min-h-svh">
      <header className="border-b border-border/60">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-5">
          <div className="flex items-center gap-2.5">
            <Sparkles className="size-4 text-primary" />
            <span className="text-sm font-semibold tracking-tight">
              Persona Studio
            </span>
          </div>
          <p className="hidden text-xs text-muted-foreground sm:block">
            A recorded call in, an evaluation persona out
          </p>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-10 px-6 py-10 lg:grid-cols-[16rem_1fr] lg:gap-14 lg:py-14">
        <aside className="lg:sticky lg:top-14 lg:self-start">
          <StageRail stage={stage} />
          {stage.kind === "awaiting-audio" ? null : (
            <div className="mt-6">
              <AudioCard audio={stage.audio} onReplace={reset} />
            </div>
          )}
        </aside>

        <div className="min-w-0">
          <Content stage={stage} onFile={accept} onGenerate={generate} />
        </div>
      </main>
    </div>
  )
}

function Content({
  stage,
  onFile,
  onGenerate,
}: {
  stage: Stage
  onFile: (file: File) => void
  onGenerate: () => void
}) {
  switch (stage.kind) {
    case "awaiting-audio":
      return <Dropzone onFile={onFile} rejected={stage.rejected} />

    case "transcribing":
      return (
        <Working
          title="Listening to the call"
          detail="Separating the two speakers and timestamping every turn."
        />
      )

    case "transcribed":
      return (
        <section>
          <Heading
            title="Transcript"
            detail={`${stage.transcript.turns.length} turns over ${formatSeconds(stage.transcript.durationSeconds)}. ${stage.transcript.agentLabel} is the agent, ${stage.transcript.customerLabel} is the customer.`}
            action={
              <Button onClick={onGenerate}>
                Generate persona
                <ArrowRight data-icon="inline-end" />
              </Button>
            }
          />
          <TranscriptView transcript={stage.transcript} />
        </section>
      )

    case "generating":
      return (
        <Working
          title="Reading the customer off the call"
          detail="Their situation, how they speak, what they push for, and where the agent's real limits were."
        />
      )

    case "failed":
      return (
        <div className="rounded-xl border border-destructive/40 bg-destructive/5 p-5">
          <p className="flex items-center gap-2 text-sm font-medium text-destructive">
            <TriangleAlert className="size-4" />
            The persona could not be built
          </p>
          <p className="mt-2 max-w-prose text-sm text-muted-foreground">{stage.error}</p>
        </div>
      )

    case "ready":
      return (
        <Tabs defaultValue="persona">
          <TabsList className="mb-8">
            <TabsTrigger value="persona">Persona</TabsTrigger>
            <TabsTrigger value="transcript">
              Transcript
              <span className="ml-1.5 text-muted-foreground">
                {stage.transcript.turns.length}
              </span>
            </TabsTrigger>
          </TabsList>
          <TabsContent value="persona">
            <PersonaReport persona={stage.persona} />
          </TabsContent>
          <TabsContent value="transcript">
            <TranscriptView transcript={stage.transcript} />
          </TabsContent>
        </Tabs>
      )
  }
}

function Heading({
  title,
  detail,
  action,
}: {
  title: string
  detail: string
  action?: ReactNode
}) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        <p className="mt-1 max-w-prose text-sm text-muted-foreground">{detail}</p>
      </div>
      {action}
    </div>
  )
}

/**
 * The in-flight state. Skeleton lines rather than a spinner, so the page does not jump
 * when the real content lands in its place.
 */
function Working({ title, detail }: { title: string; detail: string }) {
  return (
    <section>
      <Heading title={title} detail={detail} />
      <div className="space-y-6" aria-busy="true">
        {[0, 1, 2, 3].map((row) => (
          <div key={row} className="grid gap-2 sm:grid-cols-[3.25rem_1fr] sm:gap-4">
            <Skeleton className="h-4 w-10 justify-self-end" />
            <div className="space-y-2">
              <Skeleton className="h-3 w-24" />
              <Skeleton className="h-4 w-full max-w-[52ch]" />
              <Skeleton className="h-4 w-full max-w-[38ch]" />
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
