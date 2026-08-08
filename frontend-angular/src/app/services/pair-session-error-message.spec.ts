import { describe, expect, it } from 'vitest';

import { pairSessionErrorMessage } from './pair-session-error-message';

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
});
