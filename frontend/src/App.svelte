<script lang="ts">
  import { onMount } from 'svelte';
  import BootBanner from './components/BootBanner.svelte';
  import CategoryChips from './components/CategoryChips.svelte';
  import FatalScreen from './components/FatalScreen.svelte';
  import Header from './components/Header.svelte';
  import NavPill from './components/NavPill.svelte';
  import NewPill from './components/NewPill.svelte';
  import Toasts from './components/Toast.svelte';
  import { app, boot, loadHealth, loadSources, refreshAll } from './lib/app.svelte';
  import { arrivalEffect, shouldHold } from './lib/feed';
  import { feed, noteArrival, refreshCounts, reloadFeed } from './lib/feed.svelte';
  import { live } from './lib/live.svelte';
  import { initPrefs } from './lib/prefs.svelte';
  import { isListTab } from './lib/route';
  import { initRouter, navigate, router } from './lib/router.svelte';
  import { refreshSettings } from './lib/settings.svelte';
  import { toast } from './lib/toast.svelte';
  import FeedScreen from './screens/FeedScreen.svelte';
  import SourcesScreen from './screens/SourcesScreen.svelte';
  import SettingsScreen from './screens/settings/SettingsScreen.svelte';

  let booting = false;
  async function start(): Promise<void> {
    if (booting) return;
    booting = true;
    try {
      await boot(router.route);
    } finally {
      booting = false;
    }
  }

  function onArticle(counted: boolean): void {
    noteArrival(arrivalEffect({
      mode: feed.mode,
      visible: isListTab(router.route.tab),
      counted,
      hold: shouldHold({ scrollY: window.scrollY, openId: router.route.open }),
    }));
  }

  onMount(() => {
    const stopPrefs = initPrefs();
    const stopRouter = initRouter();
    void start();

    const offs = [
      live.on('article_summarized', () => onArticle(true)),
      live.on('article_scored', () => onArticle(false)),
      live.on('article_ingested', () => {}), // no card until it is summarized
      live.on('source_polled', () => {
        loadSources().catch((err) => console.error('sources refresh', err));
        if (isListTab(router.route.tab)) void refreshCounts();
      }),
      live.on('profile_proposed', () => {
        if (router.route.tab === 'settings') void refreshSettings();
        toast('New profile proposal ready', 'info', {
          action: { label: 'Review', run: () => navigate({ tab: 'settings', cats: [], open: null }) },
        });
      }),
      // Before boot has succeeded there is nothing to refresh — boot instead.
      live.on('system_ready', () => void (app.ready ? refreshAll(router.route) : start())),
      live.on('pipeline_error', (e) => console.warn('pipeline_error', e.data)),
      live.onReconnect(() => void (app.ready ? refreshAll(router.route) : start())),
    ];
    live.connect();

    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      live.retryNow();
      if (!app.ready) return;
      loadHealth().catch(() => {});
      loadSources().catch(() => {});
      if (isListTab(router.route.tab) && (feed.stale || feed.pending > 0)) void reloadFeed();
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      for (const off of offs) off();
      live.disconnect();
      stopRouter();
      stopPrefs();
    };
  });

  const tab = $derived(router.route.tab);
  const listTab = $derived(isListTab(tab));
</script>

{#if app.ready && listTab}
  <Header><CategoryChips /></Header>
{:else}
  <Header />
{/if}

<main class="screen">
  {#if !app.ready}
    {#if app.error}
      <FatalScreen message={app.error} onRetry={() => void start()} />
    {:else}
      <p class="center-state muted">Loading…</p>
    {/if}
  {:else}
    <BootBanner />
    {#key tab}
      {#if tab === 'feed' || tab === 'saved'}
        <FeedScreen mode={tab} />
      {:else if tab === 'sources'}
        <SourcesScreen />
      {:else}
        <SettingsScreen />
      {/if}
    {/key}
  {/if}
</main>

{#if app.ready && listTab}<NewPill />{/if}
<NavPill />
<Toasts lifted={listTab && feed.pending > 0} />
