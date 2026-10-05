export interface ToastAction { label: string; run: () => void }
export interface ToastItem { id: number; text: string; kind: 'info' | 'error'; action?: ToastAction }

export const toasts: ToastItem[] = $state([]);
let nextId = 1;

export function dismiss(id: number): void {
  const i = toasts.findIndex((t) => t.id === id);
  if (i >= 0) toasts.splice(i, 1);
}

/** Show a toast. Undo-style toasts stay 4s; errors 5s; plain notices 2.5s. */
export function toast(
  text: string,
  kind: 'info' | 'error' = 'info',
  opts: { action?: ToastAction; ms?: number } = {},
): number {
  const id = nextId++;
  toasts.push({ id, text, kind, action: opts.action });
  const ms = opts.ms ?? (opts.action ? 4000 : kind === 'error' ? 5000 : 2500);
  setTimeout(() => dismiss(id), ms);
  return id;
}

export function toastError(error: unknown): void {
  toast(error instanceof Error ? error.message : String(error), 'error');
}
