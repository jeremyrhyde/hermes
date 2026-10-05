<script lang="ts">
  import { profileLabel } from '../../lib/settings';
  import { saveProfile, settings } from '../../lib/settings.svelte';

  let textarea: HTMLTextAreaElement | undefined = $state();
</script>

<section class="stack">
  <!-- The heading text is the textarea's real label; the version sits beside
       it, not inside, so a screen reader doesn't announce it as the name. -->
  <div class="head">
    <h2 class="section-title"><label for="profile-body">Taste profile</label></h2>
    <span class="version">{profileLabel(settings.profile)}</span>
  </div>
  <!-- Re-created only when a *different* version arrives: a refetch of the
       same version must never discard what is being typed. Disabled until the
       first load, the one window where typing would certainly be lost. -->
  {#key settings.profile.version}
    <textarea class="input" id="profile-body" rows="12" spellcheck="false"
      disabled={!settings.profileLoaded} bind:this={textarea} value={settings.profile.body}></textarea>
  {/key}
  <div class="actions">
    <p class="help">
      Saving appends a new version. Scoring picks it up at each source's next poll — articles already
      scored keep the score they were given until something re-scores them.
    </p>
    <button type="button" class="btn" disabled={settings.savingProfile || !settings.profileLoaded}
      onclick={() => textarea && void saveProfile(textarea.value)}>{settings.savingProfile ? 'Saving…' : 'Save'}</button>
  </div>
  {#if settings.profileError}<p class="inline-error" role="status">{settings.profileError}</p>{/if}
</section>

<style>
  .head { display: flex; align-items: baseline; justify-content: space-between; gap: var(--space-3); }
  .version { font-size: var(--text-sm); color: var(--color-text-muted); font-family: var(--font-mono); }
  .actions { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); }
</style>
