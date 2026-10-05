import { api, ApiError } from './api';
import { Sequencer } from './feed';
import { feed } from './feed.svelte';
import {
  ALREADY_PENDING_ERROR, approveNotice, EMPTY_APPROVAL_ERROR, EMPTY_PROFILE_ERROR, knobCommit, REJECT_NOTICE,
  saveNotice, STALE_PROPOSAL_ERROR,
} from './settings';
import { toast } from './toast.svelte';
import type { KnobKey, Preferences, Profile, Review } from './types';

const SERVER_DEFAULTS: Preferences = { score_cutoff: 0, max_displayed: 50, distill_threshold: 20 };

interface SettingsState {
  /** Effective knob values from GET api/preferences/; null until loaded. */
  prefs: Preferences | null;
  /** What each slider shows while dragging; re-synced from `prefs` on every load. */
  drafts: Preferences;
  knobError: string;
  profile: Profile;
  profileLoaded: boolean;
  savingProfile: boolean;
  profileError: string;
  review: Review;
  rescore: boolean;
  generating: boolean;
  resolving: boolean;
  reviewError: string;
}

export const settings: SettingsState = $state({
  prefs: null, drafts: { ...SERVER_DEFAULTS }, knobError: '',
  profile: { version: null, body: '', kind: null }, profileLoaded: false, savingProfile: false, profileError: '',
  review: { state: 'insufficient', count: 0, threshold: 20, proposal: null },
  rescore: false, generating: false, resolving: false, reviewError: '',
});

// Any write supersedes an in-flight refresh, so a late GET can't paint over it.
const seq = new Sequencer();
const message = (err: unknown, fallback: string) => (err instanceof Error && err.message ? err.message : fallback);

export async function refreshSettings(): Promise<void> {
  const id = seq.next();
  const [prefs, profile, review] = await Promise.allSettled([api.preferences(), api.profile(), api.review()]);
  if (!seq.isCurrent(id)) return;
  if (prefs.status === 'fulfilled') {
    settings.prefs = { ...SERVER_DEFAULTS, ...prefs.value };
    settings.drafts = { ...settings.prefs };
  } else {
    console.error('settings: preferences', prefs.reason);
  }
  if (profile.status === 'fulfilled') {
    const p = profile.value ?? ({} as Partial<Profile>);
    settings.profile = { version: p.version ?? null, body: p.body ?? '', kind: p.kind ?? null };
  } else {
    console.error('settings: profile', profile.reason);
  }
  settings.profileLoaded = true;
  if (review.status === 'fulfilled') {
    const r = review.value ?? ({} as Partial<Review>);
    settings.review = {
      state: r.state ?? 'insufficient',
      count: r.count ?? 0,
      threshold: r.threshold ?? settings.prefs?.distill_threshold ?? SERVER_DEFAULTS.distill_threshold,
      proposal: r.proposal ?? null,
    };
  } else {
    console.error('settings: review', review.reason);
  }
}

/** Slider released. A rejection springs the slider back and shows the server's reason. */
export async function commitKnob(key: KnobKey, raw: string): Promise<void> {
  const effective = settings.prefs?.[key] ?? SERVER_DEFAULTS[key];
  settings.knobError = '';
  const value = knobCommit(raw, effective);
  if (value === null) {
    settings.drafts[key] = effective;
    return;
  }
  seq.next();
  try {
    await api.setPreference(key, value);
  } catch (err) {
    settings.knobError = message(err, 'Could not save that setting.');
    settings.drafts[key] = effective;
    return;
  }
  if (settings.prefs) settings.prefs[key] = value;
  if (key === 'distill_threshold') await refreshSettings();
  else feed.stale = true; // the gate moved; the feed reloads when you go back to it
}

export async function saveProfile(body: string): Promise<void> {
  settings.profileError = '';
  if (!body.trim()) {
    settings.profileError = EMPTY_PROFILE_ERROR;
    return;
  }
  const id = seq.next();
  settings.savingProfile = true;
  let version: string | undefined;
  try {
    version = (await api.saveProfile(body))?.version;
  } catch (err) {
    if (seq.isCurrent(id)) settings.profileError = message(err, 'Could not save the profile.');
    return;
  } finally {
    settings.savingProfile = false;
  }
  if (!seq.isCurrent(id)) return;
  toast(saveNotice(version), 'info', { ms: 8000 });
  await refreshSettings();
}

export async function propose(): Promise<void> {
  const id = seq.next();
  settings.reviewError = '';
  settings.generating = true;
  try {
    await api.propose();
  } catch (err) {
    if (!seq.isCurrent(id)) return;
    if (!(err instanceof ApiError && err.status === 409)) {
      settings.reviewError = message(err, 'Could not propose a profile.');
      return;
    }
    settings.reviewError = ALREADY_PENDING_ERROR;
  } finally {
    settings.generating = false;
  }
  if (seq.isCurrent(id)) await refreshSettings();
}

async function resolve<T>(call: () => Promise<T>, failure: string): Promise<{ value: T } | null> {
  const id = seq.next();
  settings.resolving = true;
  settings.reviewError = '';
  try {
    const value = await call();
    return seq.isCurrent(id) ? { value } : null;
  } catch (err) {
    if (!seq.isCurrent(id)) return null;
    if (err instanceof ApiError && err.status === 409) {
      settings.reviewError = STALE_PROPOSAL_ERROR;
      void refreshSettings();
      return null;
    }
    settings.reviewError = message(err, failure);
    return null;
  } finally {
    settings.resolving = false;
  }
}

export async function approve(body: string): Promise<void> {
  const version = settings.review.proposal?.version;
  if (!version) return;
  if (!body.trim()) {
    settings.reviewError = EMPTY_APPROVAL_ERROR;
    return;
  }
  const rescore = settings.rescore;
  const res = await resolve(() => api.approve(version, body, rescore), 'Could not approve the proposal.');
  if (!res) return;
  toast(approveNotice(version, rescore, res.value?.rescored ?? 0), 'info', { ms: 10000 });
  settings.rescore = false;
  if (rescore) feed.stale = true;
  await refreshSettings();
}

export async function reject(): Promise<void> {
  const version = settings.review.proposal?.version;
  if (!version) return;
  const res = await resolve(() => api.reject(version), 'Could not reject the proposal.');
  if (!res) return;
  toast(REJECT_NOTICE, 'info', { ms: 6000 });
  await refreshSettings();
}
