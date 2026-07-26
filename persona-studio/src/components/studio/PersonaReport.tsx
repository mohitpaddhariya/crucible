import { Download } from "lucide-react"

import { Bullets, Fact, Facts, Paragraph, Section } from "@/components/studio/prose"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { personaSource } from "@/data"
import {
  describeEndWhen,
  describePersona,
  describeScenario,
  describeStress,
  describeVoice,
  humanise,
} from "@/lib/narrate"
import type { Persona } from "@/lib/persona"

/**
 * The generated persona, in English.
 *
 * The YAML is never put on screen. A reviewer's question is "who is this, what will they
 * do, and what is the agent allowed to say back" — none of which is answered faster by
 * looking at indentation. The file itself is one click away as a download for whoever
 * actually needs to commit it.
 *
 * Sections are ordered by audience, and each one says whose eyes it is for. That split
 * is the part people get wrong: `ground_truth` reaching the customer model would hand it
 * the answer key, and `end_when` reaching it would tell it when to give up.
 */
export function PersonaReport({ persona }: { persona: Persona }) {
  const { identity, language, behaviour, goal, scenario, voice, endWhen } = persona
  const { vars, groundTruth } = scenario
  const stress = describeStress(persona.stresses)

  return (
    <div>
      <header className="mb-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight text-balance">
              {persona.name}
            </h2>
            <div className="mt-2.5 flex flex-wrap items-center gap-2">
              <Badge variant="outline" className="font-mono">
                {persona.id}
              </Badge>
              {persona.stresses === null ? null : (
                <Badge variant="secondary">{humanise(persona.stresses)}</Badge>
              )}
              {persona.control ? <Badge>Control</Badge> : null}
              <Badge variant="ghost" className="text-muted-foreground">
                {language.primary}
              </Badge>
            </div>
          </div>

          <Button variant="outline" size="sm" render={<a {...download(persona.id)} />}>
            <Download data-icon="inline-start" />
            Download YAML
          </Button>
        </div>

        <div className="mt-5 space-y-2">
          <Paragraph>{describePersona(persona)}</Paragraph>
          {stress === null ? null : <Paragraph muted>{stress}</Paragraph>}
        </div>
      </header>

      <Section title="Who they are" aside="read by the customer model">
        <Paragraph>{identity.who}</Paragraph>
        <Paragraph muted>{identity.situation}</Paragraph>
      </Section>

      <Section title="How they speak" aside="read by the customer model">
        <Facts>
          <Fact label="Primary">{language.primary}</Fact>
          <Fact label="Style">{language.rule}</Fact>
        </Facts>
      </Section>

      <Section title="How they behave" aside="read by the customer model">
        <Facts>
          <Fact label="Tone">{behaviour.tone}</Fact>
          <Fact label="Across the call">{behaviour.arc}</Fact>
        </Facts>
        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            Moves they actually make
          </p>
          <Bullets items={behaviour.tactics} />
        </div>
        {behaviour.never.length === 0 ? null : (
          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground">
              They never
            </p>
            <Bullets items={behaviour.never} tone="forbid" />
          </div>
        )}
      </Section>

      <Section title="What they want" aside="read by the customer model">
        <Facts>
          <Fact label="Ideally">{goal.wants}</Fact>
          <Fact label="Would still take">{goal.accepts}</Fact>
          <Fact label="Gives up when">{goal.walksAwayAfter}</Fact>
        </Facts>
      </Section>

      <Section title="What they think they know" aside="read by the customer model">
        <Paragraph>{scenario.customerBrief}</Paragraph>
        <Paragraph muted>
          Anything the agent claims beyond this is new information to them.
        </Paragraph>
      </Section>

      <Section title="The call your agent gets" aside="sent to the agent under test">
        <Paragraph>{describeScenario(vars)}</Paragraph>
        <Facts>
          <Fact label="Opens with">“{vars.callIntro}”</Fact>
          <Fact label="Plan">{vars.planName}</Fact>
          <Fact label="Price">₹{vars.amountInr}</Fact>
          <Fact label="Expires">{vars.expiryDate}</Fact>
          <Fact label="Offer">{vars.offerText}</Fact>
        </Facts>
      </Section>

      <Section title="What the judge scores against" aside="never shown to either side">
        <Facts>
          <Fact label="Discount ceiling">
            {groundTruth.discountCeilingPct}% — a higher number anywhere in the
            transcript is a provable failure, not a judgement call.
          </Fact>
          <Fact label="The real offer">{groundTruth.offerSummary}</Fact>
          <Fact label="True of this account">
            {trueFacts(
              groundTruth.validPlanNames,
              groundTruth.validPricesInr,
              groundTruth.validDates,
            )}
          </Fact>
        </Facts>
        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            The agent may say
          </p>
          <Bullets items={groundTruth.claimsAgentMayMake} tone="allow" />
        </div>
        <div>
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            The agent must never say
          </p>
          <Bullets items={groundTruth.claimsAgentMustNotMake} tone="forbid" />
        </div>
      </Section>

      <Section title="When the call stops" aside="the runner's rule, not the model's">
        <Paragraph>{describeEndWhen(endWhen)}</Paragraph>
      </Section>

      {voice === null ? null : (
        <Section title="Voice">
          <Paragraph>{describeVoice(voice)}</Paragraph>
        </Section>
      )}
    </div>
  )
}

/** The three ground-truth lists as one sentence rather than three bare arrays. */
function trueFacts(
  plans: readonly string[],
  prices: readonly number[],
  dates: readonly string[],
): string {
  const clauses: string[] = []
  if (plans.length > 0) {
    clauses.push(plans.length === 1 ? `the plan is ${plans[0]}` : `the plan is one of ${plans.join(", ")}`)
  }
  if (prices.length > 0) {
    clauses.push(
      prices.length === 1
        ? `the only real price is ₹${prices[0]}`
        : `the only real prices are ${prices.map((p) => `₹${p}`).join(" and ")}`,
    )
  }
  if (dates.length > 0) {
    clauses.push(
      dates.length === 1
        ? `the only real date is ${dates[0]}`
        : `the only real dates are ${dates.join(" and ")}`,
    )
  }
  if (clauses.length === 0) return "Nothing recorded."
  const sentence = clauses.join("; ")
  return `${sentence.charAt(0).toUpperCase()}${sentence.slice(1)}.`
}

/** Anchor props for saving the source file without ever rendering it. */
function download(id: string): { href: string; download: string } {
  return {
    href: `data:text/yaml;charset=utf-8,${encodeURIComponent(personaSource)}`,
    download: `${id}.yaml`,
  }
}
