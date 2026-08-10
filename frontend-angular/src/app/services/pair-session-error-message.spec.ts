import { describe, expect, it } from 'vitest';

import { pairSessionErrorCode, pairSessionErrorMessage } from './pair-session-error-message';

describe('pairSessionErrorMessage', () => {
  it('renders expired authentication as an actionable German message', () => {
    expect(pairSessionErrorMessage(
      new Error('public_session_authentication_expired'),
      'Erstellen fehlgeschlagen',
    )).toContain('abgelaufen');
  });

  it('preserves unknown error details and uses a fallback for non-errors', () => {
    expect(pairSessionErrorMessage(new Error('specific_failure'), 'Fallback')).toBe('specific_failure');
    expect(pairSessionErrorMessage({}, 'Fallback')).toBe('Fallback');
  });

  it.each([
    ['public_pair_pending_attempt_conflict', 'früherer Beitrittsversuch'],
    ['peer_identity_must_be_distinct', 'nicht mit sich selbst'],
    ['device_key_must_be_distinct', 'eigenes Browserprofil'],
  ])('renders %s as an actionable German conflict', (code, expectedText) => {
    const error = { status: 400, error: { error: code } };
    expect(pairSessionErrorCode(error)).toBe(code);
    expect(pairSessionErrorMessage(error, 'Fallback')).toContain(expectedText);
    expect(pairSessionErrorMessage(error, 'Fallback')).not.toContain(code);
  });
});
