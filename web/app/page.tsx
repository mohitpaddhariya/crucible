import App from './App';

/**
 * Thin shell. Everything on screen is fetched client-side from `/api/**`, which reads
 * `runs/` off disk — so there is no build-time snapshot to go stale and no run id baked
 * into the page. It used to import a hardcoded RUN_ID from ./lib/data; that is why.
 *
 * `force-dynamic` matches the route handlers under app/api (Next 16, cacheComponents off).
 */
export const dynamic = 'force-dynamic';

export default function Page() {
  return <App />;
}
