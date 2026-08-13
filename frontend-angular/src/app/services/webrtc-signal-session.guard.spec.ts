import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  SIGNAL_SESSION_RECREATION_REQUIRED,
  WebrtcSignalSessionGuard,
} from './webrtc-signal-session.guard';

describe('WebrtcSignalSessionGuard epoch scoping', () => {
  beforeEach(() => sessionStorage.clear());
  afterEach(() => sessionStorage.clear());

  it('blocks only the exact public signaling epoch', () => {
    const guard = new WebrtcSignalSessionGuard();
    guard.block('session-a', 2);

    expect(() => guard.assertReusable('session-a', 2))
      .toThrow(SIGNAL_SESSION_RECREATION_REQUIRED);
    expect(() => guard.assertReusable('session-a', 3)).not.toThrow();
    expect(() => guard.assertReusable('session-b', 2)).not.toThrow();
    expect(() => guard.assertReusable('session-a')).not.toThrow();
  });

  it('persists exact epochs and clears all of them on retirement', () => {
    const first = new WebrtcSignalSessionGuard();
    first.block('session-a', 2);
    first.block('session-a', 3);
    first.block('session-b', 2);

    const restored = new WebrtcSignalSessionGuard();
    expect(restored.isBlocked('session-a', 2)).toBe(true);
    expect(restored.isBlocked('session-a', 3)).toBe(true);

    restored.clearSession('session-a');

    expect(restored.isBlocked('session-a', 2)).toBe(false);
    expect(restored.isBlocked('session-a', 3)).toBe(false);
    expect(restored.isBlocked('session-b', 2)).toBe(true);
  });

  it('honors a pre-epoch public latch conservatively until retirement', () => {
    const guard = new WebrtcSignalSessionGuard();
    guard.block('session-a');

    expect(guard.isBlocked('session-a', 2)).toBe(true);
    expect(guard.isBlocked('session-a', 3)).toBe(true);

    guard.clearSession('session-a');
    expect(guard.isBlocked('session-a', 2)).toBe(false);
  });
});
