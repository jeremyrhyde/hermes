export type Mode = 'feed' | 'saved';
export type RatingValue = -1 | 1 | null;

export interface SourceRef { id: string; name: string; type: string }

export interface FeedItem {
  article_id: number;
  headline: string;
  bullets: string[];
  url: string;
  published_at: string | null;
  source: SourceRef;
  score: number | null;
  rating: RatingValue;
  badges: string[];
  categories: string[];
  saved: boolean;
}

/** One withheld group, normalized. `high`/`low` are null for an empty group (and always for `unscored`). */
export interface GateGroup { count: number; high: number | null; low: number | null }

export interface Gating {
  cutoff: number;
  max_displayed: number;
  total: number;
  above_cutoff: GateGroup;
  below_cutoff: GateGroup;
  unscored: GateGroup;
}

/** GET api/ranked/ as sent. `unscored` carries only a count; normalizeGating
 *  (lib/feed.ts) fills the rest so templates never meet `undefined`. */
export interface Ranked extends Omit<Gating, 'unscored'> {
  displayed: FeedItem[];
  unscored: Pick<GateGroup, 'count'>;
}

export interface CategoryFilter { category: string; count: number; selected: boolean }
export interface Categories { selected: string[]; filters: CategoryFilter[] }

export interface Source {
  id: string;
  name: string;
  type: string;
  enabled: boolean;
  error_count: number;
  unusable_count: number;
  disabled: boolean;
  disabled_until: string | null;
  last_polled_at: string | null;
  next_poll_at: string | null;
}

export interface StartupFailure { component: string; error: string; severity?: string }
export interface Health { status: string; startup_failures: StartupFailure[] }

export type KnobKey = 'score_cutoff' | 'max_displayed' | 'distill_threshold';
export type Preferences = Record<KnobKey, number>;

export interface Profile { version: string | null; body: string; kind: string | null }
export interface Proposal { version: string; body: string; created_at: string }
export type ReviewState = 'insufficient' | 'ready' | 'pending';
export interface Review { state: ReviewState; count: number; threshold: number; proposal: Proposal | null }
export interface Approval { version: string; rescored: number }

export interface LiveEvent { type: string; subject?: string | null; data?: Record<string, unknown> }
