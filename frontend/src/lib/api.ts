import type {
  Approval, Categories, FeedItem, Health, KnobKey, Mode, Preferences, Profile, Ranked, Review, Source,
} from './types';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

export function detailOf(body: unknown): string | null {
  if (!body || typeof body !== 'object' || !('detail' in body)) return null;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const item = d as { loc?: unknown; msg?: string };
        const field = Array.isArray(item.loc) ? item.loc[item.loc.length - 1] : undefined;
        const msg = item.msg ?? String(d);
        return typeof field === 'string' ? `${field}: ${msg}` : msg;
      })
      .join('; ');
  }
  return null;
}

async function request<T>(method: string, path: string, body?: unknown, base = 'api'): Promise<T> {
  let res: Response;
  try {
    // Relative to the page (no leading slash), so it works at :8002/ and
    // under Pantheon's /hermes/.
    res = await fetch(`${base}${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the Hermes server");
  }
  if (res.status === 204) return undefined as T;
  const data: unknown = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, detailOf(data) ?? (res.statusText || `HTTP ${res.status}`));
  return data as T;
}

/** Repeated `category=` pairs: the API ANDs them. */
function query(pairs: [string, string][]): string {
  return pairs.length ? `?${new URLSearchParams(pairs)}` : '';
}
const cats = (categories: string[]): [string, string][] => categories.map((c) => ['category', c]);

export const api = {
  health: () => request<Health>('GET', '/health', undefined, '.'),

  ranked: (categories: string[]) => request<Ranked>('GET', `/ranked/${query(cats(categories))}`),
  saved: (categories: string[], limit = 100) =>
    request<FeedItem[]>('GET', `/saved/${query([['limit', String(limit)], ...cats(categories)])}`),
  categories: (scope: Mode, categories: string[]) =>
    request<Categories>('GET', `/categories/${query([['scope', scope], ...cats(categories)])}`),

  save: (id: number) => request<void>('POST', `/saved/${id}`),
  unsave: (id: number) => request<void>('DELETE', `/saved/${id}`),
  rate: (id: number, value: -1 | 1) => request<void>('PUT', `/articles/${id}/rating`, { value }),
  clearRating: (id: number) => request<void>('DELETE', `/articles/${id}/rating`),
  interaction: (id: number, kind: 'expand' | 'click_through') =>
    request<void>('POST', `/articles/${id}/interactions`, { kind }),

  sources: () => request<Source[]>('GET', '/sources/'),
  pollSource: (id: string) => request<{ polled: string }>('POST', `/sources/${encodeURIComponent(id)}/poll`),

  preferences: () => request<Preferences>('GET', '/preferences/'),
  setPreference: (key: KnobKey, value: number) => request<void>('PUT', `/preferences/${key}`, { value }),

  profile: () => request<Profile>('GET', '/profile/'),
  saveProfile: (body: string) => request<{ version: string }>('PUT', '/profile/', { body }),
  review: () => request<Review>('GET', '/profile/review'),
  propose: () => request<{ version: string }>('POST', '/profile/review'),
  approve: (version: string, body: string, rescore: boolean) =>
    request<Approval>('POST', `/profile/review/${encodeURIComponent(version)}/approve`, { body, rescore }),
  reject: (version: string) => request<void>('POST', `/profile/review/${encodeURIComponent(version)}/reject`),
};
