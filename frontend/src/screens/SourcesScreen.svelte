<script lang="ts">
  import { onMount } from 'svelte';
  import Icon from '../components/Icon.svelte';
  import { api } from '../lib/api';
  import { app, loadSources } from '../lib/app.svelte';
  import { relTime } from '../lib/format';
  import { toast, toastError } from '../lib/toast.svelte';
  import type { Source } from '../lib/types';

  let polling: Record<string, boolean> = $state({});

  onMount(() => {
    loadSources().catch(toastError);
  });

  async function poll(s: Source): Promise<void> {
    polling[s.id] = true;
    try {
      await api.pollSource(s.id);
      toast(`Polled ${s.name}`);
      await loadSources();
    } catch (err) {
      toastError(err); // 503 "poller is not running" when no API key is set
    } finally {
      polling[s.id] = false;
    }
  }

  const statusOf = (s: Source) => (s.disabled ? 'disabled' : s.enabled ? 'active' : 'paused');
</script>

<div class="page-head"><h2 class="section-title">Sources</h2></div>

{#if app.sources.length === 0}
  <p class="empty">No sources yet. List your feeds in <code>sources.yaml</code>.</p>
{:else}
  <ul class="list">
    {#each app.sources as s (s.id)}
      <li class="card source" class:warn={s.disabled || s.error_count > 0}>
        <div class="info">
          <span class="name">{s.name}</span>
          <span class="meta">
            <span>{statusOf(s)}</span>
            {#if s.last_polled_at}<span>polled {relTime(s.last_polled_at)}</span>{/if}
            {#if s.error_count > 0}<span class="bad">{s.error_count} error(s)</span>{/if}
            <!-- The only visibility into skipped posts. -->
            {#if s.unusable_count > 0}<span class="bad">{s.unusable_count} unreadable</span>{/if}
          </span>
        </div>
        <button type="button" class="btn small" disabled={polling[s.id]} onclick={() => void poll(s)}>
          <Icon name="refresh" size={16} /> {polling[s.id] ? 'Polling…' : 'Poll now'}
        </button>
      </li>
    {/each}
  </ul>
{/if}

<style>
  .list { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--space-3); }
  .source { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: var(--space-3) var(--space-4); }
  .source.warn { border-color: var(--color-warn); }
  .info { display: grid; gap: 2px; min-width: 0; }
  .name { font-weight: 600; }
  .meta { display: flex; flex-wrap: wrap; gap: var(--space-2); font-size: var(--text-sm); color: var(--color-text-muted); }
  .bad { color: var(--color-warn); }
</style>
