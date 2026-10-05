import { loadPrefs, resolveMotion, resolveTheme, savePrefs, type Prefs } from './prefs';

function storage(): Storage | null {
  try {
    return localStorage;
  } catch {
    return null;
  }
}

export const prefs: Prefs = $state(loadPrefs(storage()));

const LIGHT = '(prefers-color-scheme: light)';
const REDUCE = '(prefers-reduced-motion: reduce)';

/** Write the resolved theme and motion onto <html> (always a concrete value, so
 *  CSS has one light block and one reduced rule) and match the browser chrome. */
export function applyPrefs(): void {
  const root = document.documentElement;
  root.dataset.theme = resolveTheme(prefs.theme, matchMedia(LIGHT).matches);
  root.dataset.motion = resolveMotion(prefs.motion, matchMedia(REDUCE).matches);
  const bg = getComputedStyle(root).getPropertyValue('--color-bg').trim();
  if (bg) document.querySelector('meta[name="theme-color"]')?.setAttribute('content', bg);
}

/** Persist and apply on every change; follow the OS while a pref is Auto/Follow system. */
export function initPrefs(): () => void {
  const stopEffect = $effect.root(() => {
    $effect(() => {
      const snapshot: Prefs = { theme: prefs.theme, motion: prefs.motion };
      savePrefs(storage(), snapshot);
      applyPrefs();
    });
  });
  const light = matchMedia(LIGHT);
  const reduce = matchMedia(REDUCE);
  light.addEventListener('change', applyPrefs);
  reduce.addEventListener('change', applyPrefs);
  return () => {
    stopEffect();
    light.removeEventListener('change', applyPrefs);
    reduce.removeEventListener('change', applyPrefs);
  };
}
