import { describe, expect, it, vi } from 'vitest';

import { rateLimitMessage, rateLimitRetryAfterMs } from './http-rate-limit';

describe('HTTP rate-limit contract', () => {
  it('parses delta-seconds and ignores non-429 responses', () => {
    const headers = { get: vi.fn(() => '7') };
    expect(rateLimitRetryAfterMs({ status: 429, headers })).toBe(7_000);
    expect(rateLimitRetryAfterMs({ status: 503, headers })).toBeNull();
  });

  it('falls back safely when an older server omits Retry-After', () => {
    expect(rateLimitRetryAfterMs({ status: 429 }, 4_000)).toBe(4_000);
    expect(rateLimitMessage({ status: 429 })).toContain('10 Sekunden');
  });
});
