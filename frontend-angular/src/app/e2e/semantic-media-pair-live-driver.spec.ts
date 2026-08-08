import { Subject } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  SemanticMediaPairProductPorts,
  SemanticPeerHubFixture,
  installSemanticMediaPairLiveDriver,
  openDirectProductPath,
} from './semantic-media-pair-live-driver';

const fixture: SemanticPeerHubFixture = Object.freeze({
  hubUrl: 'https://hub.example.test',
  sessionId: 'session-bound-by-control-plane',
  epoch: 1,
  senderId: 'sender',
  recipientId: 'recipient',
  senderConsentDigest: 'a'.repeat(64),
  recipientConsentDigest: 'b'.repeat(64),
  scopeDigest: 'c'.repeat(64),
  consentVersion: 1,
  expiresAtMs: Date.now() + 60_000,
  transport: 'webrtc',
});

describe('semantic media pair live driver direct binding', () => {
  const semanticMessage$ = new Subject<{ message_id: string }>();
  const transport = {
    semanticMessage$,
    mode$: { value: 'webrtc' },
    open: vi.fn().mockResolvedValue(undefined),
  };
  const signaling = { fallbackToHubRelay: vi.fn() };

  beforeEach(() => {
    localStorage.setItem(
      'ananta.user.token',
      `header.${btoa(JSON.stringify({ sub: fixture.senderId }))}.signature`,
    );
    transport.open.mockClear();
    signaling.fallbackToHubRelay.mockClear();
  });

  it('fails with a setup reason when no explicit Pair binding API is installed', async () => {
    installPorts();

    await expect(openDirectProductPath(fixture, true))
      .rejects.toThrow('semantic_peer_direct_pair_binding_setup_required');
    expect(transport.open).not.toHaveBeenCalled();
    expect(signaling.fallbackToHubRelay).not.toHaveBeenCalled();
  });

  it('opens an explicitly bound session without selecting legacy Hub relay', async () => {
    const assertSessionAvailable = vi.fn();
    installPorts({ assertSessionAvailable });

    await expect(openDirectProductPath(fixture, true)).resolves.toBe('webrtc');

    expect(assertSessionAvailable).toHaveBeenCalledWith(fixture.sessionId);
    expect(transport.open).toHaveBeenCalledWith(fixture.sessionId, true, expect.any(Object));
    expect(signaling.fallbackToHubRelay).not.toHaveBeenCalled();
  });

  function installPorts(
    controlPlane?: { assertSessionAvailable(sessionId: string): void },
  ): void {
    installSemanticMediaPairLiveDriver({
      transport,
      signaling,
      controlPlane,
    } as unknown as SemanticMediaPairProductPorts);
  }
});
