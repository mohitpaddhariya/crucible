import Demo from './components/Demo';
import {
  getPersonas,
  getConversations,
  getScorecards,
  getDefects,
  RUN_ID,
} from './lib/data';

// Read from disk on every request so the demo always reflects the real files.
export const dynamic = 'force-dynamic';

export default function Page() {
  return (
    <Demo
      runId={RUN_ID}
      personas={getPersonas()}
      conversations={getConversations()}
      scorecards={getScorecards()}
      defects={getDefects()}
    />
  );
}
