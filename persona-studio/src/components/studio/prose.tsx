import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

/**
 * The four shapes every piece of persona detail collapses into.
 *
 * Deliberately small. The persona report is long, and the only way it stays readable —
 * and stays honest about which parts are prose from the file and which are labels we
 * added — is if there is exactly one way to render a heading, a paragraph, a labelled
 * fact and a list.
 */

export function Section({
  title,
  aside,
  children,
}: {
  title: string
  /** Who reads this part of the persona, e.g. "the customer model". */
  aside?: string
  children: ReactNode
}) {
  return (
    <section className="border-t border-border/60 py-7 first:border-t-0 first:pt-0">
      <div className="mb-3.5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
        {aside === undefined ? null : (
          <span className="text-xs text-muted-foreground">{aside}</span>
        )}
      </div>
      <div className="space-y-4">{children}</div>
    </section>
  )
}

export function Paragraph({
  children,
  muted = false,
}: {
  children: ReactNode
  muted?: boolean
}) {
  return (
    <p
      className={cn(
        "max-w-prose text-[0.9375rem] leading-relaxed text-balance",
        muted ? "text-muted-foreground" : "text-foreground/90",
      )}
    >
      {children}
    </p>
  )
}

/** A `label — value` row. Values are always plain sentences, never YAML fragments. */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[9.5rem_1fr] sm:gap-4">
      <dt className="pt-px text-xs font-medium text-muted-foreground sm:text-right">
        {label}
      </dt>
      <dd className="max-w-prose text-[0.9375rem] leading-relaxed text-foreground/90">
        {children}
      </dd>
    </div>
  )
}

export function Facts({ children }: { children: ReactNode }) {
  return <dl className="space-y-3">{children}</dl>
}

/**
 * A list of behaviours or claims. `tone` is what separates "things it may do" from
 * "things it must never do" at a glance — the two lists otherwise look identical and
 * getting them the wrong way round is the expensive mistake.
 */
export function Bullets({
  items,
  tone = "neutral",
}: {
  items: readonly string[]
  tone?: "neutral" | "allow" | "forbid"
}) {
  if (items.length === 0) return null

  const marker =
    tone === "allow"
      ? "before:bg-emerald-500/70"
      : tone === "forbid"
        ? "before:bg-destructive/70"
        : "before:bg-muted-foreground/40"

  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li
          key={item}
          className={cn(
            "relative max-w-prose pl-5 text-[0.9375rem] leading-relaxed text-foreground/90",
            "before:absolute before:top-[0.6875rem] before:left-0 before:size-1.5 before:rounded-full before:content-['']",
            marker,
          )}
        >
          {item}
        </li>
      ))}
    </ul>
  )
}
