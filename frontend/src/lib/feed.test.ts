import { describe, expect, it } from 'vitest';
import {
  arrivalEffect, dropRemoved, EMPTY_GATING, mergeInFlight, normalizeGating, ratingRequest, Sequencer, shouldHold,
  swipeLeftResult, toggledRating,
} from './feed';
import type { FeedItem } from './types';

const item = (id: number, over: Partial<FeedItem> = {}): FeedItem => ({
  article_id: id, headline: `h${id}`, bullets: [], url: 'https://x', published_at: null,
  source: { id: 's', name: 'S', type: 'substack' }, score: null, rating: null, badges: [], categories: [], saved: false,
  ...over,
});

describe('Sequencer', () => {
  it('only the newest request is current', () => {
    const s = new Sequencer();
    const a = s.next();
    const b = s.next();
    expect(s.isCurrent(a)).toBe(false);
    expect(s.isCurrent(b)).toBe(true);
  });
});

describe('normalizeGating', () => {
  it('fills missing groups and fields', () => {
    expect(normalizeGating({ cutoff: 40, unscored: { count: 2 } as never })).toEqual({
      ...EMPTY_GATING, cutoff: 40, unscored: { count: 2, high: null, low: null },
    });
  });
  it('handles null', () => expect(normalizeGating(null)).toEqual(EMPTY_GATING));
});

describe('mergeInFlight', () => {
  it('keeps local saved/rating for articles with a write in flight', () => {
    const fresh = [item(1, { saved: false, rating: null, score: 70 }), item(2, { saved: true })];
    const current = [item(1, { saved: true, rating: -1 }), item(2, { saved: false })];
    const merged = mergeInFlight(fresh, current, new Map([[1, 1]]));
    expect(merged[0]).toMatchObject({ saved: true, rating: -1, score: 70 });
    expect(merged[1]).toMatchObject({ saved: true });
  });
  it('treats a zero counter as not in flight', () => {
    const fresh = [item(1, { saved: false })];
    expect(mergeInFlight(fresh, [item(1, { saved: true })], new Map([[1, 0]]))[0].saved).toBe(false);
  });
  it('is the fresh list when nothing is in flight', () => {
    const fresh = [item(1)];
    expect(mergeInFlight(fresh, [], new Map())).toBe(fresh);
  });
});

describe('dropRemoved', () => {
  const fresh = [item(1), item(2)];
  it('drops a card whose unsave is still in flight', () =>
    expect(dropRemoved(fresh, 9, new Map([[1, Infinity]])).map((i) => i.article_id)).toEqual([2]));
  it('drops a card from a list requested before the unsave settled', () =>
    expect(dropRemoved(fresh, 5, new Map([[1, 5]])).map((i) => i.article_id)).toEqual([2]));
  it('trusts a list requested after the unsave settled', () =>
    expect(dropRemoved(fresh, 6, new Map([[1, 5]])).map((i) => i.article_id)).toEqual([1, 2]));
  it('is the fresh list when nothing was removed', () => expect(dropRemoved(fresh, 1, new Map())).toBe(fresh));
});

describe('shouldHold', () => {
  it('applies at the top with nothing open', () => expect(shouldHold({ scrollY: 10, openId: null })).toBe(false));
  it('holds when scrolled', () => expect(shouldHold({ scrollY: 80, openId: null })).toBe(true));
  it('holds when a card is open', () => expect(shouldHold({ scrollY: 0, openId: 3 })).toBe(true));
});

describe('arrivalEffect', () => {
  it('only refreshes counts on Saved', () => expect(arrivalEffect({ mode: 'saved', visible: true, counted: true, hold: false })).toBe('counts'));
  it('marks stale when Saved is not on screen', () => expect(arrivalEffect({ mode: 'saved', visible: false, counted: true, hold: false })).toBe('stale'));
  it('marks stale when the list is not on screen', () => expect(arrivalEffect({ mode: 'feed', visible: false, counted: true, hold: false })).toBe('stale'));
  it('reloads when not holding', () => expect(arrivalEffect({ mode: 'feed', visible: true, counted: true, hold: false })).toBe('reload'));
  it('counts a held summary', () => expect(arrivalEffect({ mode: 'feed', visible: true, counted: true, hold: true })).toBe('pending'));
  it('marks a held score stale without counting', () => expect(arrivalEffect({ mode: 'feed', visible: true, counted: false, hold: true })).toBe('stale'));
});

describe('ratings', () => {
  it('toggles the same value off', () => {
    expect(toggledRating(1, 1)).toBeNull();
    expect(toggledRating(-1, 1)).toBe(1);
    expect(toggledRating(null, -1)).toBe(-1);
  });
  it('swipe left rates down, or clears an existing rate-down', () => {
    expect(swipeLeftResult(null)).toEqual({ next: -1, label: 'Rated down' });
    expect(swipeLeftResult(1)).toEqual({ next: -1, label: 'Rated down' });
    expect(swipeLeftResult(-1)).toEqual({ next: null, label: 'Rating cleared' });
  });
  it('maps a rating to its request', () => {
    expect(ratingRequest(null)).toEqual({ method: 'DELETE' });
    expect(ratingRequest(1)).toEqual({ method: 'PUT', value: 1 });
  });
});
