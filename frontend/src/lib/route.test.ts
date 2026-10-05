import { describe, expect, it } from 'vitest';
import { buildHash, canonicalHash, DEFAULT_ROUTE, normalizeCats, parseHash } from './route';

describe('parseHash', () => {
  it('defaults an empty hash to the feed', () => {
    expect(parseHash('')).toEqual(DEFAULT_ROUTE);
    expect(parseHash('#/')).toEqual(DEFAULT_ROUTE);
  });
  it('reads tab, categories and the open article', () => {
    expect(parseHash('#/feed?cat=AI,Policy&open=123')).toEqual({ tab: 'feed', cats: ['AI', 'Policy'], open: 123 });
  });
  it('decodes category names, including ones with commas', () => {
    expect(parseHash('#/saved?cat=Science%2C%20Tech,AI')).toEqual({ tab: 'saved', cats: ['Science, Tech', 'AI'], open: null });
  });
  it('drops a non-numeric open id and duplicate categories', () => {
    expect(parseHash('#/feed?cat=AI,AI&open=abc')).toEqual({ tab: 'feed', cats: ['AI'], open: null });
  });
  it('ignores list state on non-list tabs', () => {
    expect(parseHash('#/settings?cat=AI&open=4')).toEqual({ tab: 'settings', cats: [], open: null });
  });
  it('rejects unknown tabs and extra segments', () => {
    expect(parseHash('#/nope')).toBeNull();
    expect(parseHash('#/feed/12')).toBeNull();
  });
});

describe('buildHash', () => {
  it('round-trips', () => {
    const r = { tab: 'feed' as const, cats: ['Science, Tech', 'AI'], open: 9 };
    expect(parseHash(buildHash(r))).toEqual(r);
  });
  it('omits empty state', () => expect(buildHash({ tab: 'sources', cats: [], open: null })).toBe('#/sources'));
});

describe('canonicalHash', () => {
  it('returns null when the hash is already canonical', () => expect(canonicalHash('#/feed?cat=AI')).toBeNull());
  it('returns the cleaned hash for a fixable route', () => expect(canonicalHash('#/feed?open=x')).toBe('#/feed'));
  it('redirects garbage to the feed', () => expect(canonicalHash('#/workouts')).toBe('#/feed'));
  it('fills an empty hash', () => expect(canonicalHash('')).toBe('#/feed'));
});

describe('normalizeCats', () => {
  it('orders by the server and drops unknown names', () =>
    expect(normalizeCats(['Policy', 'Gone', 'AI'], ['AI', 'Science', 'Policy'])).toEqual(['AI', 'Policy']));
});
