import { dur } from './motion';

/** Travel (px) before a gesture is classified. */
export const AXIS_LOCK_PX = 10;
/** Horizontal only if |dx| beats |dy| by this factor; anything else is a scroll. */
export const AXIS_RATIO = 1.5;
/** Commit once dragged past this share of the card's width… */
export const COMMIT_SHARE = 0.35;
/** …or when released while flicking faster than this (px per ms). */
export const FLICK_SPEED = 0.5;

export type Axis = 'pending' | 'horizontal' | 'vertical';
export type Direction = 'left' | 'right';

export function classifyGesture(dx: number, dy: number): Axis {
  if (Math.hypot(dx, dy) < AXIS_LOCK_PX) return 'pending';
  return Math.abs(dx) > AXIS_RATIO * Math.abs(dy) ? 'horizontal' : 'vertical';
}

export function shouldCommit(dx: number, width: number, velocity: number): Direction | null {
  if (dx === 0) return null;
  const far = Math.abs(dx) >= COMMIT_SHARE * width;
  const flick = Math.abs(velocity) >= FLICK_SPEED && Math.sign(velocity) === Math.sign(dx);
  return far || flick ? (dx > 0 ? 'right' : 'left') : null;
}

/** A flick only counts if the finger was still moving when it lifted. */
export const FLICK_IDLE_MS = 100;
export function releaseVelocity(velocity: number, idleMs: number): number {
  return idleMs > FLICK_IDLE_MS ? 0 : velocity;
}

/** Displacement shown for a drag of `dx`: 1:1 up to the threshold, then 30%. */
export function resist(dx: number, width: number): number {
  const limit = COMMIT_SHARE * width;
  const a = Math.abs(dx);
  return a <= limit ? dx : Math.sign(dx) * (limit + (a - limit) * 0.3);
}

interface Options {
  onLeft: () => void;
  onRight: () => void;
}

/**
 * Svelte action for a swipeable card. The node must contain an element with
 * `data-swipe-face`; that element follows the finger, and `data-swipe` on the
 * node ('left' | 'right' | absent) tells CSS which underlay to reveal.
 *
 * Touch and pen only: a mouse drag stays text selection, and desktop has the
 * buttons. The node should have `touch-action: pan-y` so vertical scrolling
 * stays native — this only ever captures a gesture once it is horizontal.
 */
export function swipe(node: HTMLElement, options: Options) {
  let opts = options;
  let pointerId: number | null = null;
  let startX = 0;
  let startY = 0;
  let dx = 0;
  let lastX = 0;
  let lastT = 0;
  let velocity = 0;
  let axis: Axis = 'pending';
  let clearTimer: ReturnType<typeof setTimeout> | undefined;

  const face = () => node.querySelector<HTMLElement>('[data-swipe-face]');

  function clearUnderlay() {
    clearTimeout(clearTimer);
    clearTimer = undefined;
    // Not if a new drag has begun since the spring-back started.
    if (pointerId === null) delete node.dataset.swipe;
  }

  function setDx(px: number, animate: boolean) {
    const el = face();
    if (!el) return;
    const ms = animate ? dur(180) : 0;
    clearTimeout(clearTimer);
    clearTimer = undefined;
    el.style.transition = ms ? `transform ${ms}ms ease-out` : 'none';
    el.style.transform = px ? `translateX(${px}px)` : '';
    if (px > 0) node.dataset.swipe = 'right';
    else if (px < 0) node.dataset.swipe = 'left';
    else if (ms > 0) {
      // Keep the underlay visible until the face has finished returning.
      el.addEventListener('transitionend', clearUnderlay, { once: true });
      clearTimer = setTimeout(clearUnderlay, ms + 50);
    } else delete node.dataset.swipe;
  }

  // A swipe that started on the card head must not also toggle it open.
  function swallowNextClick() {
    const stop = (e: Event) => {
      e.preventDefault();
      e.stopPropagation();
    };
    node.addEventListener('click', stop, { capture: true, once: true });
    setTimeout(() => node.removeEventListener('click', stop, { capture: true }), 0);
  }

  function onDown(e: PointerEvent) {
    if (e.pointerType === 'mouse') return;
    // A different second pointer is ignored; the same pointer again means its
    // pointerup was lost (a pen lifted outside the card), so start afresh.
    if (pointerId !== null && e.pointerId !== pointerId) return;
    // A new touch ends any spring-back in progress, so its pending clear can't fire mid-gesture.
    clearTimeout(clearTimer);
    clearTimer = undefined;
    delete node.dataset.swipe;
    pointerId = e.pointerId;
    startX = lastX = e.clientX;
    startY = e.clientY;
    lastT = e.timeStamp;
    dx = velocity = 0;
    axis = 'pending';
  }

  function onMove(e: PointerEvent) {
    if (e.pointerId !== pointerId) return;
    if (axis === 'pending') {
      axis = classifyGesture(e.clientX - startX, e.clientY - startY);
      if (axis === 'vertical') {
        pointerId = null;
        return;
      }
      if (axis === 'horizontal') node.setPointerCapture(e.pointerId);
    }
    if (axis !== 'horizontal') return;
    dx = e.clientX - startX;
    const dt = e.timeStamp - lastT;
    if (dt > 0) velocity = (e.clientX - lastX) / dt;
    lastX = e.clientX;
    lastT = e.timeStamp;
    setDx(resist(dx, node.offsetWidth), false);
  }

  function onUp(e: PointerEvent) {
    if (e.pointerId !== pointerId) return;
    pointerId = null;
    if (axis !== 'horizontal') return;
    swallowNextClick();
    const dir = e.type === 'pointerup' ? shouldCommit(dx, node.offsetWidth, releaseVelocity(velocity, e.timeStamp - lastT)) : null;
    setDx(0, true);
    if (!dir) return;
    navigator.vibrate?.(10);
    if (dir === 'right') opts.onRight();
    else opts.onLeft();
  }

  node.addEventListener('pointerdown', onDown);
  node.addEventListener('pointermove', onMove);
  node.addEventListener('pointerup', onUp);
  node.addEventListener('pointercancel', onUp);

  return {
    update(next: Options) {
      opts = next;
    },
    destroy() {
      node.removeEventListener('pointerdown', onDown);
      node.removeEventListener('pointermove', onMove);
      node.removeEventListener('pointerup', onUp);
      node.removeEventListener('pointercancel', onUp);
    },
  };
}
