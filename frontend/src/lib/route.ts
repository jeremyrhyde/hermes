// Hash routes: '#/feed?cat=AI,Policy&open=123'. Everything after '#' stays in
// the browser; the server always serves the same index.html at /.
//
// Category names are encodeURIComponent'd individually and joined with a raw
// comma, so a name containing a comma survives and the URL stays readable.

export type Tab = 'feed' | 'saved' | 'sources' | 'settings';
export const TABS: readonly Tab[] = ['feed', 'saved', 'sources', 'settings'];

export interface Route {
  tab: Tab;
  /** Selected category filters (Feed and Saved only). */
  cats: string[];
  /** The expanded article (Feed and Saved only). */
  open: number | null;
}

export const DEFAULT_ROUTE: Route = { tab: 'feed', cats: [], open: null };

export const isListTab = (tab: Tab): tab is 'feed' | 'saved' => tab === 'feed' || tab === 'saved';

function params(qs: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const part of qs.split('&')) {
    if (!part) continue;
    const i = part.indexOf('=');
    const key = i < 0 ? part : part.slice(0, i);
    if (!out.has(key)) out.set(key, i < 0 ? '' : part.slice(i + 1));
  }
  return out;
}

function decode(s: string): string | null {
  try {
    return decodeURIComponent(s);
  } catch {
    return null;
  }
}

/** The route a hash names, or null when it names nothing (redirect to the feed). */
export function parseHash(hash: string): Route | null {
  const raw = hash.replace(/^#\/?/, '');
  const q = raw.indexOf('?');
  const path = (q < 0 ? raw : raw.slice(0, q)).split('/').filter(Boolean);
  const qs = q < 0 ? '' : raw.slice(q + 1);
  if (path.length === 0) return { ...DEFAULT_ROUTE, cats: [] };
  if (path.length !== 1 || !(TABS as readonly string[]).includes(path[0])) return null;
  const tab = path[0] as Tab;
  if (!isListTab(tab)) return { tab, cats: [], open: null };

  const p = params(qs);
  const cats: string[] = [];
  for (const piece of (p.get('cat') ?? '').split(',')) {
    const name = piece ? decode(piece) : null;
    if (name && !cats.includes(name)) cats.push(name);
  }
  const openRaw = p.get('open') ?? '';
  const open = /^\d+$/.test(openRaw) ? Number(openRaw) : null;
  return { tab, cats, open };
}

export function buildHash(route: Route): string {
  const parts: string[] = [];
  if (isListTab(route.tab)) {
    if (route.cats.length) parts.push(`cat=${route.cats.map(encodeURIComponent).join(',')}`);
    if (route.open !== null) parts.push(`open=${route.open}`);
  }
  return `#/${route.tab}${parts.length ? `?${parts.join('&')}` : ''}`;
}

/** The hash to replace the current one with, or null if it is already canonical. */
export function canonicalHash(hash: string): string | null {
  const route = parseHash(hash);
  const canonical = buildHash(route ?? DEFAULT_ROUTE);
  return canonical === hash ? null : canonical;
}

/** `selected` in the server's display order, minus names it no longer offers. */
export function normalizeCats(selected: string[], order: string[]): string[] {
  return order.filter((name) => selected.includes(name));
}

export function sameCats(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((name, i) => name === b[i]);
}
