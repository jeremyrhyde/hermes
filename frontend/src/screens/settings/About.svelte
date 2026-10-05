<script lang="ts">
  import { refreshAll } from '../../lib/app.svelte';
  import { liveState } from '../../lib/live.svelte';
  import { router } from '../../lib/router.svelte';

  let reloading = $state(false);

  async function reload(): Promise<void> {
    reloading = true;
    try {
      await refreshAll(router.route);
    } finally {
      reloading = false;
    }
  }
</script>

<section class="stack">
  <h2 class="section-title">About</h2>
  <!-- The version is how you confirm a kiosk picked up a new build. -->
  <dl class="facts">
    <dt>Version</dt><dd>{__APP_VERSION__}</dd>
    <dt>Realtime</dt><dd>{liveState.connected ? 'connected' : 'disconnected'}</dd>
  </dl>
  <button type="button" class="btn" disabled={reloading} onclick={() => void reload()}>
    {reloading ? 'Reloading…' : 'Reload data'}
  </button>
</section>

<style>
  .facts { display: grid; grid-template-columns: auto 1fr; gap: var(--space-1) var(--space-4); margin: 0; font-size: var(--text-sm); }
  dt { color: var(--color-text-muted); }
  dd { margin: 0; font-family: var(--font-mono); }
</style>
