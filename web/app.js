/* Hermes web UI — single Alpine.js state object.
 *
 * Architecture:
 *   - REST: fetch() against /ranked/, /saved/, /sources/ and /health
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
    // The Feed tab's list: /ranked/'s `displayed`, already ordered and gated by
    // the server. Never re-sorted or patched here — see applyEvent.
    items: [],
    // Everything /ranked/ says about the gate: the knobs in effect, the size of
    // the whole population, and the three withheld groups. Seeded with the
    // server's own defaults so the header reads sensibly before the first
    // response, and re-normalized on every one — `high`/`low` are null for an
    // empty group, and a group key the server never sent must not become
    // `undefined` in a template.
    gating: {
      cutoff: 0,
      max_displayed: 50,
      total: 0,
      above_cutoff: { count: 0, high: null, low: null },
      below_cutoff: { count: 0, high: null, low: null },
      unscored: { count: 0, high: null, low: null },
    },
    // The server's rejection of a knob write, verbatim. Empty when the knobs
    // are in a state the server accepted.
    knobError: '',
    // What each slider reads *while being dragged*. A range input fires `input`
    // continuously, so writing through on every pixel would mean a PUT and a
    // refetch per pixel; the write waits for `change` (release) instead. This
    // holds the in-between value so the number beside the label still moves
    // with your thumb. Re-synced from the server on every load, so a rejected
    // or superseded drag cannot leave it lying.
    knobDraft: { score_cutoff: 0, max_displayed: 50 },
    // Preference key -> the field /ranked/ reports it under. The cutoff has two
    // spellings across the two endpoints — `score_cutoff` is a preferences key
    // among others, `cutoff` is unambiguous inside a ranking — and this is the
    // one place they meet. Reading `gating[key]` directly instead yields
    // `undefined`, which as an input value clears the box rather than
    // reverting it.
    _knobField: { score_cutoff: 'cutoff', max_displayed: 'max_displayed' },
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
      // The Feed reads /ranked/, which takes no limit: how many articles are
      // displayed is the max_displayed knob's business, and a query parameter
      // beside it would be a second, contradicting answer.
      const listPath = saved
        ? `/saved/?limit=100${this._categoryQuery()}`
        : `/ranked/${this._categoryQuery('?')}`;
      const requests = [
        this._json(listPath),
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
      if (saved) {
        this.savedItems = list.value || [];
      } else {
        const ranked = list.value || {};
        this.items = ranked.displayed || [];
        this.gating = {
          cutoff: ranked.cutoff ?? 0,
          max_displayed: ranked.max_displayed ?? 0,
          total: ranked.total ?? 0,
          above_cutoff: this._group(ranked.above_cutoff),
          below_cutoff: this._group(ranked.below_cutoff),
          unscored: this._group(ranked.unscored),
        };
        // Re-anchor the slider labels to what the server actually applied.
        // Without this a rejected or superseded drag leaves the number showing
        // where the thumb was let go rather than where the gate ended up — and
        // the slider itself would snap back via :value while its own label
        // disagreed, which is worse than either being wrong alone.
        this.knobDraft = {
          score_cutoff: this.gating.cutoff,
          max_displayed: this.gating.max_displayed,
        };
      }

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

    /* Whether the current tab shows a list of articles.
     *
     * Named rather than spelled out as `tab !== 'sources' && tab !== 'settings'`
     * in three templates, because every tab added since has had to remember to
     * exclude itself from all three — and the one that forgot would render an
     * empty article panel under its own content with no obvious cause. */
    get isListTab() {
      return this.tab === 'feed' || this.tab === 'saved';
    },

    /* The withheld groups as rendered rows, empty ones omitted.
     *
     * Built here rather than in three near-identical bits of markup because
     * the null handling is the whole job: an empty group carries
     * high === low === null, and a row that interpolated those would read
     * "(null → null)". A group with a count of 0 has no row at all. */
    get gateRows() {
      const g = this.gating;
      const rows = [];
      if (g.above_cutoff.count > 0) {
        rows.push({
          key: 'above',
          label: `${g.above_cutoff.count} more above cutoff${this._range(g.above_cutoff)}`,
        });
      }
      if (g.below_cutoff.count > 0) {
        rows.push({
          key: 'below',
          label: `${g.below_cutoff.count} below cutoff${this._range(g.below_cutoff)}`,
        });
      }
      if (g.unscored.count > 0) {
        // No range: an unscored article has no score to bound.
        rows.push({ key: 'unscored', label: `${g.unscored.count} not yet scored` });
      }
      return rows;
    },

    // ---------------------------------------------------------------- helpers
    async _json(path, opts = {}) {
      const res = await fetch(path, opts);
      if (!res.ok) {
        const err = new Error(`${path} ${res.status}`);
        // The knob controls show the server's rejection verbatim rather than
        // duplicating its ranges client-side, where the two would drift.
        const body = await res.json().catch(() => null);
        err.detail = body?.detail ?? null;
        throw err;
      }
      // 204 No Content has no body
      if (res.status === 204) return null;
      return res.json();
    },

    /* One withheld group, with every key present. The server always sends
     * count/high/low, but a shape it never sent must still render blank rather
     * than "undefined" — the same defensiveness the WebSocket payloads need. */
    _group(group) {
      return { count: 0, high: null, low: null, ...(group || {}) };
    },

    /* " (74 → 70)" for a group's score range, or '' when it has none. */
    _range(group) {
      const { high, low } = group;
      if (high === null || high === undefined) return '';
      if (low === null || low === undefined) return '';
      return high === low ? ` (${high})` : ` (${high} → ${low})`;
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
      // Neither tab parameterizes a list, so neither fetches one. That also
      // keeps them out of the x-show flap entirely: with no await between the
      // flip and the paint, nothing can evaluate false and then true inside one
      // frame, which is the shape that leaves an element hidden forever.
      if (name === 'sources' || name === 'settings') {
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

    /* Write one gating knob, then repaint from the server.
     *
     * *el* is the input itself, not its value, because the revert has to reach
     * the DOM: a rejected 300 leaves `gating.cutoff` exactly as it was, so
     * nothing about the bound state changes and Alpine has no reason to put the
     * box back — the field would keep showing a number that is not in effect.
     *
     * The new gating is *never* computed here. The cutoff decides which of
     * three groups every article lands in, and re-deriving that client-side is
     * the same duplicated gate the refetch exists to avoid. */
    async setKnob(key, el) {
      const effective = this.gating[this._knobField[key]];
      const raw = el.value.trim();
      const value = Number(raw);
      this.knobError = '';
      // The blank check is not redundant: a number input reports anything it
      // cannot parse as '', and Number('') is 0 — a valid cutoff. Without it,
      // clearing the box or typing letters would quietly drop the cutoff to 0
      // instead of putting back the number that is in effect.
      if (raw === '' || !Number.isInteger(value) || value === effective) {
        el.value = effective;  // blank, junk, or a no-op edit
        return;
      }
      // Anything already in flight described the *old* knob, so retire it here
      // rather than after the PUT: a /ranked/ response that resolves mid-write
      // would otherwise paint a gate the user has already moved off.
      this._filterSeq++;
      try {
        await this._json(`/preferences/${key}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ value }),
        });
      } catch (err) {
        console.error('setKnob', key, err);
        this.knobError = err.detail || 'Could not save that setting.';
        // Re-read rather than reusing `effective`, which was captured before
        // the await. A second writer — another tab or device — can move the
        // cutoff while this PUT is in flight, and restoring the snapshot would
        // leave the box showing a number the gate rows below it disagree with.
        // Alpine cannot correct that afterwards: `:value` only writes to the
        // DOM when its expression changes, and the expression did not.
        el.value = this.gating[this._knobField[key]];
        return;
      }
      return this.refreshFiltered();
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
        // Refetch rather than patch. The feed is ordered and gated by the
        // server, so splicing a card in would mean re-deriving the cutoff, the
        // display cap and the three group counts here — a second copy of the
        // gate, and the one that drifts. It would also place the article
        // wrongly: summarized arrives *before* scored, so the card would be
        // ranked by a score it does not have yet and then never move.
        //
        // On the Saved tab this refetches /saved/ and the counts, which is
        // still right: a newly summarized article changes the category counts
        // even when it changes nothing about what is pinned.
        case 'article_summarized':
        case 'article_scored':
          this.refreshFiltered();
          break;

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
