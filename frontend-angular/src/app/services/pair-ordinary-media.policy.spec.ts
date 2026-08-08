import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import {
  PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE,
  PairOrdinaryMediaPolicy,
} from './pair-ordinary-media.policy';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';

describe('PairOrdinaryMediaPolicy', () => {
  it('rejects public Pair media independently of mutable feature flags', () => {
    const authorities = new Map<string, 'hub' | 'public'>([
      ['public-session', 'public'],
      ['private-session', 'hub'],
    ]);
    TestBed.configureTestingModule({ providers: [
      PairOrdinaryMediaPolicy,
      {
        provide: PairSessionControlPlaneService,
        useValue: { authorityKindForSession: vi.fn((sessionId: string) => {
          const authority = authorities.get(sessionId);
          if (!authority) throw new Error('pair_control_plane_binding_missing');
          return authority;
        }) },
      },
    ] });
    const policy = TestBed.inject(PairOrdinaryMediaPolicy);

    expect(() => policy.assertAllowed('public-session'))
      .toThrow(PUBLIC_ORDINARY_MEDIA_E2EE_UNAVAILABLE);
    expect(() => policy.assertAllowed('private-session')).not.toThrow();
    expect(policy.allows('missing-session')).toBe(false);
    expect(() => policy.assertAllowed('missing-session'))
      .toThrow('ordinary_media_session_binding_missing');
  });
});
