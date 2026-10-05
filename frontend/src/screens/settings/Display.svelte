<script lang="ts">
  import type { MotionPref, ThemePref } from '../../lib/prefs';
  import { prefs } from '../../lib/prefs.svelte';

  const THEMES: { value: ThemePref; label: string }[] = [
    { value: 'auto', label: 'Auto' }, { value: 'dark', label: 'Dark' }, { value: 'light', label: 'Light' },
  ];
  const MOTIONS: { value: MotionPref; label: string }[] = [
    { value: 'system', label: 'Follow system' }, { value: 'full', label: 'Full' }, { value: 'reduced', label: 'Reduced' },
  ];
</script>

<section class="stack">
  <h2 class="section-title">Display</h2>
  <div class="field">
    <span id="theme-label">Theme</span>
    <div class="segmented" role="group" aria-labelledby="theme-label">
      {#each THEMES as t (t.value)}
        <button type="button" class:on={prefs.theme === t.value} aria-pressed={prefs.theme === t.value}
          onclick={() => (prefs.theme = t.value)}>{t.label}</button>
      {/each}
    </div>
  </div>
  <div class="field">
    <span id="motion-label">Motion</span>
    <div class="segmented" role="group" aria-labelledby="motion-label">
      {#each MOTIONS as m (m.value)}
        <button type="button" class:on={prefs.motion === m.value} aria-pressed={prefs.motion === m.value}
          onclick={() => (prefs.motion = m.value)}>{m.label}</button>
      {/each}
    </div>
  </div>
  <p class="help">Saved on this device only.</p>
</section>

<style>
  .field { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); flex-wrap: wrap; }
  .field > span { font-weight: 600; }
</style>
