<script lang="ts">
  import { fly } from 'svelte/transition';
  import Icon from './Icon.svelte';
  import { feed, reloadFeed } from '../lib/feed.svelte';
  import { dur, reducedMotion } from '../lib/motion';

  function show(): void {
    scrollTo({ top: 0, behavior: reducedMotion() ? 'auto' : 'smooth' });
    void reloadFeed(); // clears pending/stale
  }
</script>

{#if feed.pending > 0}
  <button type="button" class="new" transition:fly={{ y: 16, duration: dur(180) }} onclick={show}>
    <Icon name="arrow-up" size={16} /> {feed.pending} new
  </button>
{/if}

<style>
  .new {
    position: fixed; left: 50%; z-index: 30; transform: translateX(-50%);
    bottom: calc(var(--pill-h) + var(--space-4) + var(--safe-bottom) + var(--space-3));
    display: inline-flex; align-items: center; gap: var(--space-2);
    min-height: var(--tap-min); padding: 0 var(--space-4); border: 0; border-radius: var(--radius-pill);
    background: var(--color-accent); color: var(--color-on-accent); font-weight: 600;
    box-shadow: var(--shadow-2); cursor: pointer;
  }
</style>
