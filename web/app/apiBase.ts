/**
 * Where the run artifacts come from.
 *
 * Locally the Next route handlers under `app/api/**` read `runs/` off disk, so same-origin
 * is correct. On Vercel that directory does not exist — the artifacts are ~40 MB of recorded
 * conversations and stitched WAVs that have no business in a serverless bundle — so requests
 * go to the Modal backend, which serves exactly the bytes those same handlers produce.
 *
 * Decided at RUNTIME from the hostname, not at build time from an env var. The first attempt
 * used NEXT_PUBLIC_API_BASE and the deployment silently shipped without it: Vercel stored the
 * value as sensitive, it never reached the build, `process.env.NEXT_PUBLIC_API_BASE` inlined
 * as undefined, and the dashboard fetched same-origin `/api/runs` — which answers 200 with
 * `[]` on Vercel rather than failing, so the page rendered "no conversations yet" and looked
 * like empty data instead of a broken deploy. Nothing about this value is secret; it is
 * readable in the client bundle by design. Reading it from the hostname removes a build-time
 * dependency that could only ever fail silently.
 *
 * An explicit NEXT_PUBLIC_API_BASE still wins, for pointing a local UI at a remote backend.
 */
const REMOTE_API = 'https://mohit-paddhariya--crucible-backend-api.modal.run';

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '0.0.0.0', '[::1]']);

function resolveBase(): string {
  const explicit = (process.env.NEXT_PUBLIC_API_BASE ?? '').trim();
  if (explicit) return explicit.replace(/\/$/, '');

  // Server-side render / prerender: same-origin is the only meaningful answer, and the
  // dashboard fetches on the client anyway.
  if (typeof window === 'undefined') return '';

  return LOCAL_HOSTS.has(window.location.hostname) ? '' : REMOTE_API;
}

/** `/api/runs` → the deployed backend when this page is not served from localhost. */
export function apiUrl(path: string): string {
  return `${resolveBase()}${path}`;
}

/** Base for URLs handed to an <audio> element, which cannot go through apiUrl(). */
export function apiBase(): string {
  return resolveBase();
}
