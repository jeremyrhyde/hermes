<script lang="ts">
  import { KNOBS } from '../../lib/settings';
  import { commitKnob, settings } from '../../lib/settings.svelte';
</script>

<section class="stack" aria-labelledby="knobs-title">
  <h2 class="section-title" id="knobs-title">Feed</h2>
  {#each KNOBS as k (k.key)}
    <div class="knob">
      <label class="label" for={k.id}>
        <span>{k.label}</span>
        <output for={k.id}>{settings.drafts[k.key]}{k.unit}</output>
      </label>
      <!-- The number tracks the drag live; the write waits for release
           (change), never one PUT per pixel. -->
      <input type="range" class="range" id={k.id} min={k.min} max={k.max} step="1"
        disabled={!settings.prefs}
        bind:value={settings.drafts[k.key]}
        onchange={() => void commitKnob(k.key, String(settings.drafts[k.key]))} />
      <p class="help">{k.help}</p>
    </div>
  {/each}
  {#if settings.knobError}<p class="inline-error" role="status">{settings.knobError}</p>{/if}
</section>

<style>
  .knob { display: grid; gap: var(--space-1); }
  .label { display: flex; justify-content: space-between; font-weight: 600; }
  output { font-variant-numeric: tabular-nums; color: var(--color-accent); }
  .range { width: 100%; min-height: var(--tap-min); accent-color: var(--color-accent); }
</style>
