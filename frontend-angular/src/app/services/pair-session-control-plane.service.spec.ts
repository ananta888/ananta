import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { NetworkProfileService } from './network-profile.service';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { PUBLIC_OIDC_ISSUER } from './public-ananta-endpoints';
import { UserAuthService } from './user-auth.service';

describe('PairSessionControlPlaneService', () => {
  const posts: Array<{ url: string; body: Record<string, unknown>; token?: string }> = [];
  const gets: Array<{ url: string; token?: string; useRetry?: boolean }> = [];
  const deletes: Array<{ url: string; token?: string }> = [];
  const oidcToken = jwt({
    iss: PUBLIC_OIDC_ISSUER,
    sub: 'raw-oidc-sub',
    preferred_username: 'alice',
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
  const auth = { oidcAccessTokenValue: oidcToken as string | null, userPayload: { sub: 'hub-user' } };
  const profile = { current: publicProfile(), publicPairOptedIn: true };
  let createdResponse: Record<string, unknown>;
  let joinedResponse: Record<string, unknown>;
  let listedResponses: Array<Record<string, unknown>>;

  beforeEach(() => {
    posts.length = 0;
    gets.length = 0;
    deletes.length = 0;
    auth.oidcAccessTokenValue = oidcToken;
    profile.current = publicProfile();
    profile.publicPairOptedIn = true;
    createdResponse = publicSession('created-session');
    joinedResponse = publicSession('joined-session');
    listedResponses = [publicSession('listed-session')];
    localStorage.clear();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      PairSessionControlPlaneService,
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://127.0.0.1:5000' }] } },
      { provide: NetworkProfileService, useValue: profile },
      { provide: UserAuthService, useValue: auth },
      { provide: HubApiCoreService, useValue: {
        post: vi.fn((url: string, body: Record<string, unknown>, _base: string, token?: string) => {
          posts.push({ url, body, token });
          return of({
            ok: true,
            local_peer_id: 'oidc:canonical-peer',
            session: url.endsWith('/join') ? joinedResponse : createdResponse,
          });
        }),
        get: vi.fn((url: string, _base: string, token?: string, useRetry?: boolean) => {
          gets.push({ url, token, useRetry });
          if (url.endsWith('/rendezvous/sessions')) {
            return of({
              ok: true,
              local_peer_id: 'oidc:canonical-peer',
              data: { items: listedResponses },
            });
          }
          if (url.includes('/rendezvous/turn-credentials?')) {
            return of({
              ok: true,
              session_id: 'created-session',
              local_peer_id: 'oidc:canonical-peer',
              data: {
                username: 'expiry:peer', password: 'credential', ttl: 3600,
                uris: ['turn:webrtc.ananta.de:3478'],
                session_id: 'created-session',
                local_peer_id: 'oidc:canonical-peer',
              },
            });
          }
          return of({ ok: true, data: { signals: [], cursor: '7' } });
        }),
        delete: vi.fn((url: string, _base: string, token?: string) => {
          deletes.push({ url, token });
          return of({ ok: true });
        }),
      } },
    ] });
  });

  it('pins public create/join calls and uses only the canonical server peer id', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({
      title: 'Pair', permissions: { chat: true }, public_key_fingerprint: 'fingerprint',
      public_key_spki_b64: 'spki',
    }).subscribe();
    service.join({
      invite_code: 'INVITE', public_key_fingerprint: 'fingerprint', public_key_spki_b64: 'spki',
    }).subscribe();

    expect(service.currentPeerId).toBe('hub-user');
    expect(service.peerIdForSession('created-session')).toBe('oidc:canonical-peer');
    expect(service.peerIdForSession('joined-session')).toBe('oidc:canonical-peer');
    expect(posts.map(item => item.url)).toEqual([
      'https://webrtc.ananta.de/rendezvous/sessions',
      'https://webrtc.ananta.de/rendezvous/sessions/join',
    ]);
    expect(posts.every(item => item.token === oidcToken)).toBe(true);
  });

  it('restores public bindings only from an explicit authenticated list response', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    let sessions: readonly unknown[] = [];
    service.list().subscribe(items => { sessions = items; });

    expect(sessions).toEqual([
      expect.objectContaining({ id: 'listed-session', local_peer_id: 'oidc:canonical-peer' }),
    ]);
    expect(service.isPublicSession('listed-session')).toBe(true);
    expect(service.peerIdForSession('listed-session')).toBe('oidc:canonical-peer');
    expect(() => service.participants('unknown-session')).toThrow('pair_control_plane_binding_missing');
  });

  it('never sends a bearer token to an attacker-modified public profile origin', () => {
    profile.current = {
      ...publicProfile(),
      rendezvous: {
        ...publicProfile().rendezvous,
        base_url: 'https://webrtc.ananta.de.attacker.example',
      },
    };
    const service = TestBed.inject(PairSessionControlPlaneService);

    expect(() => service.create({ title: 'Pair' })).toThrow('public_rendezvous_profile_untrusted');
    expect(posts).toEqual([]);
  });

  it('does not treat an arbitrary public-looking profile as explicit public consent', () => {
    profile.current = {
      ...publicProfile(),
      profile_id: 'attacker-controlled-profile',
      public_rendezvous: true,
    };
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Local Pair' }).subscribe();

    expect(posts).toEqual([expect.objectContaining({
      url: 'http://127.0.0.1:5000/share-sessions',
      token: undefined,
    })]);
  });

  it('rejects a downgraded public create response without creating a binding', async () => {
    createdResponse = { ...createdResponse, security_mode: 'legacy' };
    const service = TestBed.inject(PairSessionControlPlaneService);

    await expect(firstValueFrom(service.create({ title: 'Pair' })))
      .rejects.toThrow('public_pair_security_contract_invalid');
    expect(service.isPublicSession('created-session')).toBe(false);
    expect(() => service.peerIdForSession('created-session'))
      .toThrow('pair_control_plane_binding_missing');
  });

  it('rejects a non-p2p/non-WebRTC public join response without creating a binding', async () => {
    joinedResponse = { ...joinedResponse, mode: 'relay', transport: 'hub_relay' };
    const service = TestBed.inject(PairSessionControlPlaneService);

    await expect(firstValueFrom(service.join({ invite_code: 'INVITE' })))
      .rejects.toThrow('public_pair_transport_contract_invalid');
    expect(service.isPublicSession('joined-session')).toBe(false);
  });

  it('rejects an entire public list atomically when one session exceeds advertised capabilities', async () => {
    listedResponses = [
      publicSession('listed-valid'),
      { ...publicSession('listed-downgraded'), permissions_version: 0 },
    ];
    const service = TestBed.inject(PairSessionControlPlaneService);

    await expect(firstValueFrom(service.list()))
      .rejects.toThrow('public_pair_capability_contract_invalid');
    expect(service.isPublicSession('listed-valid')).toBe(false);
    expect(service.isPublicSession('listed-downgraded')).toBe(false);
  });

  it('refuses a public profile that does not advertise strict WebRTC-only capability', () => {
    profile.current = { ...publicProfile(), require_e2e_payload_encryption: false };
    const service = TestBed.inject(PairSessionControlPlaneService);

    expect(() => service.create({ title: 'Pair' })).toThrow('public_rendezvous_profile_untrusted');
    expect(posts).toEqual([]);
  });

  it('distinguishes an expired login from an untrusted network profile', () => {
    auth.oidcAccessTokenValue = jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'raw-oidc-sub',
      exp: Math.floor(Date.now() / 1000) - 1,
    });
    const service = TestBed.inject(PairSessionControlPlaneService);

    expect(() => service.create({ title: 'Pair' }))
      .toThrow('public_session_authentication_expired');
    expect(posts).toEqual([]);
  });

  it('rejects malformed identity claims before any public API call', () => {
    auth.oidcAccessTokenValue = jwt({
      iss: PUBLIC_OIDC_ISSUER,
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    const service = TestBed.inject(PairSessionControlPlaneService);

    expect(() => service.create({ title: 'Pair' }))
      .toThrow('public_session_authentication_invalid');
    expect(posts).toEqual([]);
  });

  it('keeps a clearly local profile on Hub even while an OIDC login exists', () => {
    profile.current = {
      ...publicProfile(), profile_id: 'local', public_rendezvous: false,
      oidc: { ...publicProfile().oidc, issuer: '' },
      rendezvous: { ...publicProfile().rendezvous, base_url: '', signaling_url: '' },
    };
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Local Pair' }).subscribe();

    expect(posts[0].url).toBe('http://127.0.0.1:5000/share-sessions');
    expect(posts[0].token).toBeUndefined();
  });

  it('does not fall back to Hub after a bound public session loses its token', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Pair' }).subscribe();
    auth.oidcAccessTokenValue = null;

    expect(() => service.participants('created-session')).toThrow('public_session_authentication_lost');
    expect(() => service.end('created-session')).toThrow('public_session_authentication_lost');
    expect(gets).toEqual([]);
    expect(deletes).toEqual([]);
  });

  it('rejects a valid replacement token for another OIDC subject', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Pair' }).subscribe();
    auth.oidcAccessTokenValue = jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'another-account',
      exp: Math.floor(Date.now() / 1000) + 3600,
    });

    expect(() => service.participants('created-session')).toThrow('public_session_identity_changed');
    expect(gets).toEqual([]);
  });

  it('uses cursor signaling without implicit GET retries and deduces authority from the binding', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Pair' }).subscribe();
    service.signalPoll('created-session', '6').subscribe();
    service.signalSend('created-session', {
      type: 'offer', recipient_id: 'oidc:peer-b', payload: { sdp: 'v=0' },
    }).subscribe();

    expect(gets).toEqual([{
      url: 'https://webrtc.ananta.de/webrtc/sessions/created-session/signal?since=6',
      token: oidcToken,
      useRetry: false,
    }]);
    expect(posts.at(-1)).toMatchObject({
      url: 'https://webrtc.ananta.de/webrtc/sessions/created-session/signal', token: oidcToken,
    });
  });

  it('requests TURN credentials through the exact bound public session', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Pair' }).subscribe();
    gets.length = 0;

    await expect(firstValueFrom(service.turnCredentials('created-session'))).resolves.toEqual({
      username: 'expiry:peer', password: 'credential', ttl: 3600,
      uris: ['turn:webrtc.ananta.de:3478'],
    });
    expect(gets).toEqual([{
      url: 'https://webrtc.ananta.de/rendezvous/turn-credentials?session_id=created-session',
      token: oidcToken,
      useRetry: false,
    }]);
  });

  it('rejects TURN credentials whose response is not bound to the exact session and peer', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Pair' }).subscribe();
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.get).mockReturnValueOnce(of({
      ok: true,
      session_id: 'attacker-session',
      local_peer_id: 'oidc:canonical-peer',
      data: {
        username: 'expiry:peer', password: 'credential', ttl: 600,
        uris: ['turn:webrtc.ananta.de:3478'],
        session_id: 'created-session',
        local_peer_id: 'oidc:canonical-peer',
      },
    }));

    await expect(firstValueFrom(service.turnCredentials('created-session')))
      .rejects.toThrow('public_turn_credentials_binding_mismatch');
  });
});

function publicProfile() {
  return {
    profile_id: 'public-ananta',
    public_rendezvous: true,
    oidc: { issuer: PUBLIC_OIDC_ISSUER },
    rendezvous: {
      base_url: 'https://webrtc.ananta.de',
      signaling_url: 'wss://webrtc.ananta.de/signaling',
      transport_order: ['webrtc'],
    },
    require_e2e_payload_encryption: true,
    transport_order: ['webrtc'],
    signaling_url: 'wss://webrtc.ananta.de/signaling',
  };
}

function publicSession(id: string): Record<string, unknown> {
  const permissions = {
    chat: true,
    view_tui: true,
    remote_cursor: false,
    artifact_share: false,
    remote_control: false,
  };
  return {
    id,
    security_mode: 'strict_e2ee',
    security_contract_version: 1,
    identity_binding_version: 1,
    permissions_version: 1,
    mode: 'p2p',
    transport: 'webrtc',
    permissions,
    allowed_permissions: { ...permissions },
  };
}

function jwt(payload: Record<string, unknown>): string {
  const encode = (value: object) => btoa(JSON.stringify(value)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  return `${encode({ alg: 'none' })}.${encode(payload)}.signature`;
}
