/* Hermes web UI — single Alpine.js state object.
 *
 * Architecture:
 *   - REST: fetch() against /health and the domain endpoints
 *   - Realtime: WebSocket /ws receives Event JSON; applyEvent() patches state
 *   - Optimism: user actions flip UI immediately; revert on HTTP failure
 *
 * No build step. No external deps beyond Alpine.js (loaded via CDN in index.html).
 */

function app() {
  return {
    // ---------------------------------------------------------------- state
    tab: 'dashboard',
    health: {},
    bootFailures: [],
    events: [],
    wsConnected: false,
    clockText: '',
    pinging: false,

    // ---------------------------------------------------------------- internals
    _ws: null,
    _backoff: 1000,
    _maxBackoff: 30000,
    _clockTimer: null,
    _seq: 0,
    _maxEvents: 200,

    // ---------------------------------------------------------------- lifecycle
    async init() {
      this._tickClock();
      this._clockTimer = setInterval(() => this._tickClock(), 30_000);
      await this.refreshAll();
      this.connectWebSocket();
    },

    async refreshAll() {
      // Parallel fetch so one failing endpoint doesn't blank the rest of the
      // page. Add new endpoints to this array as the API grows.
      const [health] = await Promise.allSettled([
        this._json('/health'),
      ]);

      if (health.status === 'fulfilled') {
        this.health = health.value || {};
        this.bootFailures = this.health.startup_failures || [];
      } else {
        console.error('refreshAll: /health', health.reason);
      }
    },

    // ---------------------------------------------------------------- helpers
    async _json(path, opts = {}) {
      const res = await fetch(path, opts);
      if (!res.ok) throw new Error(`${path} ${res.status}`);
      // 204 No Content has no body
      if (res.status === 204) return null;
      return res.json();
    },

    _tickClock() {
      const d = new Date();
      const h = d.getHours();
      const m = d.getMinutes();
      const period = h >= 12 ? 'PM' : 'AM';
      const h12 = ((h + 11) % 12) + 1;
      this.clockText = `${h12}:${String(m).padStart(2, '0')} ${period}`;
    },

    _recordEvent(event) {
      // Stamp a render key and a local time string, newest first, capped so a
      // long-lived kiosk session can't grow the array without bound.
      const stamped = {
        ...event,
        _key: ++this._seq,
        _time: new Date().toLocaleTimeString(),
      };
      this.events.unshift(stamped);
      if (this.events.length > this._maxEvents) {
        this.events.length = this._maxEvents;
      }
    },

    // ---------------------------------------------------------------- user actions

    async ping() {
      // Placeholder action. The real state change arrives over the WebSocket,
      // so there is nothing to flip optimistically here — for a mutation that
      // does own local state, flip it first, await, then re-sync from the
      // response and revert on error.
      this.pinging = true;
      try {
        await this._json('/events/ping', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
        });
      } catch (err) {
        console.error('ping failed', err);
      } finally {
        this.pinging = false;
      }
    },

    // ---------------------------------------------------------------- WebSocket

    connectWebSocket() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      const url = `${proto}://${location.host}/ws`;
      let ws;
      try {
        ws = new WebSocket(url);
      } catch (err) {
        console.error('WebSocket construct failed', err);
        this._scheduleReconnect();
        return;
      }
      this._ws = ws;
      ws.onopen = () => {
        this.wsConnected = true;
        this._backoff = 1000;
      };
      ws.onmessage = (msg) => {
        try {
          this.applyEvent(JSON.parse(msg.data));
        } catch (err) {
          console.error('WS message parse error', err, msg.data);
        }
      };
      ws.onclose = () => {
        this.wsConnected = false;
        this._scheduleReconnect();
      };
      ws.onerror = () => {
        // Force close to trigger the reconnect path.
        try { ws.close(); } catch (_) { /* noop */ }
      };
    },

    _scheduleReconnect() {
      const delay = Math.min(this._backoff, this._maxBackoff);
      setTimeout(() => this.connectWebSocket(), delay);
      this._backoff = Math.min(this._backoff * 2, this._maxBackoff);
    },

    applyEvent(event) {
      // Event shape is from schemas/events.py:
      //   { type, subject, data, timestamp, source }
      if (!event || !event.type) return;

      this._recordEvent(event);

      switch (event.type) {
        case 'system_ready':
          // Server (re)started — re-pull anything cached from REST.
          this.refreshAll();
          break;

        case 'system_error':
          console.warn('system_error', event.data);
          break;

        case 'state_changed':
          // Patch the matching entity here once the UI renders real state.
          break;

        default:
          // Unknown event — log and move on.
          console.debug('unknown event type', event.type);
      }
    },
  };
}

// Expose globally so Alpine's x-data="app()" can find it.
window.app = app;
