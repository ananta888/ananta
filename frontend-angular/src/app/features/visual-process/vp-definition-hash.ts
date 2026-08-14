/** Canonicalize the Hub-owned SHA-256 definition identity used by runtime evidence. */
export function normalizeVpDefinitionHash(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const normalized = value.trim().replace(/^sha256:/i, '').toLowerCase();
  return /^[a-f0-9]{64}$/.test(normalized) ? normalized : null;
}
