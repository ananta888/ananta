export type PairSessionTerminalReason =
  | 'session_inactive'
  | 'session_not_found'
  | 'session_revoked'
  | 'session_expired'
  | 'local_peer_id_required'
  | 'membership_capability_required'
  | 'membership_capability_invalid'
  | 'membership_capability_retired'
  | 'forbidden';

const TERMINAL_REASONS = new Set<PairSessionTerminalReason>([
  'session_inactive',
  'session_not_found',
  'session_revoked',
  'session_expired',
  'local_peer_id_required',
  'membership_capability_required',
  'membership_capability_invalid',
  'membership_capability_retired',
  'forbidden',
]);

const IDEMPOTENT_RETIREMENT_REASONS = new Set<PairSessionTerminalReason>([
  'session_inactive',
  'session_not_found',
  'session_revoked',
  'session_expired',
]);

/**
 * Classifies only explicit, session-scoped HTTP rejections as terminal.
 *
 * Transport failures, authentication refreshes and 5xx responses remain
 * recoverable. Keeping this policy separate prevents individual polling
 * callers from gradually acquiring different teardown rules.
 */
export function terminalPairSessionReason(error: unknown): PairSessionTerminalReason | null {
  if (!error || typeof error !== 'object' || Array.isArray(error)) return null;
  const response = error as { status?: unknown; error?: unknown };
  const status = Number(response.status);
  if (!Number.isInteger(status) || status < 400 || status >= 500) return null;

  const payload = response.error;
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
  const body = payload as Record<string, unknown>;
  const reason = body['error'] ?? body['reason_code'];
  return isTerminalPairSessionReason(reason) ? reason : null;
}

export function isTerminalPairSessionReason(value: unknown): value is PairSessionTerminalReason {
  return typeof value === 'string' && TERMINAL_REASONS.has(value as PairSessionTerminalReason);
}

/** Reasons which independently prove that the requested active state is gone. */
export function isIdempotentPairSessionRetirementReason(
  value: unknown,
): value is PairSessionTerminalReason {
  return isTerminalPairSessionReason(value) && IDEMPOTENT_RETIREMENT_REASONS.has(value);
}
