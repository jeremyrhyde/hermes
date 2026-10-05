<script lang="ts">
  // Lucide-derived icons (https://lucide.dev, ISC license), inlined so they
  // work offline and inherit currentColor. The star has no fill attribute on
  // its path so CSS can fill it (`svg { fill: currentColor }`).
  const ICONS: Record<string, string> = {
    newspaper: '<path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2"/><path d="M18 14h-8"/><path d="M15 18h-5"/><path d="M10 6h8v4h-8V6Z"/>',
    star: '<path d="M11.5 3.3a.6.6 0 0 1 1 0l2.4 5a.6.6 0 0 0 .5.3l5.4.8c.5.1.7.7.3 1l-3.9 3.8a.6.6 0 0 0-.2.6l1 5.4c.1.5-.5.9-.9.6l-4.8-2.5a.6.6 0 0 0-.6 0l-4.8 2.5c-.4.3-1-.1-.9-.6l1-5.4a.6.6 0 0 0-.2-.6l-4-3.8c-.3-.3-.1-.9.4-1l5.4-.8a.6.6 0 0 0 .5-.3Z"/>',
    rss: '<path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/>',
    sliders: '<path d="M4 21v-7"/><path d="M4 10V3"/><path d="M12 21v-9"/><path d="M12 8V3"/><path d="M20 21v-5"/><path d="M20 12V3"/><path d="M1 14h6"/><path d="M9 8h6"/><path d="M17 16h6"/>',
    alert: '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
    'thumb-up': '<path d="M7 10v11H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1z"/><path d="M7 10l4.5-7a2 2 0 0 1 3.4 2l-1.2 5h5.1a2 2 0 0 1 2 2.5l-1.8 7A2 2 0 0 1 17 21H7"/>',
    'thumb-down': '<path d="M17 14V3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1z"/><path d="M17 14l-4.5 7a2 2 0 0 1-3.4-2l1.2-5H5.2a2 2 0 0 1-2-2.5l1.8-7A2 2 0 0 1 7 3h10"/>',
    refresh: '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    'arrow-up': '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    'external-link': '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  };

  let { name, size = 20 }: { name: string; size?: number } = $props();
</script>

<svg
  class="icon"
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="2"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true">{@html ICONS[name] ?? ''}</svg>

<style>
  .icon { flex: none; }
</style>
