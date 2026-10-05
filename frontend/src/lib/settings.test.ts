import { describe, expect, it } from 'vitest';
import { approveNotice, KNOBS, knobCommit, profileLabel, reviewPanel } from './settings';

describe('KNOBS', () => {
  it('describes the three server knobs with their server-side bounds', () =>
    expect(KNOBS.map((k) => [k.key, k.min, k.max])).toEqual([
      ['score_cutoff', 0, 100], ['max_displayed', 1, 200], ['distill_threshold', 5, 200],
    ]));
});

describe('knobCommit', () => {
  it('skips a no-op and junk', () => {
    expect(knobCommit('40', 40)).toBeNull();
    expect(knobCommit('', 40)).toBeNull();
    expect(knobCommit('4.5', 40)).toBeNull();
  });
  it('returns the integer to write', () => expect(knobCommit('55', 40)).toBe(55));
});

describe('reviewPanel', () => {
  it('passes known states through', () => {
    expect(reviewPanel('ready')).toBe('ready');
    expect(reviewPanel('pending')).toBe('pending');
  });
  it('falls back to insufficient for anything else', () => expect(reviewPanel('weird')).toBe('insufficient'));
});

describe('profileLabel', () => {
  it('says none yet without a version', () => expect(profileLabel({ version: null, body: '', kind: null })).toBe('none yet'));
  it('adds the kind', () => expect(profileLabel({ version: 'profile-v2', body: 'x', kind: 'distilled' })).toBe('profile-v2 · distilled'));
});

describe('approveNotice', () => {
  it('mentions re-scoring when it happened', () =>
    expect(approveNotice('profile-v3', true, 12)).toContain('12 article(s) queued for re-scoring'));
  it('says when nothing had a score', () => expect(approveNotice('profile-v3', true, 0)).toContain('Nothing had a score to clear.'));
  it('is plain without re-scoring', () =>
    expect(approveNotice('profile-v3', false, 0)).toBe(
      "Approved as profile-v3. Scoring uses it from each source's next poll — scores already on screen do not move until then."));
});
