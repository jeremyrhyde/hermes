// Receive-only WebSocket client for api/ws. Knows nothing about the stores:
// they subscribe with on(type, fn), and App passes refreshAll to onReconnect
// because events may have been missed while the socket was down.

import type { LiveEvent } from './types';

export interface SocketLike {
  onopen: (() => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  close(): void;
}
export type SocketCtor = new (url: string) => SocketLike;

export interface LiveOptions {
  url: string;
  WebSocketImpl?: SocketCtor;
  onStatus?: (connected: boolean) => void;
  random?: () => number;
}

const BASE_MS = 1000;
const CAP_MS = 30000;

/** `api/ws` resolved against the page, so it stays under a gateway prefix. */
export function wsUrl(baseURI: string): string {
  const url = new URL('api/ws', baseURI);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.hash = '';
  return url.toString();
}

/** 1s doubling, ±20% jitter, never more than 30s. */
export function backoffDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(BASE_MS * 2 ** attempt, CAP_MS);
  return Math.min(CAP_MS, Math.round(base * (0.8 + 0.4 * random())));
}

export class LiveClient {
  private handlers = new Map<string, Set<(event: LiveEvent) => void>>();
  private reconnectHooks = new Set<() => void>();
  private socket: SocketLike | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private attempt = 0;
  private everOpened = false;
  private stopped = true;
  private isOpen = false;

  constructor(private opts: LiveOptions) {}

  on(type: string, fn: (event: LiveEvent) => void): () => void {
    let set = this.handlers.get(type);
    if (!set) this.handlers.set(type, (set = new Set()));
    set.add(fn);
    return () => set.delete(fn);
  }

  onReconnect(fn: () => void): () => void {
    this.reconnectHooks.add(fn);
    return () => this.reconnectHooks.delete(fn);
  }

  connect(): void {
    this.stopped = false;
    this.clearTimer();
    const Impl = this.opts.WebSocketImpl ?? (globalThis.WebSocket as unknown as SocketCtor);
    let ws: SocketLike;
    try {
      ws = new Impl(this.opts.url);
    } catch (err) {
      console.error('live: WebSocket construct failed', err);
      this.schedule();
      return;
    }
    this.socket = ws;
    ws.onopen = () => {
      const reconnect = this.everOpened;
      this.everOpened = true;
      this.attempt = 0;
      this.isOpen = true;
      this.opts.onStatus?.(true);
      if (reconnect) for (const fn of this.reconnectHooks) fn();
    };
    ws.onmessage = (msg) => this.dispatch(msg.data);
    ws.onclose = () => {
      if (this.socket !== ws) return;
      this.socket = null;
      if (this.isOpen) {
        this.isOpen = false;
        this.opts.onStatus?.(false);
      }
      if (!this.stopped) this.schedule();
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* already closed */
      }
    };
  }

  disconnect(): void {
    this.stopped = true;
    this.clearTimer();
    const ws = this.socket;
    this.socket = null;
    ws?.close();
    if (this.isOpen) {
      this.isOpen = false;
      this.opts.onStatus?.(false);
    }
  }

  /** Skip the rest of the backoff (e.g. the tab became visible again). */
  retryNow(): void {
    if (this.stopped || this.socket) return;
    this.connect();
  }

  dispatch(raw: unknown): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(String(raw));
    } catch (err) {
      console.error('live: unparsable message', err, raw);
      return;
    }
    if (!parsed || typeof parsed !== 'object' || typeof (parsed as LiveEvent).type !== 'string') {
      console.debug('live: message without a type', parsed);
      return;
    }
    const event = parsed as LiveEvent;
    const set = this.handlers.get(event.type);
    if (!set || set.size === 0) {
      console.debug('live: unhandled event type', event.type);
      return;
    }
    for (const fn of set) {
      try {
        fn(event);
      } catch (err) {
        console.error('live: handler failed', event.type, err);
      }
    }
  }

  private schedule(): void {
    const delay = backoffDelay(this.attempt, this.opts.random);
    this.attempt += 1;
    this.timer = setTimeout(() => {
      this.timer = null;
      this.connect();
    }, delay);
  }

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }
}
