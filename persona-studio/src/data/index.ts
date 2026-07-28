/**
 * THE SWAP POINT.
 *
 * Everything the studio "produces" is fixed input read from disk at build time. There is
 * no transcription service and no generator behind the UI yet — this module is the seam
 * where they will be plugged in, and it is the only module that knows they are absent.
 *
 * To go live, replace the two imports below with calls that return the same shapes:
 *
 *   transcriptSource   ← POST the uploaded audio to the speech-to-text service
 *   personaSource      ← POST the transcript to the persona generator, take its YAML
 *
 * Nothing above this file assumes the data is static; `usePipeline` already models the
 * work as asynchronous stages that can fail.
 */

import YAML from "yaml"

import { parsePersona, type ParseResult, type Persona } from "@/lib/persona"
import { parseTranscript, type Transcript } from "@/lib/transcript"

import personaSource from "./generated-persona.yaml?raw"
import transcriptSource from "./streamnest-dhruvil.transcript.txt?raw"

/** The call this fixture was captured from. Shown so the demo never pretends otherwise. */
export const FIXTURE = {
  audioFileName: "streamnest_dhruvil_call.mp3",
  note: "Transcript and persona are fixed fixtures. Any MP3 you drop is played back for real, but is not sent anywhere.",
} as const

export const transcript: Transcript = parseTranscript(transcriptSource)

/**
 * Parsed once, eagerly. If a hand-swapped YAML file is malformed we want the failure on
 * screen as a sentence, not as a crash three components deep.
 */
export const generatedPersona: ParseResult<Persona> = ((): ParseResult<Persona> => {
  try {
    return parsePersona(YAML.parse(personaSource))
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error)
    return { ok: false, error: `The generated file is not valid YAML — ${detail}` }
  }
})()

/** Kept only so the raw file can be offered as a download; it is never rendered. */
export { personaSource }
