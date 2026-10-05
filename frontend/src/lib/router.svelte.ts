// History-aware hash router. Each navigation pushes a history entry carrying
// its index, so popstate can tell back from forward (for the slide direction).
// Screen changes run inside document.startViewTransition where supported and
// just swap otherwise. Filter and expand changes replace without animating.

import { tick } from 'svelte';
import { reducedMotion } from './motion';
import { buildHash, canonicalHash, DEFAULT_ROUTE, parseHash, type Route } from './route';

const fallback = (): Route => ({ ...DEFAULT_ROUTE, cats: [] });

export const router: { route: Route } = $state({ route: parseHash(location.hash) ?? fallback() });

let index = 0;

type ViewTransitionDoc = Document & { startViewTransition?: (cb: () => Promise<void>) => unknown };

function current(): Route {
  return parseHash(location.hash) ?? fallback();
}

function apply(route: Route, direction: 'forward' | 'back', animate: boolean): void {
  const doc = document as ViewTransitionDoc;
  if (!animate || reducedMotion() || !doc.startViewTransition) {
    router.route = route;
    return;
  }
  document.documentElement.dataset.nav = direction;
  doc.startViewTransition(async () => {
    router.route = route;
    await tick();
  });
}

// Safari's edge-swipe back/forward gesture sets this on the PopStateEvent it
// dispatches, since it already animated the transition itself.
type SwipePopStateEvent = PopStateEvent & { hasUAVisualTransition?: boolean };

export function initRouter(): () => void {
  // A reload keeps the tab's history.state, so an existing numeric idx means
  // we're re-entering an existing entry — leave it so back/forward still work.
  const existingIdx = typeof history.state?.idx === 'number' ? (history.state.idx as number) : null;
  index = existingIdx ?? 0;
  const fix = canonicalHash(location.hash);
  if (fix !== null) history.replaceState({ idx: index }, '', fix);
  else if (existingIdx === null) history.replaceState({ idx: index }, '');
  router.route = current();

  const onPopState = (event: SwipePopStateEvent) => {
    const idx = typeof event.state?.idx === 'number' ? (event.state.idx as number) : index + 1;
    const direction = idx < index ? 'back' : 'forward';
    index = idx;
    // A hand-edited hash arrives here too: tidy it in place.
    const fixed = canonicalHash(location.hash);
    if (fixed !== null) history.replaceState({ idx: index }, '', fixed);
    const swiped = 'hasUAVisualTransition' in event && event.hasUAVisualTransition === true;
    const next = current();
    apply(next, direction, !swiped && next.tab !== router.route.tab);
  };
  addEventListener('popstate', onPopState);
  return () => removeEventListener('popstate', onPopState);
}

/** Go somewhere. Tab changes push and slide; `replace` rewrites the entry in place. */
export function navigate(route: Route, opts: { replace?: boolean } = {}): void {
  const hash = buildHash(route);
  if (hash === location.hash) return;
  if (opts.replace) {
    history.replaceState({ idx: index }, '', hash);
  } else {
    index += 1;
    history.pushState({ idx: index }, '', hash);
  }
  apply(current(), 'forward', !opts.replace && route.tab !== router.route.tab);
}

/** Change filters or the open card: same tab, same history entry, no slide. */
export function patchRoute(patch: Partial<Pick<Route, 'cats' | 'open'>>): void {
  navigate({ ...router.route, ...patch }, { replace: true });
}
