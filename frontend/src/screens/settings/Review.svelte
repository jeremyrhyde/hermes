<script lang="ts">
  import { reviewPanel } from '../../lib/settings';
  import { approve, propose, reject, settings } from '../../lib/settings.svelte';

  const panel = $derived(reviewPanel(settings.review.state));
  const proposal = $derived(settings.review.proposal);
  let textarea: HTMLTextAreaElement | undefined = $state();
</script>

<section class="stack">
  <h2 class="section-title">Re-evaluation</h2>

  {#if panel === 'insufficient'}
    <p class="help">Not enough data for re-evaluation yet — {settings.review.count} of {settings.review.threshold} ratings.</p>
  {:else if panel === 'ready'}
    <button type="button" class="btn" disabled={settings.generating} onclick={() => void propose()}>
      {settings.generating ? 'Reading your ratings…' : `Propose an updated profile from ${settings.review.count} ratings`}
    </button>
    <p class="help">This reads your rated articles and drafts a revision. Nothing goes live until you approve it.</p>
  {:else}
    <p class="help">
      Drafted from your ratings{proposal ? ` as ${proposal.version}` : ''}. Edit it here if you like — approving
      saves whatever is in the box.
    </p>
    {#key proposal?.version}
      <textarea class="input" rows="12" spellcheck="false" aria-label="Proposed taste profile"
        bind:this={textarea} value={proposal?.body ?? ''}></textarea>
    {/key}
    <label class="check">
      <input type="checkbox" bind:checked={settings.rescore} />
      <span>Re-score the articles that were scored under the old profile</span>
    </label>
    <p class="help">
      Re-scoring clears those scores so the pipeline can judge them again. It works through 50 articles per
      source per poll, so for a while the feed ranks old and new scores against each other.
    </p>
    <div class="actions">
      <button type="button" class="btn quiet" disabled={settings.resolving} onclick={() => void reject()}>Reject</button>
      <button type="button" class="btn primary" disabled={settings.resolving}
        onclick={() => textarea && void approve(textarea.value)}>{settings.resolving ? 'Working…' : 'Approve'}</button>
    </div>
  {/if}

  {#if settings.reviewError}<p class="inline-error" role="status">{settings.reviewError}</p>{/if}
</section>

<style>
  .check { display: flex; align-items: center; gap: var(--space-2); min-height: var(--tap-min); cursor: pointer; }
  .check input { width: 20px; height: 20px; accent-color: var(--color-accent); }
  .actions { display: flex; justify-content: flex-end; gap: var(--space-2); }
</style>
