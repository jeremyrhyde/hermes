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
    knobDraft: { score_cutoff: 0, max_displayed: 50, distill_threshold: 20 },
    // Preference key -> the field /ranked/ reports it under. The cutoff has two
    // spellings across the two endpoints — `score_cutoff` is a preferences key
    // among others, `cutoff` is unambiguous inside a ranking — and this is the
    // one place they meet. Reading `gating[key]` directly instead yields
    // `undefined`, which as an input value clears the box rather than
    // reverting it.
    //
    // `distill_threshold` is deliberately absent: it gates a review cadence, not
    // a ranking, so /ranked/ has nothing to say about it and `prefs` below is
    // where its effective value is read back from. See _effective.
    _knobField: { score_cutoff: 'cutoff', max_displayed: 'max_displayed' },
    // The knobs /ranked/ does not report. Seeded with the server's own default
    // so the slider renders somewhere sensible before Settings is ever opened.
    prefs: { distill_threshold: 20 },
    /* The three sliders in Settings, rendered by one x-for over this list.
     *
     * One descriptor per knob rather than three near-identical blocks of
     * markup: with no component system the third block is a copy of the first
     * two, and the copy is what stops getting updated. Everything that differs
     * between them lives here — id, label, range, unit, help — so the template
     * stays a straight render with no per-knob branch in it. Adding a fourth
     * knob is a row here and nothing else.
     *
     * `unit` is always a string and is concatenated unconditionally; an empty
     * one is what keeps the template free of a conditional. `help` is plain
     * text because it is rendered with x-text — markup in it would be escaped
     * and shown literally.
     *
     * The value in effect is read through _effective(), not from a field named
     * here, because the two gating knobs come back from /ranked/ and the
     * cadence from /preferences/. See _effective and _knobField.
     *
     * Kept in step with the markup in index.html's Settings panel — the loop
     * there renders these keys and nothing else. */
    knobs: [
      {
        key: 'score_cutoff',
        id: 'knob-cutoff',
        label: 'Score cutoff',
        min: 0,
        max: 100,
        unit: '',
        help: 'Articles scoring below this are withheld from the feed and '
            + 'collapsed into a count. Leave it at 0, which shows everything, '
            + 'until the scores look right.',
      },
      {
        key: 'max_displayed',
        id: 'knob-max',
        label: 'Most articles shown',
        min: 1,
        max: 200,
        unit: '',
        help: 'A hard cap on the list, whatever the cutoff allows. Anything '
            + 'past it is counted as overflow rather than dropped.',
      },
      {
        // The cadence of the review loop, not of the gate — but it is a number
        // you can turn, and they all belong in one place.
        key: 'distill_threshold',
        id: 'knob-threshold',
        label: 'Re-evaluate every',
        min: 5,
        max: 200,
        unit: ' ratings',
        help: 'How many ratings to gather before Hermes offers to revise your '
            + 'taste profile. Clearing a rating takes it back out of the '
            + 'count, so the tally below can fall as well as rise.',
      },
    ],
    // The live profile, in the shape GET /profile/ returns for "there is none":
    // an absent profile is where a reader starts, not an error, and `version`
    // being null is what the label reads off.
    profile: { version: null, body: '', kind: null },
    // GET /profile/review, normalized. `state` is one of insufficient | ready |
    // pending and drives three mutually exclusive panels — see reviewPanel for
    // why it is never read raw. `proposal` is null in two of those three states,
    // which is why nothing interpolates it directly.
    review: { state: 'insufficient', count: 0, threshold: 20, proposal: null },
    // What the two textareas were last *filled* with, as opposed to what the
    // server last said. They are separate state because `:value` is not the
    // "only writes when the rendered string changes" guard it looks like:
    // Alpine re-runs a bind whenever any dependency of the expression changes,
    // and refetching replaces `profile` and `review` wholesale, so binding
    // straight to `profile.body` re-wrote the element on every poll and threw
    // away whatever was half-typed in it. These move only when a *different*
    // profile or proposal arrives — a new version — which is the one time
    // refilling the box is what the reader wants.
    profileSeed: '',
    proposalSeed: '',
    // Whether GET /profile/ has answered at least once this session.
    //
    // The seed rule above protects every refetch *except* the first: `version`
    // starts null, so the first real response always counts as a new version
    // and always refills the box. Type into the empty textarea inside that
    // window — it is open from the moment Settings is tapped — and the text is
    // gone, which is the same bug as the one the seeds fixed, just narrowed to
    // the one moment it is guaranteed rather than possible.
    //
    // Closed by disabling the field rather than by suppressing the refill: a
    // genuinely new version must still win, because editing against a profile
    // that is no longer live is worse than losing a few characters. Preventing
    // the typing is the only fix that keeps both.
    profileLoaded: false,
    // Whether an approval should also clear existing scores. Default off, like
    // the API's own: re-scoring is the most expensive thing this UI can ask for
    // — a model call per article — and a box already ticked when the panel
    // appears is closer to forcing it than to offering it.
    rescore: false,
    // In-flight flags, one per slow action, so each button can disable and
    // relabel itself without a second element flickering in beside it.
    generating: false,
    savingProfile: false,
    resolving: false,
    // Server-side rejections and confirmations, kept apart from knobError so a
    // failed save cannot blank the message about a knob and vice versa.
    profileError: '',
    profileNotice: '',
    reviewError: '',
    reviewNotice: '',
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
        this._json('api/sources/'),
        this._json('health'),
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
        ? `api/saved/?limit=100${this._categoryQuery()}`
        : `api/ranked/${this._categoryQuery('?')}`;
      const requests = [
        this._json(listPath),
        this._json(`api/categories/?scope=${saved ? 'saved' : 'feed'}${this._categoryQuery()}`),
      ];
      // Unfiltered and capped at one row: this asks "is anything pinned at
      // all", which the filtered list above cannot answer.
      if (saved) requests.push(this._json('api/saved/?limit=1'));

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
        // Spread rather than replace: distill_threshold is a draft too, and
        // /ranked/ knows nothing about it. Rebuilding the object from these two
        // keys alone would leave its label reading "undefined ratings".
        this.knobDraft = {
          ...this.knobDraft,
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

    /* Everything the Settings tab's lower half draws: the knobs /ranked/ does
     * not report, the live profile, and where the review loop stands.
     *
     * Reads _filterSeq without bumping it, the same way _refreshCounts does.
     * Bumping would cancel an in-flight refreshFiltered — the two run side by
     * side on a tab switch — while *capturing* it is what makes a knob write or
     * a tab switch mid-flight discard this paint. That is the whole point of the
     * guard here: generating a proposal is a slow request, and this is the call
     * that lands after it.
     *
     * Every response is folded into its state in a single assignment. Three
     * mutually exclusive panels hang off `review.state`, and an intervening
     * write that left all three false — even for one frame — would leave the
     * one that came back permanently hidden. See the note in refreshFiltered on
     * why Alpine cannot recover from that.
     *
     * Notices are deliberately untouched: this runs immediately after an
     * approve or a save, and clearing them here would wipe the confirmation
     * before it was read. */
    async refreshProfile() {
      const seq = this._filterSeq;
      const [prefs, profile, review] = await Promise.allSettled([
        this._json('api/preferences/'),
        this._json('api/profile/'),
        this._json('api/profile/review'),
      ]);
      if (seq !== this._filterSeq) return;  // superseded — a knob moved, or a tab

      if (prefs.status === 'fulfilled') {
        const threshold = prefs.value?.distill_threshold;
        if (Number.isInteger(threshold)) {
          this.prefs = { distill_threshold: threshold };
          // Re-anchor the label to what the server applied, for the reason
          // refreshFiltered re-anchors the other two.
          this.knobDraft = { ...this.knobDraft, distill_threshold: threshold };
        }
      } else {
        console.error('refreshProfile: /preferences/', prefs.reason);
      }

      if (profile.status === 'fulfilled') {
        const p = profile.value || {};
        const next = {
          version: p.version ?? null,
          body: p.body ?? '',
          kind: p.kind ?? null,
        };
        // Refill the box only when a different version arrived. Comparing the
        // *version* rather than the body is what makes a poll mid-sentence
        // harmless: re-seeding with identical text is still a write, and a
        // write is what discards the edit.
        if (next.version !== this.profile.version) this.profileSeed = next.body;
        this.profile = next;
        // The first response is the one refill that is guaranteed to happen:
        // `version` starts null, so anything real differs from it. That is
        // correct — the box has to be filled from somewhere — but it means the
        // window before this line is the one moment typing is certain to be
        // thrown away, which is why the textarea stays disabled until here
        // rather than being made safe afterwards. See profileLoaded.
        this.profileLoaded = true;
      } else {
        console.error('refreshProfile: /profile/', profile.reason);
        // Unlocked on failure too. There is nothing to overwrite if the fetch
        // never landed, and a box that stays disabled because the network
        // blipped is a Settings tab the reader cannot use at all — a worse
        // outcome than the edit this flag exists to protect.
        this.profileLoaded = true;
      }

      if (review.status === 'fulfilled') {
        const r = review.value || {};
        const next = {
          state: r.state ?? 'insufficient',
          count: r.count ?? 0,
          threshold: r.threshold ?? this.prefs.distill_threshold,
          proposal: r.proposal ?? null,
        };
        // Same rule for the proposal, which is edited in place the same way.
        // `?? null` on both sides: a proposal appearing or being resolved is a
        // version change too, and undefined would compare unequal to itself.
        if ((next.proposal?.version ?? null) !== (this.review.proposal?.version ?? null)) {
          this.proposalSeed = next.proposal?.body ?? '';
        }
        this.review = next;
      } else {
        console.error('refreshProfile: /profile/review', review.reason);
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

    /* The boot report, split by severity and pre-shaped into banners.
     *
     * Two groups rather than two copies of the banner markup, and rendered from
     * one x-for for the reason the card template is shared: the copy that gets
     * forgotten is the second one.
     *
     * A missing `severity` counts as an error. Every entry the server has ever
     * written except the profile advisory is a real failure, so that is the safe
     * reading of a payload from a server that predates the field. */
    get bootGroups() {
      const advisory = [];
      const errors = [];
      for (const f of this.bootFailures) {
        ((f?.severity === 'advisory') ? advisory : errors).push(f);
      }
      const groups = [];
      if (errors.length > 0) {
        groups.push({
          key: 'error',
          advisory: false,
          role: 'alert',
          headline: `${errors.length} component(s) failed to start`,
          entries: errors,
        });
      }
      if (advisory.length > 0) {
        groups.push({
          key: 'advisory',
          advisory: true,
          role: 'status',
          headline: advisory.length === 1
            ? '1 component is waiting to start'
            : `${advisory.length} components are waiting to start`,
          entries: advisory,
        });
      }
      return groups;
    },

    /* Which of the three review panels is on screen.
     *
     * Normalized rather than read off `review.state` in three templates, and
     * that is not tidiness: an unrecognized state — a key the server stopped
     * sending, a response that failed to parse — would make all three
     * expressions false at once, and an x-show that goes false and true again
     * inside one animation frame stays hidden forever (see refreshFiltered).
     * Falling back to `insufficient` guarantees exactly one panel is true on
     * every evaluation, so the panels can only ever swap, never all blank. */
    get reviewPanel() {
      const state = this.review.state;
      return (state === 'ready' || state === 'pending') ? state : 'insufficient';
    },

    /* The proposal's version, blank when there is no proposal.
     *
     * `proposal` is null in two of the three states, and x-show evaluates a
     * hidden panel's bindings too — so `review.proposal.version` in the
     * template would throw on every render outside `pending`, not merely
     * render badly. */
    get proposalVersion() {
      return this.review.proposal?.version ?? '';
    },

    /* The live profile's version, as a label. Reads "none yet" rather than
     * blank when there is none: an empty slot beside a heading looks like
     * something failed to load. */
    get profileLabel() {
      if (!this.profile.version) return 'none yet';
      return this.profile.kind
        ? `${this.profile.version} · ${this.profile.kind}`
        : this.profile.version;
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
        // The code itself, not just its text in the message. A 409 on generating
        // a proposal is recoverable by refetching where the others are not, and
        // parsing that back out of `${path} ${status}` would be a second, worse
        // copy of what the response already said.
        err.status = res.status;
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
      // Retire whatever is in flight. Both of these leave the tab they were on,
      // so any response still coming describes a panel the reader is no longer
      // looking at — and refreshProfile *captures* the sequence rather than
      // bumping it, so without this two of them can be outstanding at once with
      // the same generation and both allowed to paint. Reaching that needs a
      // `profile_proposed` event immediately followed by a tap on Settings, and
      // the two would paint the same three endpoints milliseconds apart today;
      // it is a guard against them diverging later, not against a visible bug.
      this._filterSeq++;
      if (name === 'sources') {
        this.tab = name;
        return;
      }
      // Settings flips first and loads after, which is safe here where it was
      // not for the lists: nothing in the panel is shown by "the data arrived"
      // — reviewPanel always names exactly one of three, before and after — so
      // there is no expression that can go false and then true again.
      if (name === 'settings') {
        this.tab = name;
        return this.refreshProfile();
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
        await this._json(`api/saved/${item.article_id}`, {
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
      const effective = this._effective(key);
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
        await this._json(`api/preferences/${key}`, {
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
        // Alpine cannot be relied on to correct that afterwards, and the reason
        // is not the one this comment used to give. It claimed `:value` writes
        // to the DOM only when its expression changes; it does not. Alpine
        // re-runs a bind whenever any *dependency* of the expression changes
        // and writes the result to the element regardless of whether the
        // rendered string moved. What is true is the converse: nothing here
        // changed, so no dependency changed, so no bind re-runs and the box
        // keeps the number the user let go of. Hence the explicit write.
        //
        // The distinction is not academic. These two knobs survive the wrong
        // rule only because the value Alpine rewrites already equals the one in
        // the box; a `:value` over anything the user types — the profile
        // textareas — is rewritten out from under them on every refetch, which
        // is exactly the bug the seeds in `profileSeed`/`proposalSeed` exist to
        // avoid. Do not reason from the old claim.
        //
        // The draft goes back with it. The slider reverts via `el.value` while
        // the number beside it is read from `knobDraft`, so restoring only one
        // leaves the label showing the abandoned value over a slider that has
        // already snapped back — the exact disagreement the note on knobDraft
        // above says is worse than either being wrong alone.
        this.knobDraft[key] = this._effective(key);
        el.value = this._effective(key);
        return;
      }
      // The threshold changes nothing about the ranking and everything about
      // the review panel — the same count can be short of one threshold and
      // past the next — so it repaints the panel rather than the feed.
      if (key === 'distill_threshold') return this.refreshProfile();
      return this.refreshFiltered();
    },

    /* What the server currently has in effect for a knob.
     *
     * Two sources because the two gating knobs are read back from /ranked/,
     * which reports the gate it actually applied, while the review cadence has
     * no place in a ranking and comes from /preferences/. Reading the wrong one
     * yields `undefined`, which as an input value clears the control rather
     * than reverting it. */
    _effective(key) {
      const field = this._knobField[key];
      return field ? this.gating[field] : this.prefs[key];
    },

    /* Replace the taste profile with whatever is in the textarea.
     *
     * *el* is the element, not its value, because the textarea is deliberately
     * uncontrolled: `:value` writes to the DOM only when the bound expression
     * changes, so a background refetch that returns the same body leaves what
     * the reader is typing alone. x-model would bind the other way and a poll
     * mid-sentence would discard the sentence.
     *
     * Blank is rejected here as well as by the server. The server's 400 is the
     * real guard; this one exists so the answer arrives before the round trip
     * and reads as a sentence rather than an API detail. */
    async saveProfile(el) {
      const body = el.value;
      this.profileError = '';
      this.profileNotice = '';
      if (!body.trim()) {
        this.profileError = 'A profile cannot be empty — scoring would judge '
          + 'every article against nothing.';
        return;
      }
      // Everything in flight described the profile this is replacing, exactly
      // as in setKnob: a review panel resolving after this write would describe
      // a proposal against the old text.
      this._filterSeq++;
      const seq = this._filterSeq;
      this.savingProfile = true;
      let version;
      try {
        const res = await this._json('api/profile/', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ body }),
        });
        version = res?.version;
      } catch (err) {
        console.error('saveProfile', err);
        this.savingProfile = false;
        if (seq !== this._filterSeq) return;
        this.profileError = err.detail || 'Could not save the profile.';
        return;
      }
      this.savingProfile = false;
      if (seq !== this._filterSeq) return;
      // "On the next poll", not "now": the pipeline reads the live profile once
      // per source per run, so nothing already on screen re-ranks until then.
      // Leaving that out is how a reader saves, sees identical scores, and
      // concludes the edit did not take.
      this.profileNotice = version
        ? `Saved as ${version}. Scoring uses it from each source's next poll — `
          + 'scores already on screen do not move until then.'
        : 'Saved.';
      return this.refreshProfile();
    },

    /* Ask the distiller for a revised profile. Slow — a model call over the
     * whole rated corpus — which is what makes the seq guard load-bearing
     * rather than defensive: a knob change or a tab switch while this is out
     * must not paint a panel describing the state it left.
     *
     * A 409 now means two things: a proposal already existed, or one appeared
     * while this request was waiting on the model. Both carry the same detail,
     * so this does not claim to know which — it refetches and says the panel is
     * current, which is true either way. */
    async generateProposal() {
      const seq = this._filterSeq;
      this.reviewError = '';
      this.reviewNotice = '';
      this.generating = true;
      let conflict = false;
      try {
        await this._json('api/profile/review', { method: 'POST' });
      } catch (err) {
        console.error('generateProposal', err);
        // Cleared before the guard, and on every path: this flag is the
        // button's own progress, not painted state. Leaving it set on a
        // superseded response would disable the button for the rest of the
        // session.
        this.generating = false;
        if (seq !== this._filterSeq) return;
        if (err.status !== 409) {
          this.reviewError = err.detail || 'Could not propose a profile.';
          return;
        }
        conflict = true;
      }
      this.generating = false;
      if (seq !== this._filterSeq) return;
      if (conflict) {
        this.reviewError = 'A proposal was already waiting — the panel below '
          + 'is up to date.';
      }
      return this.refreshProfile();
    },

    /* Approve the proposal, sending whatever is in the box.
     *
     * The body always goes with it rather than only when edited: the server
     * treats a supplied body as the amendment and an absent one as "as
     * proposed", and deciding which by comparing strings here would turn a
     * trailing newline into a different profile. */
    async approveProposal(el) {
      const version = this.proposalVersion;
      if (!version) return;
      const body = el.value;
      this.reviewError = '';
      this.reviewNotice = '';
      if (!body.trim()) {
        this.reviewError = 'A profile cannot be empty — clear the edit or '
          + 'reject the proposal instead.';
        return;
      }
      const rescore = this.rescore;
      const result = await this._resolve(
        `api/profile/review/${encodeURIComponent(version)}/approve`,
        { body, rescore },
        'Could not approve the proposal.',
      );
      if (result === undefined) return;

      // "Queued for re-scoring", never "pending": `rescored` counts the
      // articles whose existing score was cleared, and an article still waiting
      // on its first score is not among them. Calling it a pending count would
      // name a larger set than the one that moved.
      let notice = `Approved as ${version}. Scoring uses it from each source's `
        + 'next poll — scores already on screen do not move until then.';
      if (rescore && result?.rescored > 0) {
        notice += ` ${result.rescored} article(s) queued for re-scoring; the `
          + 'pipeline works through 50 per source per poll, so the feed ranks '
          + 'old and new scores against each other until it catches up.';
      } else if (rescore) {
        notice += ' Nothing had a score to clear.';
      }
      this.reviewNotice = notice;
      // Back to off for the next proposal. Left alone it is sticky for the rest
      // of the session, so the second approval of a session would arrive with
      // the box already ticked — the thing the default being off exists to
      // prevent, arrived at by a different route.
      this.rescore = false;
      return this.refreshProfile();
    },

    /* Discard the proposal. The live profile is untouched — and the rating
     * count still restarts, which the message says so the reader is not left
     * expecting the same proposal back on the next rating. */
    async rejectProposal() {
      const version = this.proposalVersion;
      if (!version) return;
      this.reviewError = '';
      this.reviewNotice = '';
      const result = await this._resolve(
        `api/profile/review/${encodeURIComponent(version)}/reject`,
        null,
        'Could not reject the proposal.',
      );
      if (result === undefined) return;
      this.reviewNotice = 'Proposal discarded. Your profile is unchanged, and '
        + 'the rating count starts again from here.';
      return this.refreshProfile();
    },

    /* Approve and reject differ only in their payload and their message, so the
     * seq guard, the stale-panel 409 and the error wording live once.
     *
     * Returns the parsed body on success and `undefined` on every path that
     * already handled itself — a superseded response, a failure, or a stale
     * panel — which is what the callers branch on. `null` is a success: reject
     * answers 204. */
    async _resolve(path, payload, failureMessage) {
      this._filterSeq++;
      const seq = this._filterSeq;
      this.resolving = true;
      const opts = payload === null
        ? { method: 'POST' }
        : {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          };
      try {
        const result = await this._json(path, opts);
        this.resolving = false;
        if (seq !== this._filterSeq) return undefined;
        return result;
      } catch (err) {
        console.error('resolve', path, err);
        this.resolving = false;
        if (seq !== this._filterSeq) return undefined;
        if (err.status === 409) {
          // Someone else — another tab, a double tap — already settled it.
          this.reviewError = 'That proposal was already approved or rejected. '
            + 'The panel below is now current.';
          this.refreshProfile();
          return undefined;
        }
        this.reviewError = err.detail || failureMessage;
        return undefined;
      }
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
      this._json(`api/articles/${item.article_id}/interactions`, {
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

      const path = `api/articles/${item.article_id}/rating`;
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
      // Relative to the page, so it works standalone and under /hermes/.
      const url = new URL('api/ws', document.baseURI);
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
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
      return this._json(`api/categories/?scope=${scope}${this._categoryQuery()}`)
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
          this._json('api/sources/').then(rows => { this.sources = rows || []; })
            .catch(err => console.error('sources refresh', err));
          break;

        case 'system_ready':
          // Server (re)started — re-pull anything cached from REST.
          this.refreshAll();
          break;

        case 'pipeline_error':
          console.warn('pipeline_error', event.data);
          break;

        case 'profile_proposed':
          // Only while the panel it describes is on screen. Elsewhere it would
          // be three requests for state nothing is rendering, and the panel
          // reloads on every switch to Settings anyway.
          if (this.tab === 'settings') this.refreshProfile();
          break;

        case 'article_ingested':
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
