<script lang="ts">
  import { untrack } from 'svelte';
  import { flip } from 'svelte/animate';
  import { fly } from 'svelte/transition';
  import FeedCard from '../components/FeedCard.svelte';
  import GateRows from '../components/GateRows.svelte';
  import Icon from '../components/Icon.svelte';
  import { feed, loadFeed } from '../lib/feed.svelte';
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

  function load(): void {
    loadFeed(mode, cats).catch(toastError);
  }

  // Load when the route names a list other than the one on screen, or the one
  // on screen went stale while you were elsewhere. Only the route is tracked:
  // tracking `pending` would reload on every held arrival and defeat the pill.
  $effect(() => {
    const wanted = { mode, cats };
    const fresh = untrack(() => feed.loaded && feed.mode === wanted.mode && sameCats(feed.cats, wanted.cats)
      && !feed.stale && feed.pending === 0);
    if (!fresh) loadFeed(wanted.mode, wanted.cats).catch(toastError);
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

{#if !current}
  <p class="center-state muted">Loading…</p>
{:else}
  <div class="page-head">
    <h2 class="section-title">
      {mode === 'saved' ? savedHeading(feed.items.length) : feedHeading(feed.gating, feed.items.length)}
    </h2>
    <button type="button" class="btn quiet small" onclick={load}><Icon name="refresh" size={16} /> Refresh</button>
  </div>

  {#if feed.items.length === 0}
    {#if mode === 'feed' && cats.length === 0 && feed.gating.total === 0}
      <p class="empty">Nothing yet. Add feeds to <code>sources.yaml</code>, then <code>make poll-now ID=&lt;source-id&gt;</code>.</p>
    {:else if mode === 'feed' && cats.length === 0}
      <p class="empty">Nothing is on display — the rows below say why.</p>
    {:else if mode === 'feed'}
      <div class="empty">
        <span>{cats.length === 1 ? 'No articles match this filter.' : `No articles match all ${cats.length} filters.`}</span>
        <button type="button" class="btn quiet" onclick={clear}>Clear filters</button>
      </div>
    {:else if cats.length === 0}
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
{/if}

<style>
  .list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
</style>
