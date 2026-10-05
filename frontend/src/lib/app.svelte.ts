import { api, ApiError } from './api';
import { feed, loadFeed, loadFor } from './feed.svelte';
import { isListTab, type Route } from './route';
import { patchRoute } from './router.svelte';
import { refreshSettings } from './settings.svelte';
import { toastError } from './toast.svelte';
import type { Mode, Source, StartupFailure } from './types';

interface AppState {
  ready: boolean;
  /** Set when the first load fails; the FatalScreen shows it and retries. */
  error: string | null;
  failures: StartupFailure[];
  sources: Source[];
}

export const app: AppState = $state({ ready: false, error: null, failures: [], sources: [] });

export async function loadHealth(): Promise<void> {
  app.failures = (await api.health())?.startup_failures ?? [];
}

export async function loadSources(): Promise<void> {
  app.sources = (await api.sources()) ?? [];
}

/** Boot's list load; categories the server rejects are dropped from the route and the list loads again. */
async function bootFeed(mode: Mode, cats: string[]): Promise<void> {
  const kept = await loadFor(mode, cats);
  if (kept) {
    patchRoute({ cats: kept, open: null });
    await loadFeed(mode, kept);
  }
}

/** First load: health, sources and whatever the starting route shows. */
export async function boot(route: Route): Promise<void> {
  try {
    await Promise.all([
      loadHealth(),
      loadSources().catch((err) => console.error('boot: sources', err)),
      isListTab(route.tab) ? bootFeed(route.tab, route.cats) : Promise.resolve(),
      route.tab === 'settings' ? refreshSettings() : Promise.resolve(),
    ]);
    app.error = null;
    app.ready = true;
  } catch (err) {
    app.error = err instanceof ApiError && err.status === 0
      ? "Can't reach the Hermes server."
      : `Can't reach the Hermes server (${(err as Error).message}).`;
  }
}

/** Everything again — system_ready, a socket reconnect, or About → Reload data. */
export async function refreshAll(route: Route): Promise<void> {
  const tasks: Promise<unknown>[] = [loadHealth(), loadSources()];
  if (isListTab(route.tab)) tasks.push(loadFeed(route.tab, route.cats));
  else feed.stale = true;
  if (route.tab === 'settings') tasks.push(refreshSettings());
  const failed = (await Promise.allSettled(tasks)).find((r): r is PromiseRejectedResult => r.status === 'rejected');
  if (failed) toastError(failed.reason);
}
