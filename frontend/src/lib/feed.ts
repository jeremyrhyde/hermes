import type { FeedItem, GateGroup, Gating, Mode, Ranked, RatingValue } from './types';

/** Monotonic request ids: a response for anything but the newest is dropped,
 *  so a slow response can't paint one tab's or filter's data over another's. */
export class Sequencer {
  private n = 0;
  next(): number {
    return ++this.n;
  }
  isCurrent(id: number): boolean {
    return id === this.n;
  }
  get current(): number {
    return this.n;
  }
}

export const EMPTY_GROUP: GateGroup = { count: 0, high: null, low: null };
export const EMPTY_GATING: Gating = {
  cutoff: 0, max_displayed: 50, total: 0,
  above_cutoff: EMPTY_GROUP, below_cutoff: EMPTY_GROUP, unscored: EMPTY_GROUP,
};

const group = (g: Partial<GateGroup> | null | undefined): GateGroup => ({ ...EMPTY_GROUP, ...(g ?? {}) });

/** A missing group or field becomes a zero, never `undefined` in a template. */
export function normalizeGating(r: Partial<Ranked> | null | undefined): Gating {
  return {
    cutoff: r?.cutoff ?? EMPTY_GATING.cutoff,
    max_displayed: r?.max_displayed ?? EMPTY_GATING.max_displayed,
    total: r?.total ?? 0,
    above_cutoff: group(r?.above_cutoff),
    below_cutoff: group(r?.below_cutoff),
    unscored: group(r?.unscored),
  };
}

/** A reload racing a tap must not flip the star back: articles with a write
 *  in flight keep their local saved/rating; the write's own result decides. */
export function mergeInFlight(fresh: FeedItem[], current: FeedItem[], inFlight: ReadonlyMap<number, number>): FeedItem[] {
  if (inFlight.size === 0) return fresh;
  const local = new Map(current.map((i) => [i.article_id, i]));
  return fresh.map((item) => {
    const mine = (inFlight.get(item.article_id) ?? 0) > 0 ? local.get(item.article_id) : undefined;
    return mine ? { ...item, saved: mine.saved, rating: mine.rating } : item;
  });
}

export const HOLD_SCROLL_PX = 80;

/** Hold live arrivals while the reader is down the list or reading a card. */
export function shouldHold(ctx: { scrollY: number; openId: number | null }): boolean {
  return ctx.scrollY >= HOLD_SCROLL_PX || ctx.openId !== null;
}

export type ArrivalEffect = 'counts' | 'stale' | 'reload' | 'pending';

/** What a live article event does. `counted`: a new article (summarized) vs a
 *  re-score; `visible`: a list tab is on screen. */
export function arrivalEffect(a: { mode: Mode; visible: boolean; counted: boolean; hold: boolean }): ArrivalEffect {
  if (!a.visible) return 'stale';
  if (a.mode === 'saved') return 'counts';
  if (!a.hold) return 'reload';
  return a.counted ? 'pending' : 'stale';
}

export function toggledRating(current: RatingValue, value: -1 | 1): RatingValue {
  return current === value ? null : value;
}

export function swipeLeftResult(current: RatingValue): { next: RatingValue; label: string } {
  return current === -1 ? { next: null, label: 'Rating cleared' } : { next: -1, label: 'Rated down' };
}

export function ratingRequest(value: RatingValue): { method: 'DELETE' } | { method: 'PUT'; value: -1 | 1 } {
  return value === null ? { method: 'DELETE' } : { method: 'PUT', value };
}
