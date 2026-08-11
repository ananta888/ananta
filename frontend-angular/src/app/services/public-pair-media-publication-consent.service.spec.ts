import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PAIR_MEDIA_OUTBOUND_PUBLICATION_GATE } from './pair-media-outbound-publication-gate.port';
import {
  PublicPairMediaPublicationConsentBinding,
  PublicPairMediaPublicationConsentService,
} from './public-pair-media-publication-consent.service';

const BINDING: PublicPairMediaPublicationConsentBinding = Object.freeze({
  sessionId: 'session-a',
  securityEpoch: 7,
  contractDigest: 'a'.repeat(64),
  adapterGeneration: 3,
  localPeerId: 'peer:local',
  remotePeerId: 'peer:remote',
  maxExpiresAtMs: 2_000_000,
});

describe('PublicPairMediaPublicationConsentService', () => {
  let service: PublicPairMediaPublicationConsentService;
  let transforms: { setOutboundPublicationGate: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(1_000_000);
    transforms = { setOutboundPublicationGate: vi.fn(async () => undefined) };
    TestBed.configureTestingModule({ providers: [
      PublicPairMediaPublicationConsentService,
      { provide: PAIR_MEDIA_OUTBOUND_PUBLICATION_GATE, useValue: transforms },
    ] });
    service = TestBed.inject(PublicPairMediaPublicationConsentService);
  });

  afterEach(() => {
    service.bind(null);
    vi.useRealTimers();
    TestBed.resetTestingModule();
  });

  it('binds one exact runtime context and leaves publication disabled', async () => {
    service.bind(BINDING);
    await settle();

    expect(service.snapshot()).toMatchObject({
      status: 'inactive', binding: BINDING, revision: 1,
      expiresAtMs: null, reasonCode: 'public_media_publication_consent_required',
    });
    expect(transforms.setOutboundPublicationGate).toHaveBeenCalledWith(
      'session-a', 3,
      expect.objectContaining({ revision: 1, enabled: false, expiresAtMs: 0 }),
    );

    service.bind({ ...BINDING });
    expect(service.snapshot().revision).toBe(1);
    expect(transforms.setOutboundPublicationGate).toHaveBeenCalledTimes(1);
  });

  it('grants all three slots only after the worker gate ACKs', async () => {
    const gate = deferred<void>();
    transforms.setOutboundPublicationGate.mockResolvedValueOnce(undefined)
      .mockReturnValueOnce(gate.promise);
    service.bind(BINDING);
    await settle();

    const grant = service.grant({ kind: 'timed', durationMs: 900_000 });
    expect(service.snapshot()).toMatchObject({
      status: 'granting', revision: 2, expiresAtMs: 1_900_000,
    });
    expect(service.allows('session-a', 'microphone-opus')).toBe(false);
    expect(transforms.setOutboundPublicationGate).toHaveBeenLastCalledWith(
      'session-a', 3, {
        revision: 2,
        enabled: true,
        slots: ['microphone-opus', 'camera-vp8', 'screen-vp8'],
        expiresAtMs: 1_900_000,
      },
    );

    gate.resolve(undefined);
    await expect(grant).resolves.toMatchObject({ status: 'granted', revision: 2 });
    expect(service.allows('session-a', 'microphone-opus')).toBe(true);
    expect(service.allows('session-a', 'camera-vp8')).toBe(true);
    expect(service.allows('session-a', 'screen-vp8')).toBe(true);
  });

  it('caps a session grant at the exact authority context expiry', async () => {
    service.bind(BINDING);
    await settle();

    await expect(service.grant({ kind: 'session' })).resolves.toMatchObject({
      status: 'granted', expiresAtMs: BINDING.maxExpiresAtMs,
    });
  });

  it('makes revoke non-granted synchronously and keeps the same adapter generation reusable', async () => {
    service.bind(BINDING);
    await settle();
    await service.grant({ kind: 'session' });
    const disable = deferred<void>();
    transforms.setOutboundPublicationGate.mockReturnValueOnce(disable.promise);

    const revoke = service.revoke();
    expect(service.snapshot()).toMatchObject({ status: 'revoking', revision: 3 });
    expect(service.allows('session-a', 'camera-vp8')).toBe(false);
    disable.resolve(undefined);
    await expect(revoke).resolves.toMatchObject({ status: 'revoked', revision: 3 });

    await expect(service.grant({ kind: 'timed', durationMs: 900_000 }))
      .resolves.toMatchObject({ status: 'granted', revision: 4 });
    const enabledCalls = transforms.setOutboundPublicationGate.mock.calls
      .filter(([, generation, value]) => generation === 3 && value.enabled === true);
    expect(enabledCalls).toHaveLength(2);
  });

  it('expires locally before disabling the worker and denies every later publication', async () => {
    service.bind(BINDING);
    await settle();
    await service.grant({ kind: 'timed', durationMs: 900_000 });
    transforms.setOutboundPublicationGate.mockClear();

    await vi.advanceTimersByTimeAsync(900_000);

    expect(service.snapshot()).toMatchObject({
      status: 'expired', revision: 3,
      reasonCode: 'public_media_publication_consent_expired',
    });
    expect(service.allows('session-a', 'screen-vp8')).toBe(false);
    expect(transforms.setOutboundPublicationGate).toHaveBeenCalledWith(
      'session-a', 3,
      expect.objectContaining({ revision: 3, enabled: false, expiresAtMs: 0 }),
    );
  });

  it('fences an old grant ACK after an exact binding replacement', async () => {
    const gate = deferred<void>();
    transforms.setOutboundPublicationGate.mockResolvedValueOnce(undefined)
      .mockReturnValueOnce(gate.promise)
      .mockResolvedValue(undefined);
    service.bind(BINDING);
    await settle();
    const oldGrant = service.grant({ kind: 'session' });

    service.bind({ ...BINDING, adapterGeneration: 4 });
    gate.resolve(undefined);
    await oldGrant;

    expect(service.snapshot()).toMatchObject({
      status: 'inactive', revision: 3,
      binding: { adapterGeneration: 4 },
    });
    expect(service.allows('session-a', 'microphone-opus')).toBe(false);
  });

  it('fails closed when an enabling gate is not acknowledged', async () => {
    transforms.setOutboundPublicationGate.mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('media_e2ee_worker_ack_timeout'));
    service.bind(BINDING);
    await settle();

    await expect(service.grant({ kind: 'session' })).resolves.toMatchObject({
      status: 'failed', reasonCode: 'media_e2ee_worker_ack_timeout',
    });
    expect(service.allows('session-a', 'microphone-opus')).toBe(false);
  });

  it('requires an acknowledged disable reset before retrying a bound failed grant', async () => {
    transforms.setOutboundPublicationGate.mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('media_e2ee_worker_ack_timeout'));
    service.bind(BINDING);
    await settle();

    await expect(service.grant({ kind: 'session' })).resolves.toMatchObject({
      status: 'failed', revision: 3, reasonCode: 'media_e2ee_worker_ack_timeout',
    });
    await expect(service.grant({ kind: 'session' }))
      .rejects.toThrow('media_e2ee_worker_ack_timeout');

    await expect(service.revoke('public_media_publication_consent_reset'))
      .resolves.toMatchObject({
        status: 'revoked', revision: 4,
        reasonCode: 'public_media_publication_consent_reset',
      });
    await expect(service.grant({ kind: 'timed', durationMs: 900_000 }))
      .resolves.toMatchObject({ status: 'granted', revision: 5 });

    expect(transforms.setOutboundPublicationGate.mock.calls.map(([, , gate]) => ({
      revision: gate.revision, enabled: gate.enabled,
    }))).toEqual([
      { revision: 1, enabled: false },
      { revision: 2, enabled: true },
      { revision: 3, enabled: false },
      { revision: 4, enabled: false },
      { revision: 5, enabled: true },
    ]);
  });

  it('rejects malformed terms and mismatched session checks without widening the grant', async () => {
    service.bind(BINDING);
    await settle();

    await expect(service.grant({ kind: 'timed', durationMs: 1 } as never))
      .rejects.toThrow('public_media_publication_consent_term_invalid');
    expect(() => service.assertAllowed('other-session', 'camera-vp8'))
      .toThrow('public_media_publication_consent_context_mismatch');
  });
});

function deferred<T>(): { promise: Promise<T>; resolve(value: T): void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(accept => { resolve = accept; });
  return { promise, resolve };
}

async function settle(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}
