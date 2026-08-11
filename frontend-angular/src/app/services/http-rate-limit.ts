const DEFAULT_RETRY_AFTER_MS = 10_000;
// Respect operational windows above the current 60-second defaults. A finite
// ceiling still prevents a malformed trusted response from parking a browser
// task indefinitely.
const MAX_RETRY_AFTER_MS = 24 * 60 * 60 * 1_000;

interface HttpRateLimitError {
  readonly status?: unknown;
  readonly headers?: { get(name: string): string | null };
}

/** Parses the HTTP Retry-After contract without coupling callers to HttpClient. */
export function rateLimitRetryAfterMs(
  error: unknown,
  fallbackMs = DEFAULT_RETRY_AFTER_MS,
): number | null {
  const candidate = error as HttpRateLimitError | null;
  if (Number(candidate?.status) !== 429) return null;
  const raw = candidate?.headers?.get('Retry-After')?.trim() ?? '';
  let delayMs = Number.NaN;
  if (/^\d+(?:\.\d+)?$/.test(raw)) {
    delayMs = Number(raw) * 1_000;
  } else if (raw) {
    const deadline = Date.parse(raw);
    if (Number.isFinite(deadline)) delayMs = deadline - Date.now();
  }
  const resolved = Number.isFinite(delayMs) && delayMs > 0 ? delayMs : fallbackMs;
  return Math.max(250, Math.min(MAX_RETRY_AFTER_MS, Math.ceil(resolved)));
}

export function rateLimitMessage(error: unknown): string | null {
  const delayMs = rateLimitRetryAfterMs(error);
  if (delayMs === null) return null;
  const seconds = Math.max(1, Math.ceil(delayMs / 1_000));
  return `Der Pair-Server ist ausgelastet. Bitte in ${seconds} Sekunden erneut versuchen.`;
}
