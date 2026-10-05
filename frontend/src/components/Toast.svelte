<script lang="ts">
  import { fly } from 'svelte/transition';
  import { dur } from '../lib/motion';
  import { dismiss, toasts } from '../lib/toast.svelte';

  /** Raised above the NewPill while it is showing. */
  let { lifted = false }: { lifted?: boolean } = $props();
</script>

<div class="toasts" class:lifted aria-live="polite">
  {#each toasts as t (t.id)}
    <div class="toast {t.kind}" role={t.kind === 'error' ? 'alert' : undefined} transition:fly={{ y: 16, duration: dur(180) }}>
      <span>{t.text}</span>
      {#if t.action}
        <button type="button" class="action" onclick={() => { t.action?.run(); dismiss(t.id); }}>{t.action.label}</button>
      {/if}
    </div>
  {/each}
</div>

<style>
  /* Bottom stacking: NavPill, then NewPill, then toasts. */
  .toasts {
    position: fixed; left: 50%; z-index: 40;
    bottom: calc(var(--pill-h) + var(--space-4) + var(--safe-bottom) + var(--space-3));
    transform: translateX(-50%);
    display: grid; gap: var(--space-2); width: min(92vw, 420px);
    pointer-events: none; transition: transform var(--transition);
  }
  .toasts.lifted { transform: translate(-50%, calc(-1 * (var(--tap-min) + var(--space-2)))); }
  .toast {
    display: flex; align-items: center; justify-content: space-between; gap: var(--space-3);
    padding: var(--space-3) var(--space-4); border-radius: var(--radius-md);
    background: var(--color-surface-3); box-shadow: var(--shadow-2); font-size: var(--text-sm);
  }
  .toast:has(.action) { pointer-events: auto; padding-right: var(--space-2); }
  .toast.error { background: var(--color-danger); color: var(--color-on-accent); }
  .action {
    min-height: var(--tap-min); padding: 0 var(--space-3); border: 0; background: none;
    color: var(--color-accent); font-weight: 600; cursor: pointer;
  }
  .toast.error .action { color: inherit; text-decoration: underline; }
</style>
