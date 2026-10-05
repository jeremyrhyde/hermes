import type { KnobKey, Profile, ReviewState } from './types';

export interface Knob { key: KnobKey; id: string; label: string; min: number; max: number; unit: string; help: string }

/** One descriptor per knob; the Knobs component is a straight render of this.
 *  Bounds mirror `_PREFERENCES` in core/api.py. */
export const KNOBS: readonly Knob[] = [
  {
    key: 'score_cutoff', id: 'knob-cutoff', label: 'Score cutoff', min: 0, max: 100, unit: '',
    help: 'Articles scoring below this are withheld from the feed and collapsed into a count. '
      + 'Leave it at 0, which shows everything, until the scores look right.',
  },
  {
    key: 'max_displayed', id: 'knob-max', label: 'Most articles shown', min: 1, max: 200, unit: '',
    help: 'A hard cap on the list, whatever the cutoff allows. Anything past it is counted as overflow rather than dropped.',
  },
  {
    key: 'distill_threshold', id: 'knob-threshold', label: 'Re-evaluate every', min: 5, max: 200, unit: ' ratings',
    help: 'How many ratings to gather before Hermes offers to revise your taste profile. '
      + 'Clearing a rating takes it back out of the count, so the tally below can fall as well as rise.',
  },
];

/** The value to PUT for a slider release, or null for blank, junk or a no-op. */
export function knobCommit(raw: string, effective: number): number | null {
  const trimmed = raw.trim();
  const value = Number(trimmed);
  if (trimmed === '' || !Number.isInteger(value) || value === effective) return null;
  return value;
}

/** Exactly one panel, always — an unknown state reads as `insufficient`. */
export function reviewPanel(state: string): ReviewState {
  return state === 'ready' || state === 'pending' ? state : 'insufficient';
}

export function profileLabel(p: Profile): string {
  if (!p.version) return 'none yet';
  return p.kind ? `${p.version} · ${p.kind}` : p.version;
}

const NEXT_POLL = "Scoring uses it from each source's next poll — scores already on screen do not move until then.";

export function saveNotice(version: string | undefined): string {
  return version ? `Saved as ${version}. ${NEXT_POLL}` : 'Saved.';
}

export function approveNotice(version: string, rescore: boolean, rescored: number): string {
  let notice = `Approved as ${version}. ${NEXT_POLL}`;
  if (rescore && rescored > 0) {
    notice += ` ${rescored} article(s) queued for re-scoring; the pipeline works through 50 per source per poll, `
      + 'so the feed ranks old and new scores against each other until it catches up.';
  } else if (rescore) {
    notice += ' Nothing had a score to clear.';
  }
  return notice;
}

export const REJECT_NOTICE = 'Proposal discarded. Your profile is unchanged, and the rating count starts again from here.';
export const EMPTY_PROFILE_ERROR = 'A profile cannot be empty — scoring would judge every article against nothing.';
export const EMPTY_APPROVAL_ERROR = 'A profile cannot be empty — clear the edit or reject the proposal instead.';
export const STALE_PROPOSAL_ERROR = 'That proposal was already approved or rejected. The panel below is now current.';
export const ALREADY_PENDING_ERROR = 'A proposal was already waiting — the panel below is up to date.';
