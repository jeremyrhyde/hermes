<script lang="ts">
  import { onMount } from 'svelte';
  import About from './About.svelte';
  import Display from './Display.svelte';
  import Knobs from './Knobs.svelte';
  import ProfileEditor from './ProfileEditor.svelte';
  import Review from './Review.svelte';
  import { refreshSettings } from '../../lib/settings.svelte';

  onMount(() => {
    void refreshSettings();
  });
</script>

<div class="card panel"><Knobs /></div>
<div class="card panel"><ProfileEditor /></div>
<div class="card panel"><Review /></div>
<div class="card panel"><Display /></div>
<div class="card panel"><About /></div>

<style>
  .panel { padding: var(--space-4); }
</style>
