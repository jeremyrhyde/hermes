<script lang="ts">
  import type { Mode } from '../lib/types';

  let { mode }: { mode: Mode } = $props();
</script>

<p class="muted">{mode}</p>
