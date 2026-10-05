<script lang="ts">
  import { feed } from '../lib/feed.svelte';
  import { patchRoute, router } from '../lib/router.svelte';

  const selected = $derived(router.route.cats);
  // Shown only when the API offers filters. On Saved, also hidden when nothing
  // is saved at all: every count would be 0, and a fully disabled row above
  // "Nothing saved yet" reads as broken rather than empty.
  const show = $derived(feed.filters.length > 0 && (router.route.tab !== 'saved' || feed.hasAnySaved));

  function toggle(name: string): void {
    const next = selected.includes(name) ? selected.filter((c) => c !== name) : [...selected, name];
    const order = feed.filters.map((f) => f.category);
    patchRoute({ cats: order.filter((c) => next.includes(c)), open: null });
  }
</script>

{#if show}
  <div class="row">
    <div class="scroll" role="group" aria-label="Filter by category">
      {#each feed.filters as f (f.category)}
        {@const on = selected.includes(f.category)}
        <!-- Zero-count filters are disabled, never hidden: a chip vanishing
             mid-interaction reads as a bug. A selected one stays clickable so
             the selection is always reversible. -->
        <button type="button" class="chip" class:on aria-pressed={on}
          disabled={!on && f.count === 0} onclick={() => toggle(f.category)}>{f.category} ({f.count})</button>
      {/each}
    </div>
    <!-- Outside the scroller: clear stays reachable however far you scrolled. -->
    {#if selected.length > 0}
      <button type="button" class="chip" onclick={() => patchRoute({ cats: [], open: null })}>clear</button>
    {/if}
  </div>
{/if}

<style>
  .row { display: flex; align-items: center; gap: var(--space-2); }
  .scroll { flex: 1; display: flex; gap: var(--space-2); overflow-x: auto; scrollbar-width: none; padding: 2px 0; }
  .scroll::-webkit-scrollbar { display: none; }
  .chip {
    flex: none; min-height: var(--tap-min); padding: 0 var(--space-3); white-space: nowrap;
    border: 1px solid var(--color-border); border-radius: var(--radius-pill);
    background: var(--color-surface); color: var(--color-text-muted); font-size: var(--text-sm); cursor: pointer;
    transition: background var(--transition-fast), color var(--transition-fast), border-color var(--transition-fast);
  }
  .chip.on { background: var(--color-accent-soft); border-color: var(--color-accent); color: var(--color-text); }
  .chip:disabled { opacity: 0.45; cursor: default; }
</style>
