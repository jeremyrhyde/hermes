import { afterEach, describe, expect, it, vi } from 'vitest';
import { dur, reducedMotion } from './motion';

function env(motion: string | undefined, osReduce: boolean) {
  vi.stubGlobal('document', { documentElement: { dataset: motion === undefined ? {} : { motion } } });
  vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('reduce') && osReduce }));
}
afterEach(() => vi.unstubAllGlobals());

describe('dur', () => {
  it('is 0 under an explicit Reduced even when the OS allows motion', () => {
    env('reduced', false);
    expect(dur(180)).toBe(0);
  });
  it('keeps motion under an explicit Full even when the OS asks to reduce', () => {
    env('full', true);
    expect(dur(180)).toBe(180);
  });
  it('falls back to the OS before the attribute is set', () => {
    env(undefined, true);
    expect(reducedMotion()).toBe(true);
    env(undefined, false);
    expect(reducedMotion()).toBe(false);
  });
});
