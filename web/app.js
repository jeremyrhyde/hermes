/* Hermes web UI — single Alpine.js state object.
 *
 * Architecture:
 *   - REST: fetch() against /feed/, /sources/ and /health
 *   - Realtime: WebSocket /ws receives Event JSON; applyEvent() patches state
 *   - applyEvent tolerates unknown event types: log and ignore, never throw
 *
 * No build step. No external deps beyond Alpine.js and its collapse plugin
 * (both loaded via CDN in index.html).
 */

function app() {
  return {
    // ---------------------------------------------------------------- state
    tab: 'feed',
    items: [],
    sources: [],
    expanded: null,
    wsConnected: false,
    bootFailures: [],
    clockText: '',

    // ---------------------------------------------------------------- internals
    _ws: null,
    _backoff: 1000,
    _maxBackoff: 30000,
    _clockTimer: null,

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
      const [feed, sources, health] = await Promise.allSettled([
        this._json('/feed/?limit=100'),
        this._json('/sources/'),
        this._json('/health'),
      ]);

      if (feed.status === 'fulfilled') this.items = feed.value || [];
      else console.error('refreshAll: /feed/', feed.reason);

      if (sources.status === 'fulfilled') this.sources = sources.value || [];
      else console.error('refreshAll: /sources/', sources.reason);

      if (health.status === 'fulfilled') {
        this.bootFailures = health.value?.startup_failures || [];
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

    relTime(iso) {
      if (!iso) return '';
      const then = new Date(iso);
      const mins = Math.round((Date.now() - then.getTime()) / 60000);
      if (mins < 60) return `${mins}m ago`;
      const hours = Math.round(mins / 60);
      if (hours < 24) return `${hours}h ago`;
      return `${Math.round(hours / 24)}d ago`;
    },

    // ---------------------------------------------------------------- user actions

    toggle(item) {
      this.expanded = this.expanded === item.article_id ? null : item.article_id;
    },

    recordClick(item) {
      // Interaction signals are captured but deliberately unused in ranking
      // (spec 7.3, research finding R10). Phase 4 decides whether to trust them.
      console.debug('click_through', item.article_id);
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

      switch (event.type) {
        case 'article_summarized': {
          const item = event.data?.item;
          if (!item) break;
          const idx = this.items.findIndex(i => i.article_id === item.article_id);
          const normalized = { score: null, rating: null, badges: [], ...item };
          if (idx >= 0) this.items.splice(idx, 1, normalized);
          else this.items.unshift(normalized);
          break;
        }

        case 'source_polled':
          this._json('/sources/').then(rows => { this.sources = rows || []; })
            .catch(err => console.error('sources refresh', err));
          break;

        case 'system_ready':
          // Server (re)started — re-pull anything cached from REST.
          this.refreshAll();
          break;

        case 'pipeline_error':
          console.warn('pipeline_error', event.data);
          break;

        case 'article_ingested':
        case 'article_scored':
        case 'profile_proposed':
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
