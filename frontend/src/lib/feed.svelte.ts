import { api, ApiError } from './api';
import {
  dropRemoved, EMPTY_GATING, mergeInFlight, normalizeGating, ratingRequest, Sequencer, swipeLeftAction, toggledRating,
  type ArrivalEffect,
} from './feed';
import { normalizeCats, sameCats } from './route';
import { toast, toastError } from './toast.svelte';
import type { CategoryFilter, FeedItem, Gating, Mode, Ranked, RatingValue } from './types';

interface FeedState {
  /** Which list `items` holds, and under which filters. */
  mode: Mode;
  cats: string[];
  /** The most recently requested list; reloads follow this, not the loaded list. */
  target: { mode: Mode; cats: string[] };
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
  mode: 'feed', cats: [], target: { mode: 'feed', cats: [] }, items: [], gating: EMPTY_GATING, filters: [], filtersLoaded: false,
  // frozen: always replace feed.gating, never mutate it
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
 *  callers decide what to do: boot (via loadFor) shows the FatalScreen,
 *  FeedScreen (via loadFor) shows a toast with Retry, reloadFeed toasts. */
export async function loadFeed(mode: Mode, cats: string[]): Promise<void> {
  feed.target = { mode, cats };
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
    feed.items = mergeInFlight(dropRemoved(ranked?.displayed ?? [], id, removedAt), feed.items, inFlight);
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

/** Load the list a route names. If the server rejects its categories (400 for a
 *  name outside the vocabulary), return the subset it still offers so the caller
 *  can rewrite the route; otherwise null. Other failures throw. */
export async function loadFor(mode: Mode, cats: string[]): Promise<string[] | null> {
  try {
    await loadFeed(mode, cats);
    return null;
  } catch (err) {
    if (err instanceof ApiError && err.status === 400 && cats.length > 0) {
      const offered = await api.categories(mode, []).then((c) => c?.filters.map((f) => f.category) ?? [], () => null);
      if (offered) {
        const kept = normalizeCats(cats, offered);
        if (!sameCats(kept, cats)) return kept;
      }
    }
    throw err;
  }
}

export function reloadFeed(): Promise<void> {
  return loadFeed(feed.target.mode, feed.target.cats).catch(toastError);
}

/** Counts only — the filter row after a save or a poll. */
export async function refreshCounts(): Promise<void> {
  const id = seq.current;
  const { mode, cats } = feed;
  try {
    const c = await api.categories(mode, cats);
    if (seq.isCurrent(id) && feed.mode === mode && sameCats(feed.cats, cats)) feed.filters = c?.filters ?? [];
  } catch (err) {
    console.error('feed: counts', err);
  }
}

export async function setSaved(item: FeedItem, next: boolean): Promise<boolean> {
  const id = item.article_id;
  const prev = item.saved;
  if (prev === next) return true;
  // Saved and the feed don't overlap, so either change takes the card off
  // the list you're on: saving moves it out of the feed, unsaving out of Saved.
  const removing = next ? feed.mode !== 'saved' : feed.mode === 'saved';
  const listKey = `${feed.mode}|${feed.cats.join(',')}`;
  const idx = feed.items.findIndex((i) => i.article_id === id);
  const prevMark = removedAt.get(id);
  item.saved = next;
  if (removing && idx >= 0) feed.items.splice(idx, 1);
  if (removing) removedAt.set(id, Infinity);
  else if (next) removedAt.delete(id); // re-saved on Saved: it's back on this list
  begin(id);
  try {
    await (next ? api.save(id) : api.unsave(id));
  } catch (err) {
    if (prevMark !== undefined) removedAt.set(id, prevMark);
    else removedAt.delete(id);
    item.saved = prev;
    const cur = find(id);
    if (cur) cur.saved = prev;
    else if (removing && idx >= 0 && listKey === `${feed.mode}|${feed.cats.join(',')}`) feed.items.splice(idx, 0, item);
    toastError(err);
    return false;
  } finally {
    end(id);
  }
  if (removing) removedAt.set(id, seq.current);
  if (next) feed.hasAnySaved = true;
  else if (feed.mode === 'saved' && feed.cats.length === 0 && feed.items.length === 0) feed.hasAnySaved = false;
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

/** Star tap or swipe right. Either way the card leaves the list it's on
 *  (saving moves it to Saved; unsaving moves it back to the feed), so it
 *  always offers Undo. */
export async function toggleSaved(item: FeedItem): Promise<void> {
  const id = item.article_id;
  const next = !item.saved;
  if (!(await setSaved(item, next))) return;
  toast(next ? 'Moved to Saved' : 'Removed from saved', 'info', {
    action: { label: 'Undo', run: () => void setSaved(find(id) ?? item, !next) },
  });
}

/** Take an article off the feed (``next``) or put it back. Removal is
 *  optimistic, guarded against a reload resurrecting it the same way unsaving
 *  on Saved is; putting it back reloads, so it returns to its ranked place. */
export async function setDismissed(item: FeedItem, next: boolean): Promise<boolean> {
  const id = item.article_id;
  const listKey = `${feed.mode}|${feed.cats.join(',')}`;
  const idx = feed.items.findIndex((i) => i.article_id === id);
  if (next) {
    if (idx >= 0) feed.items.splice(idx, 1);
    removedAt.set(id, Infinity);
  }
  begin(id);
  try {
    await (next ? api.dismiss(id) : api.undismiss(id));
  } catch (err) {
    if (next) {
      removedAt.delete(id);
      if (!find(id) && idx >= 0 && listKey === `${feed.mode}|${feed.cats.join(',')}`) feed.items.splice(idx, 0, item);
    }
    toastError(err);
    return false;
  } finally {
    end(id);
  }
  if (next) {
    removedAt.set(id, seq.current);
    void refreshCounts();
  } else {
    removedAt.delete(id);
    if (!find(id)) await reloadFeed();
  }
  return true;
}

/** Swipe left (or the card's Remove button): off the list you're looking at,
 *  with Undo. See swipeLeftAction. */
export async function swipeLeft(item: FeedItem): Promise<void> {
  const id = item.article_id;
  if (swipeLeftAction(feed.mode).action === 'unsave') {
    if (item.saved) await toggleSaved(item);
    return;
  }
  if (!(await setDismissed(item, true))) return;
  toast('Removed', 'info', { action: { label: 'Undo', run: () => void setDismissed(find(id) ?? item, false) } });
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
