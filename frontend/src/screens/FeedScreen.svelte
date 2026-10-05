<script lang="ts">
  import { untrack } from 'svelte';
  import { flip } from 'svelte/animate';
  import { fly } from 'svelte/transition';
  import FeedCard from '../components/FeedCard.svelte';
  import GateRows from '../components/GateRows.svelte';
  import Icon from '../components/Icon.svelte';
  import { feed, loadFor } from '../lib/feed.svelte';
  import { feedHeading, savedHeading } from '../lib/format';
  import { dur } from '../lib/motion';
  import { normalizeCats, sameCats } from '../lib/route';
  import { patchRoute, router } from '../lib/router.svelte';
  import { toastError } from '../lib/toast.svelte';
  import type { Mode } from '../lib/types';

  let { mode }: { mode: Mode } = $props();
  const cats = $derived(router.route.cats);
  const open = $derived(router.route.open);
  const current = $derived(feed.loaded && feed.mode === mode && sameCats(feed.cats, cats));

  const catsKey = $derived(cats.join('\u0000'));
  const showList = $derived(feed.loaded && feed.mode === mode);

  let attempt = 0;
  let failed: string | null = $state(null);

  function load(): void {
    const n = ++attempt;
    failed = null;
    const wantMode = mode;
    const wantCats = untrack(() => cats);
    loadFor(wantMode, wantCats).then(
      (kept) => {
        if (n !== attempt) return;
        failed = null;
        if (kept) patchRoute({ cats: kept, open: null });
      },
      (err) => {
        if (n !== attempt) return;
        failed = err instanceof Error ? err.message : String(err);
        toastError(err);
      },
    );
  }

  // Load when the route names a list other than the one on screen, or the one
  // on screen went stale while you were elsewhere. Only the route's values are
  // tracked (mode and the joined categories): opening a card makes a new `cats`
  // array, and tracking `pending` would defeat the pill.
  $effect(() => {
    const wantMode = mode;
    void catsKey; // track the values, not the array
    const fresh = untrack(() => feed.loaded && feed.mode === wantMode && sameCats(feed.cats, cats)
      && feed.target.mode === wantMode && sameCats(feed.target.cats, cats) && !feed.stale && feed.pending === 0);
    if (!fresh) load();
  });

  // Tidy the URL once this tab's filters have loaded (never before, so a deep
  // link isn't stripped by a race with the load): drop categories the server
  // no longer offers, and an open id that isn't in the list.
  $effect(() => {
    if (!current || !feed.filtersLoaded) return;
    const fixedCats = normalizeCats(cats, feed.filters.map((f) => f.category));
    const fixedOpen = open !== null && !feed.items.some((i) => i.article_id === open) ? null : open;
    if (!sameCats(fixedCats, cats) || fixedOpen !== open) patchRoute({ cats: fixedCats, open: fixedOpen });
  });

  const clear = () => patchRoute({ cats: [], open: null });
</script>

{#if showList}
  <div class="page-head">
    <h2 class="section-title">
      {mode === 'saved' ? savedHeading(feed.items.length) : feedHeading(feed.gating, feed.items.length)}
    </h2>
    <button type="button" class="btn quiet small" onclick={load}><Icon name="refresh" size={16} /> Refresh</button>
  </div>

  {#if feed.items.length === 0}
    {#if mode === 'feed' && feed.cats.length === 0 && feed.gating.total === 0}
      <p class="empty">Nothing yet. Add feeds to <code>sources.yaml</code>, then <code>make poll-now ID=&lt;source-id&gt;</code>.</p>
    {:else if mode === 'feed' && feed.cats.length === 0}
      <p class="empty">Nothing is on display — the rows below say why.</p>
    {:else if mode === 'feed'}
      <div class="empty">
        <span>{feed.cats.length === 1 ? 'No articles match this filter.' : `No articles match all ${feed.cats.length} filters.`}</span>
        <button type="button" class="btn quiet" onclick={clear}>Clear filters</button>
      </div>
    {:else if feed.cats.length === 0}
      <p class="empty">Nothing saved yet. Star an article in the Feed to keep it here.</p>
    {:else}
      <div class="empty">
        <span>No saved articles match this filter.</span>
        <button type="button" class="btn quiet" onclick={clear}>Clear filters</button>
      </div>
    {/if}
  {:else}
    <ul class="list">
      {#each feed.items as item (item.article_id)}
        <li animate:flip={{ duration: dur(220) }} in:fly={{ y: -12, duration: dur(220) }}
          out:fly={{ x: mode === 'saved' ? 80 : 0, duration: dur(180) }}>
          <FeedCard {item} />
        </li>
      {/each}
    </ul>
  {/if}

  {#if mode === 'feed'}<GateRows />{/if}
{:else if failed}
  <div class="center-state"><p>{failed}</p><button type="button" class="btn" onclick={load}>Retry</button></div>
{:else}
  <p class="center-state muted">Loading…</p>
{/if}

<style>
  .list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
</style>
