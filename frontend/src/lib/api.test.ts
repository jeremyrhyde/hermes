import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, detailOf } from './api';

const calls: { url: string; init?: RequestInit }[] = [];

function respond(status: number, body: unknown = null) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return new Response(status === 204 ? null : JSON.stringify(body), { status });
  });
}

beforeEach(() => { calls.length = 0; });
afterEach(() => { vi.unstubAllGlobals(); });

describe('detailOf', () => {
  it('reads a string detail', () => expect(detailOf({ detail: 'nope' })).toBe('nope'));
  it('joins validation details with their field', () =>
    expect(detailOf({ detail: [{ loc: ['body', 'value'], msg: 'bad' }, { msg: 'worse' }] })).toBe('value: bad; worse'));
  it('returns null without a detail', () => expect(detailOf({ other: 1 })).toBeNull());
});

describe('request', () => {
  it('maps a network failure to ApiError(0)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const err = await api.sources().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(0);
    expect((err as ApiError).message).toBe("Can't reach the Hermes server");
  });

  it('surfaces the server detail on a non-2xx', async () => {
    vi.stubGlobal('fetch', respond(400, { detail: 'invalid score_cutoff 900; valid range: 0-100' }));
    const err = (await api.setPreference('score_cutoff', 900).catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(400);
    expect(err.message).toBe('invalid score_cutoff 900; valid range: 0-100');
  });

  it('falls back to the status when the error body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>oops</html>', { status: 502, statusText: 'Bad Gateway' })));
    const err = (await api.sources().catch((e: unknown) => e)) as ApiError;
    expect(err.status).toBe(502);
    expect(err.message).toBe('Bad Gateway');
  });

  it('returns undefined for a 204', async () => {
    vi.stubGlobal('fetch', respond(204));
    await expect(api.save(7)).resolves.toBeUndefined();
  });
});

describe('endpoints', () => {
  beforeEach(() => vi.stubGlobal('fetch', respond(200, {})));

  it('builds every path relative to the page', async () => {
    await Promise.all([
      api.health(), api.ranked(['AI', 'Policy']), api.saved(['AI']), api.saved([], 1),
      api.categories('saved', ['AI']), api.save(1), api.unsave(1), api.rate(1, -1), api.clearRating(1),
      api.interaction(1, 'expand'), api.sources(), api.pollSource('a b'), api.preferences(),
      api.setPreference('max_displayed', 20), api.profile(), api.saveProfile('x'), api.review(),
      api.propose(), api.approve('profile-v2', 'x', true), api.reject('profile-v2'),
    ]);
    for (const { url } of calls) expect(url.startsWith('/')).toBe(false);
    expect(calls.map((c) => c.url)).toEqual([
      './health',
      'api/ranked/?category=AI&category=Policy',
      'api/saved/?limit=100&category=AI',
      'api/saved/?limit=1',
      'api/categories/?scope=saved&category=AI',
      'api/saved/1', 'api/saved/1', 'api/articles/1/rating', 'api/articles/1/rating',
      'api/articles/1/interactions', 'api/sources/', 'api/sources/a%20b/poll', 'api/preferences/',
      'api/preferences/max_displayed', 'api/profile/', 'api/profile/', 'api/profile/review',
      'api/profile/review', 'api/profile/review/profile-v2/approve', 'api/profile/review/profile-v2/reject',
    ]);
  });

  it('sends JSON bodies with the right methods', async () => {
    await api.rate(3, 1);
    await api.approve('v', 'body', false);
    expect(calls[0].init?.method).toBe('PUT');
    expect(calls[0].init?.body).toBe('{"value":1}');
    expect(calls[1].init?.body).toBe('{"body":"body","rescore":false}');
    expect((calls[1].init?.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });
});
