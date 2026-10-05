<script lang="ts">
  let { message, onRetry }: { message: string; onRetry: () => void } = $props();

  // A kiosk has nobody to tap Retry.
  $effect(() => {
    const timer = setInterval(onRetry, 5000);
    return () => clearInterval(timer);
  });
</script>

<div class="center-state" role="alert">
  <p>{message}</p>
  <button type="button" class="btn" onclick={onRetry}>Retry</button>
  <p class="help">Retrying every 5 seconds.</p>
</div>
