import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import {
  PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE,
  PUBLIC_ORDINARY_MEDIA_E2EE_NOT_READY,
  PairOrdinaryMediaPolicy,
} from './pair-ordinary-media.policy';
import { PairMediaE2eeCoordinatorService } from './pair-media-e2ee-coordinator.service';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';

describe('PairOrdinaryMediaPolicy', () => {
  function configure() {
    const authorities = new Map<string, 'hub' | 'public'>([
      ['public-session', 'public'],
      ['private-session', 'hub'],
    ]);
    const statuses = new Map<string, any>([[
      'public-session',
      { sessionId: 'public-session', state: 'awaiting-security' },
    ]]);
    const activatable = new Set<string>();
    TestBed.configureTestingModule({ providers: [
      PairOrdinaryMediaPolicy,
      {
        provide: PairMediaE2eeCoordinatorService,
        useValue: {
          statusFor: vi.fn((sessionId: string) => statuses.get(sessionId)
            ?? { sessionId, state: 'inactive' }),
          canActivate: vi.fn((sessionId: string) => activatable.has(sessionId)),
        },
      },
      {
        provide: PairSessionControlPlaneService,
        useValue: { authorityKindForSession: vi.fn((sessionId: string) => {
          const authority = authorities.get(sessionId);
          if (!authority) throw new Error('pair_control_plane_binding_missing');
          return authority;
        }) },
      },
    ] });
    return { policy: TestBed.inject(PairOrdinaryMediaPolicy), statuses, activatable };
  }

  it('preserves Hub media independently of the Public E2EE coordinator', () => {
    const { policy } = configure();

    expect(policy.canActivate('private-session')).toBe(true);
    expect(policy.allows('private-session')).toBe(true);
    expect(() => policy.assertActivationAllowed('private-session')).not.toThrow();
    expect(() => policy.assertAllowed('private-session')).not.toThrow();
  });

  it('separates Public activation admission from capture readiness', () => {
    const { policy, statuses, activatable } = configure();

    expect(() => policy.assertAllowed('public-session'))
      .toThrow('public_ordinary_media_e2ee_awaiting_security');
    expect(() => policy.assertActivationAllowed('public-session'))
      .toThrow('public_ordinary_media_e2ee_awaiting_security');

    statuses.set('public-session', { sessionId: 'public-session', state: 'awaiting-peer' });
    expect(() => policy.assertAllowed('public-session'))
      .toThrow('public_ordinary_media_e2ee_awaiting_peer');

    statuses.set('public-session', { sessionId: 'public-session', state: 'inactive' });
    activatable.add('public-session');
    expect(policy.canActivate('public-session')).toBe(true);
    expect(() => policy.assertActivationAllowed('public-session')).not.toThrow();
    expect(policy.allows('public-session')).toBe(false);
    expect(() => policy.assertAllowed('public-session'))
      .toThrow(PUBLIC_ORDINARY_MEDIA_E2EE_NOT_READY);

    statuses.set('public-session', { sessionId: 'public-session', state: 'ready' });
    activatable.delete('public-session');
    expect(policy.canActivate('public-session')).toBe(true);
    expect(policy.allows('public-session')).toBe(true);
    expect(() => policy.assertAllowed('public-session')).not.toThrow();
  });

  it('projects a coordinator failure without weakening missing-binding checks', () => {
    const { policy, statuses } = configure();
    statuses.set('public-session', {
      sessionId: 'public-session', state: 'failed', reasonCode: 'media_e2ee_transform_unsupported',
    });

    expect(() => policy.assertActivationAllowed('public-session'))
      .toThrow('media_e2ee_transform_unsupported');
    expect(() => policy.assertAllowed('public-session'))
      .toThrow('media_e2ee_transform_unsupported');
    expect(policy.allows('missing-session')).toBe(false);
    expect(() => policy.assertAllowed('missing-session'))
      .toThrow('ordinary_media_session_binding_missing');
    statuses.set('public-session', { sessionId: 'public-session', state: 'failed' });
    expect(() => policy.assertAllowed('public-session'))
      .toThrow(PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE);
  });
});
