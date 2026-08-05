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
    // One entry per *configured* filter, including zero counts — a dead-end
    // category is rendered disabled rather than vanishing mid-interaction.
    // Empty when categories are unconfigured, which hides the row entirely.
    categoryFilters: [],
    // Active selection, AND-combined. Alpine state only: deliberately not
    // persisted, so a reload always lands on the unfiltered feed.
    selected: [],
    expanded: null,
    wsConnected: false,
    bootFailures: [],
    clockText: '',

    // ---------------------------------------------------------------- internals
    _ws: null,
    _backoff: 1000,
    _maxBackoff: 30000,
    _clockTimer: null,
    // Guards the selection-dependent state (items, categoryFilters) against
    // out-of-order responses: click two filters quickly and the first pair of
    // fetches can resolve after the second, leaving counts that contradict
    // the selection. Only the newest generation is allowed to write.
    _filterSeq: 0,

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
      const seq = ++this._filterSeq;
      const [feed, categories, sources, health] = await Promise.allSettled([
        this._json(`/feed/?limit=100${this._categoryQuery()}`),
        this._json(`/categories/${this._categoryQuery('?')}`),
        this._json('/sources/'),
        this._json('/health'),
      ]);

      // Sources and health don't depend on the selection, so they apply even
      // if a newer filter request has superseded this one.
      if (seq === this._filterSeq) {
        if (feed.status === 'fulfilled') this.items = feed.value || [];
        else console.error('refreshAll: /feed/', feed.reason);

        if (categories.status === 'fulfilled') {
          this.categoryFilters = categories.value?.filters || [];
        } else {
          console.error('refreshAll: /categories/', categories.reason);
        }
      }

      if (sources.status === 'fulfilled') this.sources = sources.value || [];
      else console.error('refreshAll: /sources/', sources.reason);

      if (health.status === 'fulfilled') {
        this.bootFailures = health.value?.startup_failures || [];
      } else {
        console.error('refreshAll: /health', health.reason);
      }
    },

    /* Refetch the two endpoints the selection parameterizes. Sources and
     * health don't vary with it, so a filter click leaves them alone. */
    async refreshFiltered() {
      const seq = ++this._filterSeq;
      const [feed, categories] = await Promise.allSettled([
        this._json(`/feed/?limit=100${this._categoryQuery()}`),
        this._json(`/categories/${this._categoryQuery('?')}`),
      ]);
      if (seq !== this._filterSeq) return;  // superseded by a later click

      if (feed.status === 'fulfilled') this.items = feed.value || [];
      else console.error('refreshFiltered: /feed/', feed.reason);

      if (categories.status === 'fulfilled') {
        this.categoryFilters = categories.value?.filters || [];
      } else {
        console.error('refreshFiltered: /categories/', categories.reason);
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

    /* Repeated ?category= params for the active selection, or '' when nothing
     * is selected. *lead* is the separator for the first param: '?' when the
     * path has no query string yet, '&' (the default) when it already does. */
    _categoryQuery(lead = '&') {
      if (this.selected.length === 0) return '';
      const params = new URLSearchParams();
      for (const name of this.selected) params.append('category', name);
      return lead + params.toString();
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

    toggleCategory(name) {
      const idx = this.selected.indexOf(name);
      if (idx >= 0) this.selected.splice(idx, 1);
      else this.selected.push(name);
      return this.refreshFiltered();
    },

    clearCategories() {
      this.selected = [];
      return this.refreshFiltered();
    },

    /* AND semantics: the article must carry *every* active filter, not any of
     * them. Used to decide whether a live-arriving card belongs in the
     * current view. An untagged article satisfies only the empty selection. */
    matchesSelection(item) {
      const categories = item?.categories || [];
      return this.selected.every(name => categories.includes(name));
    },

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

    _refreshCounts() {
      const seq = this._filterSeq;
      return this._json(`/categories/${this._categoryQuery('?')}`)
        .then(data => {
          if (seq !== this._filterSeq) return;  // selection moved on
          this.categoryFilters = data?.filters || [];
        })
        .catch(err => console.error('counts refresh', err));
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
          const normalized = {
            score: null, rating: null, badges: [], categories: [], ...item,
          };
          const idx = this.items.findIndex(i => i.article_id === item.article_id);
          if (this.matchesSelection(normalized)) {
            if (idx >= 0) this.items.splice(idx, 1, normalized);
            else this.items.unshift(normalized);
          } else if (idx >= 0) {
            // Was visible, no longer matches — drop it rather than leave a
            // stale card in a filtered view.
            this.items.splice(idx, 1);
          }
          // Counts shift even when the article itself is filtered out.
          this._refreshCounts();
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
