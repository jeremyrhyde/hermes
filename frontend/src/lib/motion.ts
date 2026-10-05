/** Whether motion should be suppressed. `data-motion` on <html> is the resolved
 *  value (OS preference or the Display override); before it is set, ask the OS. */
export function reducedMotion(): boolean {
  const attr = typeof document !== 'undefined' ? document.documentElement.dataset.motion : undefined;
  if (attr) return attr === 'reduced';
  return typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** Transition duration that collapses to 0 when motion is reduced. */
export function dur(ms: number): number {
  return reducedMotion() ? 0 : ms;
}
