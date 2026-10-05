<script lang="ts">
  import Icon from './Icon.svelte';
  import { feed } from '../lib/feed.svelte';
  import { reducedMotion } from '../lib/motion';
  import { isListTab, type Tab } from '../lib/route';
  import { navigate, router } from '../lib/router.svelte';

  const ITEMS: { key: Tab; label: string; icon: string }[] = [
    { key: 'feed', label: 'Feed', icon: 'newspaper' },
    { key: 'saved', label: 'Saved', icon: 'star' },
    { key: 'sources', label: 'Sources', icon: 'rss' },
    { key: 'settings', label: 'Settings', icon: 'sliders' },
  ];

  const active = $derived(router.route.tab);
  const index = $derived(Math.max(0, ITEMS.findIndex((i) => i.key === active)));

  function select(key: Tab): void {
    if (key === active) {
      scrollTo({ top: 0, behavior: reducedMotion() ? 'auto' : 'smooth' });
      return;
    }
    // Feed and Saved share one filter selection, as they always have.
    navigate({ tab: key, cats: isListTab(key) ? feed.cats : [], open: null });
  }
</script>

<nav class="pill" aria-label="Sections">
  <span class="indicator" style:transform="translateX({index * 100}%)" aria-hidden="true"></span>
  {#each ITEMS as item (item.key)}
    <button
      type="button"
      class:active={active === item.key}
      aria-current={active === item.key ? 'page' : undefined}
      onclick={() => select(item.key)}>
      <Icon name={item.icon} size={20} />
      <span>{item.label}</span>
    </button>
  {/each}
</nav>

<style>
  /* Floating, bottom-centre at every width. Opaque, no backdrop-filter: blur
     is slow on a Pi kiosk. Only transform animates. */
  .pill {
    position: fixed; left: 50%; bottom: calc(var(--space-4) + var(--safe-bottom)); z-index: 20;
    transform: translateX(-50%);
    display: grid; grid-template-columns: repeat(4, 1fr);
    width: min(calc(100vw - 2 * var(--space-4)), 360px); height: var(--pill-h); padding: 4px;
    background: var(--color-surface); border: 1px solid var(--color-border);
    border-radius: var(--radius-pill); box-shadow: var(--shadow-2);
    view-transition-name: navpill;
  }
  .indicator {
    position: absolute; top: 4px; bottom: 4px; left: 4px; width: calc((100% - 8px) / 4);
    border-radius: var(--radius-pill); background: var(--color-accent-soft);
    transition: transform var(--transition);
  }
  button {
    position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
    min-height: var(--tap-min); border: 0; border-radius: var(--radius-pill); background: none;
    color: var(--color-text-muted); font-size: var(--text-xs); cursor: pointer;
  }
  .active { color: var(--color-accent); }
</style>
