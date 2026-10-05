import type { GateGroup, Gating, StartupFailure } from './types';

export function relTime(iso: string | null, now = Date.now()): string {
  if (!iso) return '';
  const mins = Math.max(0, Math.round((now - new Date(iso).getTime()) / 60000));
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

function range({ high, low }: GateGroup): string {
  if (high === null || low === null) return '';
  return high === low ? ` (${high})` : ` (${high} → ${low})`;
}

export interface GateRow { key: 'above' | 'below' | 'unscored'; label: string }

/** The withheld groups as rows; a group with a count of 0 has no row. */
export function gateRows(g: Gating): GateRow[] {
  const rows: GateRow[] = [];
  if (g.above_cutoff.count > 0) rows.push({ key: 'above', label: `${g.above_cutoff.count} more above cutoff${range(g.above_cutoff)}` });
  if (g.below_cutoff.count > 0) rows.push({ key: 'below', label: `${g.below_cutoff.count} below cutoff${range(g.below_cutoff)}` });
  if (g.unscored.count > 0) rows.push({ key: 'unscored', label: `${g.unscored.count} not yet scored` });
  return rows;
}

export interface BootGroup {
  key: 'error' | 'advisory';
  advisory: boolean;
  role: 'alert' | 'status';
  headline: string;
  entries: StartupFailure[];
}

/** The /health boot report split by severity. A missing severity counts as an
 *  error: every entry except the profile advisory has always been a failure. */
export function bootGroups(failures: StartupFailure[]): BootGroup[] {
  const errors = failures.filter((f) => f.severity !== 'advisory');
  const advisory = failures.filter((f) => f.severity === 'advisory');
  const groups: BootGroup[] = [];
  if (errors.length) {
    groups.push({ key: 'error', advisory: false, role: 'alert', headline: `${errors.length} component(s) failed to start`, entries: errors });
  }
  if (advisory.length) {
    groups.push({
      key: 'advisory', advisory: true, role: 'status',
      headline: advisory.length === 1 ? '1 component is waiting to start' : `${advisory.length} components are waiting to start`,
      entries: advisory,
    });
  }
  return groups;
}

const articles = (n: number) => `article${n === 1 ? '' : 's'}`;

export function feedHeading(g: Gating, shown: number): string {
  return `cutoff ${g.cutoff} · showing ${g.max_displayed} · ${shown} of ${g.total} ${articles(g.total)}`;
}

export function savedHeading(n: number): string {
  return `${n} saved ${articles(n)}`;
}
