import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { PairMediaPublicationPolicy } from './pair-media-publication.policy';
import { PairOrdinaryMediaPolicy } from './pair-ordinary-media.policy';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { PublicPairMediaPublicationConsentService } from './public-pair-media-publication-consent.service';

describe('PairMediaPublicationPolicy', () => {
  function configure() {
    const technical = { assertAllowed: vi.fn() };
    const consent = { assertAllowed: vi.fn() };
    const authorityKindForSession = vi.fn((sessionId: string) => {
      if (sessionId === 'hub-session') return 'hub';
      if (sessionId === 'public-session') return 'public';
      throw new Error('pair_control_plane_binding_missing');
    });
    TestBed.configureTestingModule({ providers: [
      PairMediaPublicationPolicy,
      { provide: PairOrdinaryMediaPolicy, useValue: technical },
      { provide: PublicPairMediaPublicationConsentService, useValue: consent },
      { provide: PairSessionControlPlaneService, useValue: { authorityKindForSession } },
    ] });
    return {
      policy: TestBed.inject(PairMediaPublicationPolicy), technical, consent,
    };
  }

  it('preserves Hub publication admission without consulting Public consent', () => {
    const { policy, technical, consent } = configure();

    expect(policy.allows('hub-session', 'microphone-opus')).toBe(true);
    expect(() => policy.assertAllowed('hub-session', 'screen-vp8')).not.toThrow();
    expect(technical.assertAllowed).toHaveBeenCalledWith('hub-session');
    expect(consent.assertAllowed).not.toHaveBeenCalled();
  });

  it('requires both technical E2EE readiness and exact local consent for Public publication', () => {
    const { policy, technical, consent } = configure();
    consent.assertAllowed.mockImplementationOnce(() => {
      throw new Error('public_media_publication_consent_required');
    });

    expect(policy.allows('public-session', 'camera-vp8')).toBe(false);
    expect(technical.assertAllowed).toHaveBeenCalledWith('public-session');
    expect(consent.assertAllowed).toHaveBeenCalledWith('public-session', 'camera-vp8');

    expect(() => policy.assertAllowed('public-session', 'screen-vp8')).not.toThrow();
  });

  it('does not let consent weaken technical media failure or missing bindings', () => {
    const { policy, technical, consent } = configure();
    technical.assertAllowed.mockImplementationOnce(() => {
      throw new Error('public_ordinary_media_e2ee_not_ready');
    });

    expect(() => policy.assertAllowed('public-session', 'microphone-opus'))
      .toThrow('public_ordinary_media_e2ee_not_ready');
    expect(consent.assertAllowed).not.toHaveBeenCalled();
    expect(() => policy.assertAllowed('missing', 'microphone-opus'))
      .toThrow('ordinary_media_session_binding_missing');
  });
});
