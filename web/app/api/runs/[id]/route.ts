/**
 * GET /api/runs/[id]
 *
 * Everything the UI needs for one run: merged conversations + scorecards +
 * structured synthesis. `report.md` is never parsed — only its existence is
 * reported, via `hasReport`.
 */

import { getRunDetail } from '@/lib/runs';
import { safeSegment } from '@/lib/paths';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;

  if (!safeSegment(id)) {
    return Response.json({ error: 'invalid run id' }, { status: 400 });
  }

  try {
    const detail = getRunDetail(id);
    if (!detail) {
      return Response.json({ error: `run not found: ${id}` }, { status: 404 });
    }
    return Response.json(detail, { headers: { 'Cache-Control': 'no-store' } });
  } catch (e) {
    return Response.json(
      { error: `failed to read run ${id}: ${(e as Error).message}` },
      { status: 500 }
    );
  }
}
