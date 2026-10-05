<script lang="ts">
  import type { Snippet } from 'svelte';
  import { liveState } from '../lib/live.svelte';

  let { children }: { children?: Snippet } = $props();
</script>

<header class="header">
  <div class="bar">
    <h1 class="brand">Hermes</h1>
    <!-- Informational only: REST keeps working while the socket is down. -->
    <span class="live" class:online={liveState.connected}
      title={liveState.connected ? 'Realtime connected' : 'Realtime disconnected'}>
      <span class="dot" aria-hidden="true"></span>{liveState.connected ? 'live' : 'offline'}
    </span>
  </div>
  {#if children}<div class="extra">{@render children()}</div>{/if}
</header>

<style>
  .header {
    position: sticky; top: 0; z-index: 10; padding-top: var(--safe-top);
    background: var(--color-bg); border-bottom: 1px solid var(--color-border-soft);
    view-transition-name: header;
  }
  .bar, .extra {
    max-width: var(--content-max); margin: 0 auto;
    padding: 0 calc(var(--space-4) + var(--safe-right)) 0 calc(var(--space-4) + var(--safe-left));
  }
  .bar { display: flex; align-items: center; justify-content: space-between; min-height: var(--header-h); }
  .brand { font-weight: 600; letter-spacing: 0.02em; }
  .live { display: inline-flex; align-items: center; gap: var(--space-2); font-size: var(--text-sm); color: var(--color-text-muted); }
  .dot { width: 8px; height: 8px; border-radius: var(--radius-pill); background: var(--color-danger); transition: background var(--transition-fast); }
  .online .dot { background: var(--color-accent); }
  .extra { padding-bottom: var(--space-2); }
</style>
