<script lang="ts">
  import Icon from './Icon.svelte';
  import { app } from '../lib/app.svelte';
  import { bootGroups } from '../lib/format';

  // Two groups, each with its own count, role and colour: "scoring is waiting
  // for a taste profile" is where a fresh install starts, not a failure.
  const groups = $derived(bootGroups(app.failures));
</script>

{#each groups as group (group.key)}
  <div class="banner" class:advisory={group.advisory} role={group.role}>
    <Icon name="alert" size={18} />
    <div>
      <strong>{group.headline}</strong>
      <ul>
        {#each group.entries as f, i (i)}
          <li><code>{f.component}</code> — {f.error}</li>
        {/each}
      </ul>
    </div>
  </div>
{/each}

<style>
  .banner {
    display: flex; gap: var(--space-3); align-items: flex-start;
    padding: var(--space-3) var(--space-4); border-radius: var(--radius-lg);
    background: var(--color-danger-soft); border: 1px solid var(--color-danger);
  }
  .banner :global(svg) { color: var(--color-danger); margin-top: 3px; }
  .banner.advisory { background: var(--color-warn-soft); border-color: var(--color-warn); }
  .banner.advisory :global(svg) { color: var(--color-warn); }
  ul { margin: var(--space-1) 0 0; padding-left: var(--space-4); font-size: var(--text-sm); }
</style>
