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
    // The Saved list. Loaded on first switch to the tab, not at init — most
    // sessions never open it.
    savedItems: [],
    // Whether *anything* is pinned, independent of the active filters. The
    // Saved filter row hides entirely when nothing is, and savedItems.length
    // cannot answer that question while a filter is narrowing it.
    hasAnySaved: false,
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
      // page. Sources and health don't depend on the selection or the tab, so
      // they run alongside refreshFiltered rather than inside it.
      const filtered = this.refreshFiltered();
      const [sources, health] = await Promise.allSettled([
        this._json('/sources/'),
        this._json('/health'),
      ]);

      if (sources.status === 'fulfilled') this.sources = sources.value || [];
      else console.error('refreshAll: /sources/', sources.reason);

      if (health.status === 'fulfilled') {
        this.bootFailures = health.value?.startup_failures || [];
      } else {
        console.error('refreshAll: /health', health.reason);
      }

      await filtered;
    },

    /* Refetch the endpoints the selection and the active tab parameterize.
     * Sources and health vary with neither, so a filter click or a tab switch
     * leaves them alone.
     *
     * A tab switch issues the same request pair a filter click does, which is
     * why it goes through here and inherits the _filterSeq guard: switching
     * while the previous tab's responses are in flight is exactly the
     * out-of-order case, and a late response would otherwise paint one tab's
     * counts over the other's.
     *
     * *nextTab* is why the tab flip lives here rather than in switchTab. An
     * x-show whose value goes false then true again inside one animation frame
     * stays hidden forever: Alpine's hide path defers through queueMicrotask ->
     * requestAnimationFrame -> a promise chain while its show path is a bare
     * requestAnimationFrame, so the late hide lands last and nothing cancels
     * it. Flipping the tab before its data arrived did exactly that — the list
     * and the filter row both flapped against a momentarily empty savedItems —
     * and left the Saved tab blank. Applying the tab and its data in one
     * synchronous block means every expression sees one transition, not two. */
    async refreshFiltered(nextTab = this.tab) {
      const seq = ++this._filterSeq;
      const saved = nextTab === 'saved';
      const listPath = saved ? '/saved/' : '/feed/';
      const requests = [
        this._json(`${listPath}?limit=100${this._categoryQuery()}`),
        this._json(`/categories/?scope=${saved ? 'saved' : 'feed'}${this._categoryQuery()}`),
      ];
      // Unfiltered and capped at one row: this asks "is anything pinned at
      // all", which the filtered list above cannot answer.
      if (saved) requests.push(this._json('/saved/?limit=1'));

      const [list, categories, anySaved] = await Promise.allSettled(requests);
      if (seq !== this._filterSeq) return;  // superseded by a later click

      // All of it or none of it. Counts that describe a list the user cannot
      // see are worse than stale counts, and a tab that switched to a list
      // that failed to load would render "nothing here" as if that were true.
      if (list.status !== 'fulfilled') {
        console.error(`refreshFiltered: ${listPath}`, list.reason);
        return;
      }
      this.tab = nextTab;
      if (saved) this.savedItems = list.value || [];
      else this.items = list.value || [];

      if (categories.status === 'fulfilled') {
        this.categoryFilters = categories.value?.filters || [];
      } else {
        console.error('refreshFiltered: /categories/', categories.reason);
      }

      if (anySaved?.status === 'fulfilled') {
        this.hasAnySaved = (anySaved.value || []).length > 0;
      } else if (anySaved) {
        console.error('refreshFiltered: /saved/', anySaved.reason);
      }
    },

    // ---------------------------------------------------------------- derived

    /* Which list the shared card template renders. Both tabs go through this
     * so there is exactly one copy of the card markup. */
    get visibleItems() {
      return this.tab === 'saved' ? this.savedItems : this.items;
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

    /* The selection is shared across tabs on purpose: the filter row is the
     * same row, so having it silently mean something different on each tab
     * would be the surprise. Sources parameterizes nothing, so switching to it
     * refetches nothing. */
    switchTab(name) {
      if (this.tab === name) return;
      if (name === 'sources') {  // parameterizes nothing, so nothing to fetch
        this.tab = name;
        return;
      }
      // refreshFiltered flips the tab once the list is in hand; see its note on
      // why the two cannot be separated.
      return this.refreshFiltered(name);
    },

    /* Optimistic: flip the star now, reconcile with the server after.
     *
     * On the Saved tab, unstarring also removes the card, so reverting means
     * putting the item back at its original index — restoring a star on a card
     * that is no longer rendered would leave the failure invisible. */
    async toggleSaved(item) {
      const next = !item.saved;
      const list = this.visibleItems;
      const idx = list.indexOf(item);
      const removing = !next && this.tab === 'saved' && idx >= 0;

      item.saved = next;
      if (removing) list.splice(idx, 1);

      try {
        await this._json(`/saved/${item.article_id}`, {
          method: next ? 'POST' : 'DELETE',
        });
        if (next) this.hasAnySaved = true;
        else if (this.tab === 'saved' && this.savedItems.length === 0) {
          this.hasAnySaved = false;
        }
        if (next && this.tab === 'saved' && !this.savedItems.includes(item)) {
          // Re-starred, on the Saved tab, a card an earlier click had already
          // removed from the list. Where it belongs now is the server's call —
          // reload rather than guess an index. Reloads the counts too, so the
          // refresh below would be redundant.
          return this.refreshFiltered();
        }
        this._refreshCounts();
      } catch (err) {
        console.error('toggleSaved', err);
        item.saved = !next;
        if (removing) list.splice(idx, 0, item);
      }
    },

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
      const opening = this.expanded !== item.article_id;
      this.expanded = opening ? item.article_id : null;
      // Only on the way open. A collapse is not a second read.
      if (opening) this._logInteraction(item, 'expand');
    },

    recordClick(item) {
      this._logInteraction(item, 'click_through');
    },

    /* Fire-and-forget, deliberately unlike the star and the rating: those are
     * state the user would notice being wrong, so they revert on failure. A
     * dropped analytics event is invisible by nature, and disturbing the UI
     * over one would trade a silent non-problem for a visible one.
     *
     * Signals are logged but unused in ranking (spec 7.3, research finding
     * R10); phase 4 decides whether to trust them. */
    _logInteraction(item, kind) {
      this._json(`/articles/${item.article_id}/interactions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind }),
      }).catch(err => console.debug('interaction', kind, err));
    },

    /* Optimistic, reverting on failure. Clicking the active button clears the
     * rating: the ratings table's CHECK (value IN (-1, 1)) makes neutral
     * unrepresentable, so an un-clearable misclick would be permanent — and a
     * permanent misclick is exactly the bad signal that teaches the taste
     * profile the wrong thing. */
    async rate(item, value) {
      const previous = item.rating;
      const next = previous === value ? null : value;
      item.rating = next;

      const path = `/articles/${item.article_id}/rating`;
      const opts = next === null
        ? { method: 'DELETE' }
        : {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ value: next }),
          };
      try {
        await this._json(path, opts);
      } catch (err) {
        console.error('rate', err);
        item.rating = previous;
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

    _refreshCounts() {
      const seq = this._filterSeq;
      const scope = this.tab === 'saved' ? 'saved' : 'feed';
      return this._json(`/categories/?scope=${scope}${this._categoryQuery()}`)
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
          // A missing key must render an unstarred card, never `undefined`:
          // arriving articles are never auto-saved, so the emitter has no
          // reason to send `saved` and the client must not assume it.
          const normalized = {
            score: null, rating: null, badges: [], categories: [],
            saved: false, ...item,
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
