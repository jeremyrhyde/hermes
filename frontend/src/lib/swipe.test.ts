import { describe, expect, it } from 'vitest';
import { classifyGesture, releaseVelocity, resist, shouldCommit } from './swipe';

describe('classifyGesture', () => {
  it('waits for 10px of travel', () => expect(classifyGesture(6, 6)).toBe('pending'));
  it('locks horizontal when |dx| > 1.5·|dy|', () => expect(classifyGesture(16, 10)).toBe('horizontal'));
  it('treats a diagonal as a scroll', () => expect(classifyGesture(14, 10)).toBe('vertical'));
  it('locks vertical for a scroll', () => expect(classifyGesture(2, 30)).toBe('vertical'));
});

describe('shouldCommit', () => {
  it('commits past 35% of the width', () => {
    expect(shouldCommit(140, 400, 0)).toBe('right');
    expect(shouldCommit(-140, 400, 0)).toBe('left');
  });
  it('springs back short of it', () => expect(shouldCommit(100, 400, 0.1)).toBeNull());
  it('commits on a flick in the direction of travel', () => expect(shouldCommit(40, 400, 0.6)).toBe('right'));
  it('ignores a flick back against the drag', () => expect(shouldCommit(40, 400, -0.6)).toBeNull());
  it('never commits at rest', () => expect(shouldCommit(0, 400, 1)).toBeNull());
});

describe('resist', () => {
  it('tracks the finger up to the threshold', () => expect(resist(100, 400)).toBe(100));
  it('rubber-bands past it, both directions', () => {
    expect(resist(240, 400)).toBe(140 + 100 * 0.3);
    expect(resist(-240, 400)).toBe(-(140 + 100 * 0.3));
  });
});

describe('releaseVelocity', () => {
  it('keeps the velocity while the finger was still moving', () => {
    expect(releaseVelocity(0.8, 40)).toBe(0.8);
    expect(releaseVelocity(0.8, 100)).toBe(0.8);
  });
  it('drops a stale flick', () => expect(releaseVelocity(0.8, 101)).toBe(0));
});
