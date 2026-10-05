<script lang="ts">
  import { feed } from '../lib/feed.svelte';
  import { gateRows } from '../lib/format';

  // Count-only and plain text: the server sends sizes and score ranges, not
  // articles, so there is nothing behind these rows to open.
  const rows = $derived(gateRows(feed.gating));
</script>

{#if rows.length}
  <ul class="gate">
    {#each rows as row (row.key)}
      <li><span class="caret" aria-hidden="true">▸</span>{row.label}</li>
    {/each}
  </ul>
{/if}

<style>
  .gate { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-1); color: var(--color-text-muted); font-size: var(--text-sm); }
  li { display: flex; gap: var(--space-2); padding: var(--space-2) var(--space-4); }
  .caret { color: var(--color-text-faint); }
</style>
