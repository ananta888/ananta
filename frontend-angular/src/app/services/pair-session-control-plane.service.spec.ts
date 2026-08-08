import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { NetworkProfileService } from './network-profile.service';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { UserAuthService } from './user-auth.service';

describe('PairSessionControlPlaneService', () => {
  const posts: Array<{ url: string; body: Record<string, unknown>; token?: string }> = [];
  const gets: Array<{ url: string; token?: string }> = [];
  const oidcToken = jwt({ sub: 'oidc-sub', preferred_username: 'alice' });

  beforeEach(() => {
    posts.length = 0;
    gets.length = 0;
    localStorage.clear();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      PairSessionControlPlaneService,
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://127.0.0.1:5000' }] } },
      { provide: NetworkProfileService, useValue: { current: {
        rendezvous: { base_url: 'https://webrtc.ananta.de', signaling_url: 'wss://webrtc.ananta.de/signaling' },
      } } },
      { provide: UserAuthService, useValue: { oidcAccessTokenValue: oidcToken, userPayload: { sub: 'hub-user' } } },
      { provide: HubApiCoreService, useValue: {
        post: vi.fn((url: string, body: Record<string, unknown>, _base: string, token?: string) => {
          posts.push({ url, body, token });
          return of({ ok: true, session: { id: 'public-session', security_mode: 'strict_e2ee' } });
        }),
        get: vi.fn((url: string, _base: string, token?: string) => {
          gets.push({ url, token });
          return of({ ok: true, data: { signals: [] } });
        }),
        delete: vi.fn(() => of({ ok: true })),
      } },
    ] });
  });

  it('creates and joins through the OIDC-authenticated public rendezvous boundary', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({
      title: 'Pair', permissions: { chat: true }, public_key_fingerprint: 'fingerprint',
      public_key_spki_b64: 'spki',
    }).subscribe();
    service.join({
      invite_code: 'INVITE', public_key_fingerprint: 'fingerprint', public_key_spki_b64: 'spki',
    }).subscribe();

    expect(service.currentPeerId).toBe('alice');
    expect(posts.map(item => item.url)).toEqual([
      'https://webrtc.ananta.de/rendezvous/sessions',
      'https://webrtc.ananta.de/rendezvous/sessions/join',
    ]);
    expect(posts.every(item => item.token === oidcToken)).toBe(true);
    expect(posts[0].body).toMatchObject({
      owner_device_fingerprint: 'fingerprint', allowed_permissions: { chat: true },
    });
    expect(posts[1].body).toMatchObject({ device_fingerprint: 'fingerprint' });
  });

  it('uses the public service only for signaling metadata', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.signalPoll('session-a', '').subscribe();
    service.signalSend('session-a', {
      type: 'offer', recipient_id: 'bob', payload: { sdp: 'v=0' },
    }).subscribe();

    expect(gets).toEqual([{
      url: 'https://webrtc.ananta.de/webrtc/sessions/session-a/signal', token: oidcToken,
    }]);
    expect(posts[0]).toMatchObject({
      url: 'https://webrtc.ananta.de/webrtc/sessions/session-a/signal', token: oidcToken,
    });
  });
});

function jwt(payload: Record<string, unknown>): string {
  const encode = (value: object) => btoa(JSON.stringify(value)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  return `${encode({ alg: 'none' })}.${encode(payload)}.`;
}
