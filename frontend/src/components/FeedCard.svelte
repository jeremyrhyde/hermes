<script lang="ts">
  import { slide } from 'svelte/transition';
  import Icon from './Icon.svelte';
  import { logInteraction, rate, swipeLeft, toggleSaved } from '../lib/feed.svelte';
  import { relTime } from '../lib/format';
  import { dur } from '../lib/motion';
  import { patchRoute, router } from '../lib/router.svelte';
  import { swipe } from '../lib/swipe';
  import type { FeedItem } from '../lib/types';

  let { item }: { item: FeedItem } = $props();
  const open = $derived(router.route.open === item.article_id);

  function toggleOpen(): void {
    const opening = !open;
    patchRoute({ open: opening ? item.article_id : null });
    if (opening) logInteraction(item.article_id, 'expand');
  }
</script>

<div class="wrap" use:swipe={{ onRight: () => void toggleSaved(item, { undoToast: true }), onLeft: () => void swipeLeft(item) }}>
  <div class="under right" aria-hidden="true"><Icon name="star" size={20} /> {item.saved ? 'Unsave' : 'Save'}</div>
  <div class="under left" aria-hidden="true">{item.rating === -1 ? 'Clear rating' : 'Rate down'} <Icon name="thumb-down" size={20} /></div>

  <article class="card face" data-swipe-face>
    <!-- The star is a sibling of the head button, never a child: a button
         inside a button is invalid and browsers unnest it. -->
    <div class="row">
      <button type="button" class="head" aria-expanded={open} onclick={toggleOpen}>
        {#if item.score !== null}<span class="score">{item.score}</span>{/if}
        <span class="main">
          <span class="headline">{item.headline}</span>
          <span class="meta"><span>{item.source.name}</span><span>{relTime(item.published_at)}</span></span>
        </span>
      </button>
      <button type="button" class="icon-btn star" class:on={item.saved} aria-pressed={item.saved}
        aria-label={item.saved ? 'Remove from saved' : 'Save for later'}
        title={item.saved ? 'Remove from saved' : 'Save for later'}
        onclick={() => void toggleSaved(item)}><Icon name="star" size={20} /></button>
    </div>

    {#if open}
      <div class="body" transition:slide={{ duration: dur(180) }}>
        <ul class="bullets">
          {#each item.bullets as b, i (i)}<li>{b}</li>{/each}
        </ul>
        <!-- Rating lives here, not in the header: you rate after reading. -->
        <div class="actions">
          <a class="btn" href={item.url} target="_blank" rel="noopener"
            onclick={() => logInteraction(item.article_id, 'click_through')}>
            Read the full article <Icon name="external-link" size={16} />
          </a>
          <div class="rating" role="group" aria-label="Rate this article">
            <button type="button" class="icon-btn up" class:on={item.rating === 1} aria-pressed={item.rating === 1}
              aria-label="Rate up" onclick={() => void rate(item, 1)}><Icon name="thumb-up" size={20} /></button>
            <button type="button" class="icon-btn down" class:on={item.rating === -1} aria-pressed={item.rating === -1}
              aria-label="Rate down" onclick={() => void rate(item, -1)}><Icon name="thumb-down" size={20} /></button>
          </div>
        </div>
      </div>
    {/if}
  </article>
</div>

<style>
  .wrap { position: relative; touch-action: pan-y; }
  .under {
    position: absolute; inset: 0; display: none; align-items: center; gap: var(--space-2);
    padding: 0 var(--space-6); border-radius: var(--radius-lg); font-size: var(--text-sm); font-weight: 600;
  }
  .under.right { justify-content: flex-start; background: var(--color-accent-soft); color: var(--color-accent); }
  .under.left { justify-content: flex-end; background: var(--color-danger-soft); color: var(--color-danger); }
  /* data-swipe is set by the swipe action at runtime, so the compiler can't see it. */
  .wrap:global([data-swipe='right']) .under.right, .wrap:global([data-swipe='left']) .under.left { display: flex; }

  .face { position: relative; }
  .row { display: flex; align-items: flex-start; }
  .head {
    flex: 1; display: flex; align-items: flex-start; gap: var(--space-3); min-height: var(--tap-min);
    padding: var(--space-3) 0 var(--space-3) var(--space-4); border: 0; background: none; text-align: left; cursor: pointer;
  }
  .score {
    flex: none; min-width: 2.25rem; padding: 2px var(--space-2); border-radius: var(--radius-sm);
    background: var(--color-accent-soft); color: var(--color-accent);
    font-size: var(--text-sm); font-weight: 600; font-variant-numeric: tabular-nums; text-align: center;
  }
  .main { display: grid; gap: 2px; }
  .headline { font-weight: 600; line-height: 1.35; }
  .meta { display: flex; gap: var(--space-2); font-size: var(--text-sm); color: var(--color-text-muted); }
  .star { margin: var(--space-1); }
  .star.on { color: var(--color-star); }
  .star.on :global(svg) { fill: currentColor; }
  .body { display: grid; gap: var(--space-3); padding: 0 var(--space-4) var(--space-4); }
  .bullets { display: grid; gap: var(--space-2); margin: 0; padding-left: var(--space-6); }
  .actions { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); flex-wrap: wrap; }
  .rating { display: flex; gap: var(--space-1); }
  .up.on { color: var(--color-accent); }
  .down.on { color: var(--color-danger); }
</style>
