// Device-local Display preferences. Never sent to the server: they describe
// this screen (a dim kiosk, a phone in sunlight), not the reader.

export type ThemePref = 'auto' | 'dark' | 'light';
export type MotionPref = 'system' | 'full' | 'reduced';
export interface Prefs { theme: ThemePref; motion: MotionPref }

export const PREFS_KEY = 'hermes.prefs.v1';
export const DEFAULT_PREFS: Prefs = { theme: 'auto', motion: 'system' };

const THEMES: readonly ThemePref[] = ['auto', 'dark', 'light'];
const MOTIONS: readonly MotionPref[] = ['system', 'full', 'reduced'];

type Reader = { getItem(key: string): string | null };
type Writer = { setItem(key: string, value: string): void };

export function parsePrefs(raw: string | null): Prefs {
  let value: unknown = null;
  try {
    value = raw === null ? null : JSON.parse(raw);
  } catch {
    return { ...DEFAULT_PREFS };
  }
  if (!value || typeof value !== 'object') return { ...DEFAULT_PREFS };
  const v = value as Record<string, unknown>;
  return {
    theme: THEMES.includes(v.theme as ThemePref) ? (v.theme as ThemePref) : DEFAULT_PREFS.theme,
    motion: MOTIONS.includes(v.motion as MotionPref) ? (v.motion as MotionPref) : DEFAULT_PREFS.motion,
  };
}

/** Private mode, blocked site data and quota errors all mean "defaults". */
export function loadPrefs(storage: Reader | null): Prefs {
  try {
    return parsePrefs(storage ? storage.getItem(PREFS_KEY) : null);
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

export function savePrefs(storage: Writer | null, prefs: Prefs): void {
  try {
    storage?.setItem(PREFS_KEY, JSON.stringify({ theme: prefs.theme, motion: prefs.motion }));
  } catch {
    /* storage unavailable: the choice lasts for this session only */
  }
}

export function resolveTheme(pref: ThemePref, osLight: boolean): 'dark' | 'light' {
  return pref === 'auto' ? (osLight ? 'light' : 'dark') : pref;
}

export function resolveMotion(pref: MotionPref, osReduced: boolean): 'full' | 'reduced' {
  return pref === 'system' ? (osReduced ? 'reduced' : 'full') : pref;
}
