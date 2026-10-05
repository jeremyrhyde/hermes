import { api } from './api';
import {
  dropRemoved, EMPTY_GATING, mergeInFlight, normalizeGating, ratingRequest, Sequencer, swipeLeftResult, toggledRating,
  type ArrivalEffect,
} from './feed';
import { toast, toastError } from './toast.svelte';
import type { CategoryFilter, FeedItem, Gating, Mode, Ranked, RatingValue } from './types';

interface FeedState {
  /** Which list `items` holds, and under which filters. */
  mode: Mode;
  cats: string[];
  items: FeedItem[];
  gating: Gating;
  filters: CategoryFilter[];
  /** `filters` came from a successful response for `mode`. */
  filtersLoaded: boolean;
  hasAnySaved: boolean;
  loaded: boolean;
  /** Live arrivals held behind the NewPill. */
  pending: number;
  /** The list is known to be out of date (a held re-score, a knob change, an event while away). */
  stale: boolean;
}

export const feed: FeedState = $state({
  mode: 'feed', cats: [], items: [], gating: EMPTY_GATING, filters: [], filtersLoaded: false,
  hasAnySaved: false, loaded: false, pending: 0, stale: false,
});

const seq = new Sequencer();
const inFlight = new Map<number, number>();
const removedAt = new Map<number, number>();
const begin = (id: number) => inFlight.set(id, (inFlight.get(id) ?? 0) + 1);
const end = (id: number) => {
  const n = (inFlight.get(id) ?? 1) - 1;
  if (n > 0) inFlight.set(id, n);
  else inFlight.delete(id);
};
const find = (id: number) => feed.items.find((i) => i.article_id === id);

/** Load a list with its filter counts. Throws when the list request fails, so
 *  boot can show the FatalScreen; everything after boot uses reloadFeed. */
export async function loadFeed(mode: Mode, cats: string[]): Promise<void> {
  const id = seq.next();
  const [list, categories, anySaved] = await Promise.allSettled([
    mode === 'saved' ? api.saved(cats) : api.ranked(cats),
    api.categories(mode, cats),
    mode === 'saved' ? api.saved([], 1) : Promise.resolve(null),
  ]);
  if (!seq.isCurrent(id)) return;
  if (list.status === 'rejected') throw list.reason;

  feed.mode = mode;
  feed.cats = cats;
  if (mode === 'saved') {
    feed.items = mergeInFlight(dropRemoved((list.value as FeedItem[] | null) ?? [], id, removedAt), feed.items, inFlight);
  } else {
    const ranked = list.value as Ranked | null;
    feed.items = mergeInFlight(ranked?.displayed ?? [], feed.items, inFlight);
    feed.gating = normalizeGating(ranked);
  }
  if (categories.status === 'fulfilled') {
    feed.filters = categories.value?.filters ?? [];
    feed.filtersLoaded = true;
  } else {
    console.error('feed: categories', categories.reason);
    feed.filtersLoaded = false;
  }
  for (const [k, mark] of removedAt) if (id > mark) removedAt.delete(k);
  if (anySaved.status === 'fulfilled' && anySaved.value) feed.hasAnySaved = anySaved.value.length > 0;
  feed.pending = 0;
  feed.stale = false;
  feed.loaded = true;
}

export function reloadFeed(): Promise<void> {
  return loadFeed(feed.mode, feed.cats).catch(toastError);
}

/** Counts only — the filter row after a save or a poll. */
export async function refreshCounts(): Promise<void> {
  const id = seq.current;
  try {
    const c = await api.categories(feed.mode, feed.cats);
    if (seq.isCurrent(id)) feed.filters = c?.filters ?? [];
  } catch (err) {
    console.error('feed: counts', err);
  }
}

export async function setSaved(item: FeedItem, next: boolean): Promise<boolean> {
  const id = item.article_id;
  const prev = item.saved;
  if (prev === next) return true;
  const removing = !next && feed.mode === 'saved';
  const idx = feed.items.findIndex((i) => i.article_id === id);
  item.saved = next;
  if (removing && idx >= 0) feed.items.splice(idx, 1);
  if (removing) removedAt.set(id, Infinity);
  if (next) removedAt.delete(id);
  begin(id);
  try {
    await (next ? api.save(id) : api.unsave(id));
  } catch (err) {
    removedAt.delete(id);
    item.saved = prev;
    const cur = find(id);
    if (cur) cur.saved = prev;
    else if (removing && idx >= 0) feed.items.splice(idx, 0, item);
    toastError(err);
    return false;
  } finally {
    end(id);
  }
  if (removing) removedAt.set(id, seq.current);
  if (next) feed.hasAnySaved = true;
  else if (feed.mode === 'saved' && feed.items.length === 0) feed.hasAnySaved = false;
  if (next && feed.mode === 'saved' && !find(id)) await reloadFeed();
  else void refreshCounts();
  return true;
}

export async function setRating(item: FeedItem, next: RatingValue): Promise<boolean> {
  const id = item.article_id;
  const prev = item.rating;
  if (prev === next) return true;
  item.rating = next;
  begin(id);
  try {
    const req = ratingRequest(next);
    await (req.method === 'DELETE' ? api.clearRating(id) : api.rate(id, req.value));
  } catch (err) {
    item.rating = prev;
    const cur = find(id);
    if (cur) cur.rating = prev;
    toastError(err);
    return false;
  } finally {
    end(id);
  }
  return true;
}

/** The 👍/👎 buttons: the same value again clears it. */
export function rate(item: FeedItem, value: -1 | 1): Promise<boolean> {
  return setRating(item, toggledRating(item.rating, value));
}

/** Star tap or swipe right. Unsaving on Saved removes the card at once, so it
 *  always offers Undo; swipes always do too. */
export async function toggleSaved(item: FeedItem, opts: { undoToast?: boolean } = {}): Promise<void> {
  const id = item.article_id;
  const next = !item.saved;
  if (!(await setSaved(item, next))) return;
  if (!opts.undoToast && !(!next && feed.mode === 'saved')) return;
  toast(next ? 'Saved' : 'Removed from saved', 'info', {
    action: { label: 'Undo', run: () => void setSaved(find(id) ?? item, !next) },
  });
}

/** Swipe left: rate down, or clear an existing rate-down. Undo restores the previous rating. */
export async function swipeLeft(item: FeedItem): Promise<void> {
  const id = item.article_id;
  const prev = item.rating;
  const { next, label } = swipeLeftResult(prev);
  if (!(await setRating(item, next))) return;
  toast(label, 'info', { action: { label: 'Undo', run: () => void setRating(find(id) ?? item, prev) } });
}

export function logInteraction(id: number, kind: 'expand' | 'click_through'): void {
  api.interaction(id, kind).catch((err) => console.debug('interaction', kind, err));
}

export function noteArrival(effect: ArrivalEffect): void {
  if (effect === 'reload') void reloadFeed();
  else if (effect === 'counts') void refreshCounts();
  else if (effect === 'pending') feed.pending += 1;
  else feed.stale = true;
}
