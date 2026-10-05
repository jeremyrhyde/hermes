import { describe, expect, it } from 'vitest';
import { bootGroups, feedHeading, gateRows, relTime, savedHeading } from './format';

const NOW = Date.parse('2026-10-04T12:00:00Z');
const g = (count: number, high: number | null = null, low: number | null = null) => ({ count, high, low });

describe('relTime', () => {
  it('is blank without a date', () => expect(relTime(null, NOW)).toBe(''));
  it('uses minutes under an hour', () => expect(relTime('2026-10-04T11:35:00Z', NOW)).toBe('25m ago'));
  it('uses hours under a day', () => expect(relTime('2026-10-04T09:00:00Z', NOW)).toBe('3h ago'));
  it('uses days after that', () => expect(relTime('2026-10-01T12:00:00Z', NOW)).toBe('3d ago'));
  it('never goes negative for a clock-skewed future date', () => expect(relTime('2026-10-04T12:05:00Z', NOW)).toBe('0m ago'));
});

describe('gateRows', () => {
  it('omits empty groups and formats ranges', () => {
    expect(gateRows({ cutoff: 60, max_displayed: 20, total: 40, above_cutoff: g(3, 80, 61), below_cutoff: g(1, 42, 42), unscored: g(0) }))
      .toEqual([
        { key: 'above', label: '3 more above cutoff (80 → 61)' },
        { key: 'below', label: '1 below cutoff (42)' },
      ]);
  });
  it('labels unscored without a range', () => {
    expect(gateRows({ cutoff: 0, max_displayed: 50, total: 2, above_cutoff: g(0), below_cutoff: g(0), unscored: g(2) }))
      .toEqual([{ key: 'unscored', label: '2 not yet scored' }]);
  });
});

describe('bootGroups', () => {
  it('splits errors from advisories; a missing severity is an error', () => {
    const groups = bootGroups([
      { component: 'poller', error: 'no key' },
      { component: 'scorer', error: 'waiting', severity: 'advisory' },
      { component: 'db', error: 'x', severity: 'error' },
    ]);
    expect(groups.map((x) => [x.key, x.role, x.headline, x.entries.length])).toEqual([
      ['error', 'alert', '2 component(s) failed to start', 2],
      ['advisory', 'status', '1 component is waiting to start', 1],
    ]);
  });
  it('pluralizes advisories', () => {
    const groups = bootGroups([{ component: 'a', error: '', severity: 'advisory' }, { component: 'b', error: '', severity: 'advisory' }]);
    expect(groups[0].headline).toBe('2 components are waiting to start');
  });
});

describe('headings', () => {
  it('describes the gate', () =>
    expect(feedHeading({ cutoff: 60, max_displayed: 20, total: 1, above_cutoff: g(0), below_cutoff: g(0), unscored: g(0) }, 1))
      .toBe('cutoff 60 · showing 20 · 1 of 1 article'));
  it('counts saved articles', () => {
    expect(savedHeading(1)).toBe('1 saved article');
    expect(savedHeading(4)).toBe('4 saved articles');
  });
});
