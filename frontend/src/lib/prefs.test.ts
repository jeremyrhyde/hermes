import { describe, expect, it } from 'vitest';
import { DEFAULT_PREFS, loadPrefs, parsePrefs, PREFS_KEY, resolveMotion, resolveTheme, savePrefs } from './prefs';

function memory(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    data,
  };
}
const throwing = {
  getItem: () => { throw new Error('SecurityError'); },
  setItem: () => { throw new Error('QuotaExceededError'); },
};

describe('parsePrefs', () => {
  it('defaults when nothing is stored', () => expect(parsePrefs(null)).toEqual(DEFAULT_PREFS));
  it('defaults on corrupt JSON', () => expect(parsePrefs('{nope')).toEqual(DEFAULT_PREFS));
  it('keeps known values and replaces unknown ones', () =>
    expect(parsePrefs('{"theme":"light","motion":"sideways","extra":1}')).toEqual({ theme: 'light', motion: 'system' }));
  it('replaces an unknown theme while keeping a valid motion', () =>
    expect(parsePrefs('{"theme":"sepia","motion":"full"}')).toEqual({ theme: 'auto', motion: 'full' }));
  it('defaults a non-object', () => expect(parsePrefs('"light"')).toEqual(DEFAULT_PREFS));
});

describe('storage', () => {
  it('round-trips', () => {
    const s = memory();
    savePrefs(s, { theme: 'dark', motion: 'reduced' });
    expect(s.data.get(PREFS_KEY)).toBe('{"theme":"dark","motion":"reduced"}');
    expect(loadPrefs(s)).toEqual({ theme: 'dark', motion: 'reduced' });
  });
  it('survives storage that throws, or is missing', () => {
    expect(loadPrefs(throwing)).toEqual(DEFAULT_PREFS);
    expect(() => savePrefs(throwing, DEFAULT_PREFS)).not.toThrow();
    expect(loadPrefs(null)).toEqual(DEFAULT_PREFS);
  });
});

describe('resolution', () => {
  it.each([
    ['auto', true, 'light'], ['auto', false, 'dark'],
    ['dark', true, 'dark'], ['light', false, 'light'],
  ] as const)('theme %s with OS light=%s → %s', (pref, osLight, out) => expect(resolveTheme(pref, osLight)).toBe(out));

  it.each([
    ['system', true, 'reduced'], ['system', false, 'full'],
    ['full', true, 'full'], ['reduced', false, 'reduced'],
  ] as const)('motion %s with OS reduce=%s → %s', (pref, osReduced, out) => expect(resolveMotion(pref, osReduced)).toBe(out));
});
