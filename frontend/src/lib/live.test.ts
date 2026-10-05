import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { backoffDelay, LiveClient, wsUrl } from './live';

class FakeSocket {
  static all: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) { FakeSocket.all.push(this); }
  close() { if (this.closed) return; this.closed = true; this.onclose?.(); }
  open() { this.onopen?.(); }
  send(data: unknown) { this.onmessage?.({ data }); }
}
const last = () => FakeSocket.all[FakeSocket.all.length - 1];

function client(statuses: boolean[] = []) {
  return new LiveClient({
    url: 'ws://h/api/ws',
    WebSocketImpl: FakeSocket,
    random: () => 0.5, // no jitter
    onStatus: (c) => statuses.push(c),
  });
}

beforeEach(() => { FakeSocket.all = []; vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('wsUrl', () => {
  it('stays under a gateway prefix', () => expect(wsUrl('http://h:8000/hermes/')).toBe('ws://h:8000/hermes/api/ws'));
  it('uses wss under https', () => expect(wsUrl('https://h/hermes/#/feed')).toBe('wss://h/hermes/api/ws'));
  it('works standalone', () => expect(wsUrl('http://pi:8002/')).toBe('ws://pi:8002/api/ws'));
});

describe('backoffDelay', () => {
  it('doubles from 1s', () => expect([0, 1, 2, 3].map((a) => backoffDelay(a, () => 0.5))).toEqual([1000, 2000, 4000, 8000]));
  it('caps at 30s', () => expect(backoffDelay(10, () => 0.5)).toBe(30000));
  it('jitters within ±20%, never past the cap', () => {
    expect(backoffDelay(2, () => 0)).toBe(3200);
    expect(backoffDelay(2, () => 1)).toBe(4800);
    expect(backoffDelay(10, () => 1)).toBe(30000);
    expect(backoffDelay(10, () => 0)).toBe(24000);
  });
});

describe('LiveClient', () => {
  it('reports status and reconnects with growing delays, resetting on open', () => {
    const statuses: boolean[] = [];
    const c = client(statuses);
    c.connect();
    last().open();
    expect(statuses).toEqual([true]);
    last().close();
    expect(statuses).toEqual([true, false]);
    expect(FakeSocket.all).toHaveLength(1);
    vi.advanceTimersByTime(999);
    expect(FakeSocket.all).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.all).toHaveLength(2);
    last().close(); // failed attempt, never opened
    vi.advanceTimersByTime(2000);
    expect(FakeSocket.all).toHaveLength(3);
    last().open();
    last().close();
    vi.advanceTimersByTime(1000); // reset to 1s after a successful open
    expect(FakeSocket.all).toHaveLength(4);
  });

  it('runs reconnect hooks on every open except the first', () => {
    const c = client();
    const hook = vi.fn();
    c.onReconnect(hook);
    c.connect();
    last().open();
    expect(hook).not.toHaveBeenCalled();
    last().close();
    vi.advanceTimersByTime(1000);
    last().open();
    expect(hook).toHaveBeenCalledTimes(1);
  });

  it('dispatches by type and ignores unknown, typeless and malformed messages', () => {
    vi.spyOn(console, 'debug').mockImplementation(() => {});
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const c = client();
    const seen: unknown[] = [];
    c.on('source_polled', (e) => seen.push(e.data));
    c.connect();
    last().open();
    expect(() => {
      last().send('{"type":"source_polled","data":{"source_id":"a"}}');
      last().send('{"type":"mystery"}');
      last().send('{"no":"type"}');
      last().send('not json');
    }).not.toThrow();
    expect(seen).toEqual([{ source_id: 'a' }]);
  });

  it('keeps dispatching when a handler throws', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const c = client();
    const ok = vi.fn();
    c.on('system_ready', () => { throw new Error('boom'); });
    c.on('system_ready', ok);
    c.dispatch('{"type":"system_ready"}');
    expect(ok).toHaveBeenCalled();
  });

  it('does not reconnect after disconnect', () => {
    const c = client();
    c.connect();
    last().open();
    c.disconnect();
    vi.advanceTimersByTime(60000);
    expect(FakeSocket.all).toHaveLength(1);
  });

  it('retryNow skips the remaining backoff only when down', () => {
    const c = client();
    c.connect();
    last().close();
    c.retryNow();
    expect(FakeSocket.all).toHaveLength(2);
    c.retryNow(); // a socket is already connecting
    expect(FakeSocket.all).toHaveLength(2);
  });
});
