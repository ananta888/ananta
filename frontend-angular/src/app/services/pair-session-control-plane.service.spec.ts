import { HttpContext } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { Subject, TimeoutError, defer, firstValueFrom, of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import { NetworkProfileService } from './network-profile.service';
import { PairMembershipCapabilityStore } from './pair-membership-capability.store';
import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import { PublicPairMediaRuntimeCapabilityService } from './public-pair-media-runtime-capability.service';
import { PUBLIC_PAIR_MEDIA_CAPABILITIES_V2 } from './public-pair-media-security-contract';
import { PUBLIC_OIDC_ISSUER } from './public-ananta-endpoints';
import { UserAuthService } from './user-auth.service';
import { SUPPRESS_GLOBAL_ERROR_NOTIFICATION } from './error-request-context';

describe('PairSessionControlPlaneService', () => {
  const posts: Array<{ url: string; body: Record<string, unknown>; token?: string }> = [];
  const gets: Array<{ url: string; token?: string; useRetry?: boolean }> = [];
  const deletes: Array<{ url: string; token?: string }> = [];
  const requests: Array<{
    method: string;
    url: string;
    body?: unknown;
    token?: string;
    headers?: Record<string, string>;
    context?: HttpContext;
  }> = [];
  const ownerPeerId = `peer:${'a'.repeat(64)}`;
  const joinerPeerId = `peer:${'b'.repeat(64)}`;
  const listedPeerId = `peer:${'c'.repeat(64)}`;
  const legacyPeerId = `oidc:${'d'.repeat(64)}`;
  const oidcToken = jwt({
    iss: PUBLIC_OIDC_ISSUER,
    sub: 'raw-oidc-sub',
    preferred_username: 'alice',
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
  const auth = { oidcAccessTokenValue: oidcToken as string | null, userPayload: { sub: 'hub-user' } };
  const profile = {
    current: publicProfile(),
    profile$: of(publicProfile()),
    publicPairOptedIn: true,
  };
  const directory = {
    list: vi.fn(() => [{ role: 'hub', url: 'http://127.0.0.1:5000' }]),
  };
  let createdResponse: Record<string, unknown>;
  let joinedResponse: Record<string, unknown>;
  let listedResponses: Array<Record<string, unknown>>;
  let runtimeResponse: Record<string, unknown>;
  let publicMediaAdvertisement: Record<string, unknown> | null;

  beforeEach(() => {
    posts.length = 0;
    gets.length = 0;
    deletes.length = 0;
    requests.length = 0;
    auth.oidcAccessTokenValue = oidcToken;
    profile.current = publicProfile();
    profile.publicPairOptedIn = true;
    directory.list.mockReset();
    directory.list.mockReturnValue([{ role: 'hub', url: 'http://127.0.0.1:5000' }]);
    createdResponse = publicSession('created-session', ownerPeerId, 2, 'owner', 'active');
    joinedResponse = publicSession('joined-session', joinerPeerId, 2, 'participant', 'active');
    listedResponses = [publicSession('listed-session', listedPeerId, 2, 'owner', 'parked')];
    runtimeResponse = {
      state: 'active', security_epoch: 4, changed: true,
      parked_session_ids: ['parked-session'],
    };
    publicMediaAdvertisement = null;
    localStorage.clear();
    localStorage.setItem('ananta.pair-device-id.v1', 'device-a');
    sessionStorage.clear();
    TestBed.resetTestingModule();
    TestBed.configureTestingModule({ providers: [
      PairSessionControlPlaneService,
      { provide: AgentDirectoryService, useValue: directory },
      { provide: NetworkProfileService, useValue: profile },
      { provide: UserAuthService, useValue: auth },
      {
        provide: PublicPairMediaRuntimeCapabilityService,
        useValue: { membershipAdvertisement: () => publicMediaAdvertisement },
      },
      { provide: HubApiCoreService, useValue: {
        retryCount: 2,
        request: vi.fn((
          method: string,
          url: string,
          _base: string,
          options: {
            body?: unknown;
            token?: string;
            headers?: Record<string, string>;
            context?: HttpContext;
          } = {},
        ) => {
          requests.push({ method, url, ...options });
          if (method === 'POST' && url.endsWith('/rendezvous/sessions')) {
            return of({ ok: true, local_peer_id: ownerPeerId, session: createdResponse });
          }
          if (method === 'POST' && url.endsWith('/rendezvous/sessions/join')) {
            return of({ ok: true, local_peer_id: joinerPeerId, session: joinedResponse });
          }
          if (method === 'PUT' && url.endsWith('/membership/runtime')) {
            const localPeerId = url.includes('/joined-session/') ? joinerPeerId : ownerPeerId;
            return of({ ok: true, local_peer_id: localPeerId, data: runtimeResponse });
          }
          if (method === 'POST' && url.endsWith('/rendezvous/sessions/catalog')) {
            return of({ ok: true, data: { items: listedResponses } });
          }
          if (method === 'GET' && url.endsWith('/rendezvous/sessions')) {
            return of({
              ok: true,
              local_peer_id: legacyPeerId,
              data: { items: listedResponses, local_peer_id: legacyPeerId },
            });
          }
          if (url.includes('/rendezvous/turn-credentials?')) {
            return of({
              ok: true,
              session_id: 'created-session',
              local_peer_id: ownerPeerId,
              data: {
                username: 'expiry:peer', password: 'credential', ttl: 3600,
                uris: ['turn:webrtc.ananta.de:3478'],
                session_id: 'created-session',
                local_peer_id: ownerPeerId,
              },
            });
          }
          if (method === 'GET' && url.includes('/participants')) {
            const localPeerId = url.includes('/legacy-session/') ? legacyPeerId : ownerPeerId;
            return of({
              ok: true,
              local_peer_id: localPeerId,
              data: { participants: [], local_peer_id: localPeerId },
            });
          }
          if (method === 'DELETE') return of({ ok: true, local_peer_id: ownerPeerId });
          return of({ ok: true, local_peer_id: ownerPeerId, data: { signals: [], cursor: '7' } });
        }),
        post: vi.fn((url: string, body: Record<string, unknown>, _base: string, token?: string) => {
          posts.push({ url, body, token });
          return of({
            ok: true,
            local_peer_id: legacyPeerId,
            session: url.endsWith('/join') ? joinedResponse : createdResponse,
          });
        }),
        get: vi.fn((url: string, _base: string, token?: string, useRetry?: boolean) => {
          gets.push({ url, token, useRetry });
          if (url.endsWith('/rendezvous/sessions')) {
            return of({
              ok: true,
              local_peer_id: legacyPeerId,
              data: { items: listedResponses },
            });
          }
          if (url.includes('/rendezvous/turn-credentials?')) {
            return of({
              ok: true,
              session_id: 'created-session',
              local_peer_id: legacyPeerId,
              data: {
                username: 'expiry:peer', password: 'credential', ttl: 3600,
                uris: ['turn:webrtc.ananta.de:3478'],
                session_id: 'created-session',
                local_peer_id: legacyPeerId,
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

  it('negotiates v2 and binds two device peers under the same OIDC account', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({
      title: 'Pair', permissions: { chat: true }, public_key_fingerprint: 'fingerprint',
      public_key_spki_b64: 'spki',
    }).subscribe();
    service.join({
      invite_code: 'INVITE', public_key_fingerprint: 'fingerprint', public_key_spki_b64: 'spki',
    }).subscribe();

    expect(service.currentPeerId).toBe('hub-user');
    expect(service.peerIdForSession('created-session')).toBe(ownerPeerId);
    expect(service.peerIdForSession('joined-session')).toBe(joinerPeerId);
    expect(service.requiresSignalEpoch('created-session')).toBe(true);
    expect(service.requiresSignalEpoch('joined-session')).toBe(true);
    const route = service.authorityRouteForSession('created-session');
    expect(route).toEqual({ kind: 'public', baseUrl: 'https://webrtc.ananta.de' });
    expect(Object.isFrozen(route)).toBe(true);
    expect(Object.keys(route)).toEqual(['kind', 'baseUrl']);
    expect(requests.map(item => item.url)).toEqual([
      'https://webrtc.ananta.de/rendezvous/sessions',
      'https://webrtc.ananta.de/rendezvous/sessions/join',
    ]);
    expect(requests.every(item => item.token === oidcToken)).toBe(true);
    expect(requests.map(item => item.body)).toEqual([
      expect.objectContaining({
        identity_binding_version: 2,
        owner_device_id: 'device-a',
      }),
      expect.objectContaining({
        identity_binding_version: 2,
        device_id: 'device-a',
      }),
    ]);
    expect(requests.map(item => item.headers)).toEqual([
      { 'X-Ananta-Membership-Capability': expect.stringMatching(/^[A-Za-z0-9_-]{43}$/) },
      { 'X-Ananta-Membership-Capability': expect.stringMatching(/^[A-Za-z0-9_-]{43}$/) },
    ]);
  });

  it('rejects a Public create response after the OIDC identity changes in flight', async () => {
    const core = TestBed.inject(HubApiCoreService);
    const response = new Subject<Record<string, unknown>>();
    vi.mocked(core.request).mockReturnValueOnce(response);
    const service = TestBed.inject(PairSessionControlPlaneService);
    const pending = firstValueFrom(service.create({
      title: 'Pair', permissions: { chat: true }, public_key_fingerprint: 'fingerprint',
      public_key_spki_b64: 'spki',
    }));
    auth.oidcAccessTokenValue = jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'replacement-subject',
      exp: Math.floor(Date.now() / 1000) + 3600,
    });

    response.next({ ok: true, local_peer_id: ownerPeerId, session: createdResponse });
    response.complete();

    await expect(pending).rejects.toThrow('public_session_identity_changed');
    expect(() => service.peerIdForSession('created-session'))
      .toThrow('pair_control_plane_binding_missing');
  });

  it('automatically resumes an idempotent public join after Retry-After', async () => {
    vi.useFakeTimers();
    try {
      const core = TestBed.inject(HubApiCoreService);
      const request = vi.mocked(core.request);
      let attempts = 0;
      request.mockImplementationOnce(() => defer(() => {
        attempts += 1;
        return attempts === 1
          ? throwError(() => ({
            status: 429,
            error: { error: 'rate_limited' },
            headers: { get: (name: string) => name === 'Retry-After' ? '2' : null },
          }))
          : of({ ok: true, local_peer_id: joinerPeerId, session: joinedResponse });
      }));
      const service = TestBed.inject(PairSessionControlPlaneService);
      const result = firstValueFrom(service.join<Record<string, unknown>>({
        invite_code: 'INVITE',
        public_key_fingerprint: 'fingerprint',
        public_key_spki_b64: 'spki',
      }));

      await vi.advanceTimersByTimeAsync(2_000);

      await expect(result).resolves.toMatchObject({ id: 'joined-session' });
      expect(request).toHaveBeenCalledTimes(1);
      expect(attempts).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('adds the exact immutable media capability advertisement to public create and join', () => {
    publicMediaAdvertisement = Object.freeze({
      public_media_e2ee_version: 2,
      public_media_capabilities: PUBLIC_PAIR_MEDIA_CAPABILITIES_V2,
    });
    const service = TestBed.inject(PairSessionControlPlaneService);

    service.create({ title: 'Pair', permissions: { chat: true } }).subscribe();
    service.join({ invite_code: 'INVITE' }).subscribe();

    for (const body of requests.map(request => request.body as Record<string, unknown>)) {
      expect(body['public_media_e2ee_version']).toBe(2);
      expect(body['public_media_capabilities']).toEqual({
        version: 2,
        transform: 'RTCRtpScriptTransform',
        frame_format: 'ananta.public-pair.media-frame.v2',
        grants: ['microphone-opus', 'camera-vp8', 'screen-vp8'],
      });
      expect(Object.isFrozen(body)).toBe(true);
      expect(Object.isFrozen(body['public_media_capabilities'])).toBe(true);
      expect(Object.isFrozen(
        (body['public_media_capabilities'] as { grants: readonly string[] }).grants,
      )).toBe(true);
    }
  });

  it('omits both media capability fields when the standards-only runtime is unavailable', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);

    service.create({ title: 'Pair' }).subscribe();
    service.join({ invite_code: 'INVITE' }).subscribe();

    for (const body of requests.map(request => request.body as Record<string, unknown>)) {
      expect(body).not.toHaveProperty('public_media_e2ee_version');
      expect(body).not.toHaveProperty('public_media_capabilities');
    }
  });

  it('posts only exact-account proofs and restores the authenticated v2 catalogue response', () => {
    const capability = seedBoundCapability('listed-session', listedPeerId);
    const otherCapability = seedBoundCapability(
      'other-account-session', `peer:${'e'.repeat(64)}`, 'other-account',
    );
    const service = TestBed.inject(PairSessionControlPlaneService);
    let sessions: readonly unknown[] = [];
    service.list().subscribe(items => { sessions = items; });

    expect(sessions).toEqual([
      expect.objectContaining({ id: 'listed-session', local_peer_id: listedPeerId }),
    ]);
    expect(service.isPublicSession('listed-session')).toBe(true);
    expect(service.peerIdForSession('listed-session')).toBe(listedPeerId);
    expect(() => service.participants('unknown-session')).toThrow('pair_control_plane_binding_missing');
    expect(requests[0]).toMatchObject({
      method: 'POST',
      url: 'https://webrtc.ananta.de/rendezvous/sessions/catalog',
      token: oidcToken,
      body: { memberships: [{
        session_id: 'listed-session',
        local_peer_id: listedPeerId,
        membership_capability: capability,
      }] },
    });
    expect(requests[0].headers).toBeUndefined();
    expect(requests[0].context?.get(SUPPRESS_GLOBAL_ERROR_NOTIFICATION)).toBe(true);
    expect(requests[0].url).not.toContain(capability);
    expect(requests[0].url).not.toContain(otherCapability);
  });

  it('queries every proof batch so stale leading proofs cannot hide a later live session', async () => {
    for (let index = 0; index < 33; index += 1) {
      seedBoundCapability(
        `stale-${String(index).padStart(2, '0')}`,
        `peer:${index.toString(16).padStart(64, '0')}`,
      );
    }
    const latePeerId = `peer:${'f'.repeat(64)}`;
    const lateCapability = seedBoundCapability('zz-live-session', latePeerId);
    const legacy = publicSession('legacy-session', legacyPeerId, 1);
    const live = publicSession('zz-live-session', latePeerId);
    const service = TestBed.inject(PairSessionControlPlaneService);
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.request)
      .mockReturnValueOnce(of({ ok: true, data: { items: [legacy] } }))
      .mockReturnValueOnce(of({ ok: true, data: { items: [legacy, live] } }));

    await expect(firstValueFrom(service.list())).resolves.toEqual([
      expect.objectContaining({ id: 'legacy-session' }),
      expect.objectContaining({ id: 'zz-live-session', local_peer_id: latePeerId }),
    ]);

    const catalogCalls = vi.mocked(core.request).mock.calls;
    expect(catalogCalls).toHaveLength(2);
    expect((catalogCalls[0]?.[3]?.body as { memberships: unknown[] }).memberships)
      .toHaveLength(32);
    expect((catalogCalls[1]?.[3]?.body as { memberships: Array<Record<string, unknown>> }).memberships)
      .toEqual(expect.arrayContaining([expect.objectContaining({
        session_id: 'zz-live-session',
        local_peer_id: latePeerId,
        membership_capability: lateCapability,
      })]));
    expect(service.isPublicSession('zz-live-session')).toBe(true);
  });

  it('rejects conflicting duplicate rows across batches before binding any session', async () => {
    for (let index = 0; index < 33; index += 1) {
      seedBoundCapability(
        `stale-${String(index).padStart(2, '0')}`,
        `peer:${index.toString(16).padStart(64, '0')}`,
      );
    }
    const first = publicSession('legacy-conflict', legacyPeerId, 1);
    const second = { ...first, security_epoch: 4 };
    const service = TestBed.inject(PairSessionControlPlaneService);
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.request)
      .mockReturnValueOnce(of({ ok: true, data: { items: [first] } }))
      .mockReturnValueOnce(of({ ok: true, data: { items: [second] } }));

    await expect(firstValueFrom(service.list()))
      .rejects.toThrow('pair_session_list_duplicate_conflict');
    expect(service.isPublicSession('legacy-conflict')).toBe(false);
  });

  it('rejects a completed catalogue snapshot after the OIDC subject changes in flight', async () => {
    seedBoundCapability('listed-session', listedPeerId);
    const response = new Subject<unknown>();
    const service = TestBed.inject(PairSessionControlPlaneService);
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.request).mockReturnValueOnce(response);

    const pending = firstValueFrom(service.list());
    auth.oidcAccessTokenValue = jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'replacement-account',
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    response.next({ ok: true, data: { items: listedResponses } });
    response.complete();

    await expect(pending).rejects.toThrow('public_session_identity_changed');
    expect(service.isPublicSession('listed-session')).toBe(false);
  });

  it('rejects a completed catalogue snapshot after the Public profile changes in flight', async () => {
    seedBoundCapability('listed-session', listedPeerId);
    const response = new Subject<unknown>();
    const service = TestBed.inject(PairSessionControlPlaneService);
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.request).mockReturnValueOnce(response);

    const pending = firstValueFrom(service.list());
    profile.current = {
      ...publicProfile(),
      profile_id: 'local',
      public_rendezvous: false,
    };
    response.next({ ok: true, data: { items: listedResponses } });
    response.complete();

    await expect(pending).rejects.toThrow('public_session_profile_changed');
    expect(service.isPublicSession('listed-session')).toBe(false);
  });

  it('activates one exact v2 membership and validates the authoritative epoch', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    requests.length = 0;

    await expect(firstValueFrom(service.activateSessionRuntime('created-session'))).resolves.toEqual({
      state: 'active', security_epoch: 4, changed: true,
      parked_session_ids: ['parked-session'],
    });

    expect(requests).toEqual([expect.objectContaining({
      method: 'PUT',
      url: 'https://webrtc.ananta.de/rendezvous/sessions/created-session/membership/runtime',
      body: { state: 'active' },
      token: oidcToken,
      headers: {
        'X-Ananta-Peer-Id': ownerPeerId,
        'X-Ananta-Membership-Capability': expect.stringMatching(/^[A-Za-z0-9_-]{43}$/),
      },
    })]);
    expect(requests[0].context?.get(SUPPRESS_GLOBAL_ERROR_NOTIFICATION)).toBe(true);
  });

  it('rejects a malformed runtime response instead of treating the target as active', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    runtimeResponse = {
      state: 'active', security_epoch: 0, changed: true, parked_session_ids: [],
    };

    await expect(firstValueFrom(service.activateSessionRuntime('created-session')))
      .rejects.toThrow('pair_session_runtime_response_invalid');
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

  it('projects the immutable bound Hub route after the directory changes', () => {
    profile.current = {
      ...publicProfile(),
      profile_id: 'hub-profile',
      public_rendezvous: false,
    };
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Hub Pair' }).subscribe();

    directory.list.mockReturnValue([{ role: 'hub', url: 'http://hub-b.test' }]);

    expect(service.authorityRouteForSession('created-session')).toEqual({
      kind: 'hub', baseUrl: 'http://127.0.0.1:5000',
    });
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

  it('requires the v2 create role and active runtime projection before binding', async () => {
    delete createdResponse['local_role'];
    const service = TestBed.inject(PairSessionControlPlaneService);

    await expect(firstValueFrom(service.create({ title: 'Pair' })))
      .rejects.toThrow('pair_session_local_role_missing');
    expect(service.isPublicSession('created-session')).toBe(false);
  });

  it('rejects a parked v2 join response instead of locally treating it as active', async () => {
    joinedResponse = { ...joinedResponse, local_runtime_state: 'parked' };
    const service = TestBed.inject(PairSessionControlPlaneService);

    await expect(firstValueFrom(service.join({ invite_code: 'INVITE' })))
      .rejects.toThrow('pair_session_runtime_state_invalid');
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

  it('rejects v2 discovery without authoritative role/runtime before binding it', async () => {
    seedBoundCapability('listed-session', listedPeerId);
    delete listedResponses[0]['local_role'];
    const service = TestBed.inject(PairSessionControlPlaneService);

    await expect(firstValueFrom(service.list()))
      .rejects.toThrow('pair_session_local_role_missing');
    expect(service.isPublicSession('listed-session')).toBe(false);
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

  it('never falls back to Hub when the caller requires Public Pair', () => {
    profile.current = {
      ...publicProfile(), profile_id: 'local', public_rendezvous: false,
      oidc: { ...publicProfile().oidc, issuer: '' },
      rendezvous: { ...publicProfile().rendezvous, base_url: '', signaling_url: '' },
    };
    const service = TestBed.inject(PairSessionControlPlaneService);

    expect(() => service.create({ title: 'Public only' }, { expectedAuthority: 'public' }))
      .toThrow('public_pair_authority_required');
    expect(posts).toEqual([]);
    expect(requests).toEqual([]);
  });

  it('never falls back to a Hub session catalogue when Public Pair is required', () => {
    profile.current = {
      ...publicProfile(), profile_id: 'local', public_rendezvous: false,
      oidc: { ...publicProfile().oidc, issuer: '' },
      rendezvous: { ...publicProfile().rendezvous, base_url: '', signaling_url: '' },
    };
    const service = TestBed.inject(PairSessionControlPlaneService);

    expect(() => service.list({ expectedAuthority: 'public' }))
      .toThrow('public_pair_authority_required');
    expect(requests).toEqual([]);
  });

  it('does not fall back to Hub after a bound public session loses its token', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Pair' }).subscribe();
    requests.length = 0;
    auth.oidcAccessTokenValue = null;

    expect(() => service.participants('created-session')).toThrow('public_session_authentication_lost');
    expect(() => service.end('created-session')).toThrow('public_session_authentication_lost');
    expect(gets).toEqual([]);
    expect(deletes).toEqual([]);
    expect(requests).toEqual([]);
  });

  it('rejects a valid replacement token for another OIDC subject', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Pair' }).subscribe();
    requests.length = 0;
    auth.oidcAccessTokenValue = jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'another-account',
      exp: Math.floor(Date.now() / 1000) + 3600,
    });

    expect(() => service.participants('created-session')).toThrow('public_session_identity_changed');
    expect(gets).toEqual([]);
    expect(requests).toEqual([]);
  });

  it('uses cursor signaling without implicit GET retries and deduces authority from the binding', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Pair' }).subscribe();
    requests.length = 0;
    service.signalPoll('created-session', '6', 3).subscribe();
    service.signalSend('created-session', {
      type: 'offer', recipient_id: 'oidc:peer-b', security_epoch: 3,
      payload: { sdp: 'v=0' },
    }).subscribe();

    expect(requests[0]).toMatchObject({
      method: 'GET',
      url: 'https://webrtc.ananta.de/webrtc/sessions/created-session/signal?since=6&security_epoch=3',
      token: oidcToken,
      headers: {
        'X-Ananta-Peer-Id': ownerPeerId,
        'X-Ananta-Membership-Capability': expect.stringMatching(/^[A-Za-z0-9_-]{43}$/),
      },
    });
    expect(requests[1]).toMatchObject({
      method: 'POST',
      url: 'https://webrtc.ananta.de/webrtc/sessions/created-session/signal',
      token: oidcToken,
      body: expect.objectContaining({ security_epoch: 3 }),
      headers: {
        'X-Ananta-Peer-Id': ownerPeerId,
        'X-Ananta-Membership-Capability': expect.stringMatching(/^[A-Za-z0-9_-]{43}$/),
      },
    });
    expect(requests[0].context?.get(SUPPRESS_GLOBAL_ERROR_NOTIFICATION)).toBe(true);
    expect(requests[1].context?.get(SUPPRESS_GLOBAL_ERROR_NOTIFICATION)).toBe(true);
  });

  it.each([undefined, 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    'fails closed before a v2 public signal poll with invalid epoch %s',
    async (securityEpoch) => {
      const service = TestBed.inject(PairSessionControlPlaneService);
      await firstValueFrom(service.create({ title: 'Pair' }));
      requests.length = 0;

      expect(() => service.signalPoll('created-session', '6', securityEpoch))
        .toThrow('signal_epoch_required');
      expect(requests).toEqual([]);
    },
  );

  it.each([undefined, 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    'fails closed before a v2 public signal send with invalid epoch %s',
    async (securityEpoch) => {
      const service = TestBed.inject(PairSessionControlPlaneService);
      await firstValueFrom(service.create({ title: 'Pair' }));
      requests.length = 0;

      expect(() => service.signalSend('created-session', {
        type: 'offer', security_epoch: securityEpoch, payload: { sdp: 'v=0' },
      })).toThrow('signal_epoch_required');
      expect(requests).toEqual([]);
    },
  );

  it('keeps the Hub signal-poll path compatible without an epoch query', async () => {
    profile.current = {
      ...publicProfile(), profile_id: 'local', public_rendezvous: false,
      oidc: { ...publicProfile().oidc, issuer: '' },
      rendezvous: { ...publicProfile().rendezvous, base_url: '', signaling_url: '' },
    };
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Hub Pair' }));
    expect(service.requiresSignalEpoch('created-session')).toBe(false);
    gets.length = 0;

    await firstValueFrom(service.signalPoll('created-session', '6'));

    expect(gets).toContainEqual({
      url: 'http://127.0.0.1:5000/api/webrtc/sessions/created-session/signal?since=6',
      token: undefined,
      useRetry: false,
    });
  });

  it('requests TURN credentials through the exact bound public session', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Pair' }).subscribe();
    requests.length = 0;

    await expect(firstValueFrom(service.turnCredentials('created-session'))).resolves.toEqual({
      username: 'expiry:peer', password: 'credential', ttl: 3600,
      uris: ['turn:webrtc.ananta.de:3478'],
    });
    expect(requests).toEqual([expect.objectContaining({
      method: 'GET',
      url: 'https://webrtc.ananta.de/rendezvous/turn-credentials?session_id=created-session',
      token: oidcToken,
      headers: expect.objectContaining({ 'X-Ananta-Peer-Id': ownerPeerId }),
    })]);
    expect(requests[0].context?.get(SUPPRESS_GLOBAL_ERROR_NOTIFICATION)).toBe(true);
  });

  it('coalesces and caches TURN credentials for one exact immutable binding', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    requests.length = 0;

    const [first, second] = await Promise.all([
      firstValueFrom(service.turnCredentials('created-session')),
      firstValueFrom(service.turnCredentials('created-session')),
    ]);
    const third = await firstValueFrom(service.turnCredentials('created-session'));

    expect(first).toEqual(second);
    expect(third).toEqual(first);
    expect(requests.filter(request => request.url.includes('/turn-credentials?')))
      .toHaveLength(1);
  });

  it('invalidates TURN credentials when a local session binding is forgotten', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    requests.length = 0;
    await firstValueFrom(service.turnCredentials('created-session'));

    service.forgetSession('created-session');
    listedResponses = [publicSession('created-session', ownerPeerId)];
    await firstValueFrom(service.list());
    await firstValueFrom(service.turnCredentials('created-session'));

    expect(requests.filter(request => request.url.includes('/turn-credentials?')))
      .toHaveLength(2);
  });

  it('rejects TURN credentials whose response is not bound to the exact session and peer', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    service.create({ title: 'Pair' }).subscribe();
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.request).mockReturnValueOnce(of({
      ok: true,
      session_id: 'attacker-session',
      local_peer_id: ownerPeerId,
      data: {
        username: 'expiry:peer', password: 'credential', ttl: 600,
        uris: ['turn:webrtc.ananta.de:3478'],
        session_id: 'created-session',
        local_peer_id: ownerPeerId,
      },
    }));

    await expect(firstValueFrom(service.turnCredentials('created-session')))
      .rejects.toThrow('public_turn_credentials_binding_mismatch');
  });

  it('retries an ambiguous create with the same persisted capability and exact wire body', async () => {
    publicMediaAdvertisement = Object.freeze({
      public_media_e2ee_version: 2,
      public_media_capabilities: PUBLIC_PAIR_MEDIA_CAPABILITIES_V2,
    });
    const service = TestBed.inject(PairSessionControlPlaneService);
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.request).mockReturnValueOnce(throwError(() => ({ status: 0 })));
    const expiresAt = Date.now() / 1000 + 60;

    await expect(firstValueFrom(service.create({
      title: 'Pair', permissions: { chat: true }, public_key_fingerprint: 'fingerprint',
      expires_at: expiresAt,
    }))).rejects.toMatchObject({ status: 0 });
    const firstOptions = vi.mocked(core.request).mock.calls[0][3];

    await expect(firstValueFrom(service.create({
      title: 'Pair', permissions: { chat: true }, public_key_fingerprint: 'fingerprint',
      expires_at: Date.now() / 1000 + 60,
    }))).resolves.toMatchObject({ id: 'created-session', local_peer_id: ownerPeerId });
    const secondOptions = vi.mocked(core.request).mock.calls[1][3];

    expect(secondOptions?.body).toEqual(firstOptions?.body);
    expect(secondOptions?.body).toMatchObject({
      public_media_e2ee_version: 2,
      public_media_capabilities: {
        version: 2,
        transform: 'RTCRtpScriptTransform',
        frame_format: 'ananta.public-pair.media-frame.v2',
        grants: ['microphone-opus', 'camera-vp8', 'screen-vp8'],
      },
    });
    expect(secondOptions?.headers?.['X-Ananta-Membership-Capability'])
      .toBe(firstOptions?.headers?.['X-Ananta-Membership-Capability']);
    expect(JSON.stringify(firstOptions?.body)).not.toContain(
      String(firstOptions?.headers?.['X-Ananta-Membership-Capability']),
    );
  });

  it('makes no network request when pending capability persistence is unavailable', () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    const core = TestBed.inject(HubApiCoreService);
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('denied');
    });
    try {
      expect(() => service.create({ title: 'Pair' }))
        .toThrow('public_membership_capability_storage_unavailable');
      expect(core.request).not.toHaveBeenCalled();
    } finally {
      setItem.mockRestore();
    }
  });

  it('does not reuse pending proof for changed intent or another OIDC subject', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.request).mockReturnValueOnce(throwError(() => ({ status: 503 })));
    const original = {
      title: 'Pair', permissions: { chat: true }, public_key_fingerprint: 'fingerprint',
    };
    await expect(firstValueFrom(service.create(original))).rejects.toMatchObject({ status: 503 });
    const callCount = vi.mocked(core.request).mock.calls.length;

    expect(() => service.create({ ...original, title: 'Changed' }))
      .toThrow('public_pair_pending_attempt_conflict');
    expect(vi.mocked(core.request).mock.calls).toHaveLength(callCount);

    auth.oidcAccessTokenValue = jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'another-account',
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    expect(() => service.create(original)).toThrow('public_pair_pending_attempt_conflict');
    expect(vi.mocked(core.request).mock.calls).toHaveLength(callCount);
  });

  it.each([
    'invalid_invite_code',
    'peer_identity_must_be_distinct',
    'device_key_must_be_distinct',
  ])('retires the definitive 400 %s so a corrected join can start', async (reason) => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.request).mockReturnValueOnce(throwError(() => ({
      status: 400,
      error: { error: reason },
    })));

    await expect(firstValueFrom(service.join({ invite_code: 'WRONG' })))
      .rejects.toMatchObject({ status: 400 });
    await expect(firstValueFrom(service.join({ invite_code: 'CORRECT' })))
      .resolves.toMatchObject({ id: 'joined-session', local_peer_id: joinerPeerId });
  });

  it.each([0, 401, 409, 500, 503])(
    'retains an unresolved join across HTTP status %s and reuses its exact proof',
    async (status) => {
      const service = TestBed.inject(PairSessionControlPlaneService);
      const core = TestBed.inject(HubApiCoreService);
      vi.mocked(core.request).mockReturnValueOnce(throwError(() => ({
        status,
        error: { error: 'peer_identity_must_be_distinct' },
      })));

      await expect(firstValueFrom(service.join({ invite_code: 'ORIGINAL' })))
        .rejects.toMatchObject({ status });
      const failedOptions = vi.mocked(core.request).mock.calls[0][3];
      const callCount = vi.mocked(core.request).mock.calls.length;

      expect(() => service.join({ invite_code: 'CHANGED' }))
        .toThrow('public_pair_pending_attempt_conflict');
      expect(vi.mocked(core.request).mock.calls).toHaveLength(callCount);

      await expect(firstValueFrom(service.join({ invite_code: 'ORIGINAL' })))
        .resolves.toMatchObject({ id: 'joined-session', local_peer_id: joinerPeerId });
      const retryOptions = vi.mocked(core.request).mock.calls[1][3];
      expect(retryOptions?.body).toEqual(failedOptions?.body);
      expect(retryOptions?.headers?.['X-Ananta-Membership-Capability'])
        .toBe(failedOptions?.headers?.['X-Ananta-Membership-Capability']);
    },
  );

  it('retains a lost-response attempt through 401 and recovers after same-subject reauthentication', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.request)
      .mockReturnValueOnce(throwError(() => ({ status: 0 })))
      .mockReturnValueOnce(throwError(() => ({
        status: 401,
        error: { error: 'unauthorized' },
      })));
    const body = { title: 'Pair', permissions: { chat: true } };

    await expect(firstValueFrom(service.create(body))).rejects.toMatchObject({ status: 0 });
    await expect(firstValueFrom(service.create(body))).rejects.toMatchObject({ status: 401 });
    auth.oidcAccessTokenValue = jwt({
      iss: PUBLIC_OIDC_ISSUER,
      sub: 'raw-oidc-sub',
      exp: Math.floor(Date.now() / 1000) + 7200,
    });
    await expect(firstValueFrom(service.create(body)))
      .resolves.toMatchObject({ id: 'created-session', local_peer_id: ownerPeerId });

    const options = vi.mocked(core.request).mock.calls.map(call => call[3]);
    expect(options[1]?.body).toEqual(options[0]?.body);
    expect(options[2]?.body).toEqual(options[0]?.body);
    expect(options[1]?.headers?.['X-Ananta-Membership-Capability'])
      .toBe(options[0]?.headers?.['X-Ananta-Membership-Capability']);
    expect(options[2]?.headers?.['X-Ananta-Membership-Capability'])
      .toBe(options[0]?.headers?.['X-Ananta-Membership-Capability']);
  });

  it('retains pending proof after a committed-looking invalid response until explicit discard', async () => {
    createdResponse = publicSession('created-session', legacyPeerId, 1);
    const service = TestBed.inject(PairSessionControlPlaneService);

    await expect(firstValueFrom(service.create({ title: 'Pair' })))
      .rejects.toThrow('public_local_peer_id_mismatch');
    expect(Object.keys(sessionStorage).some(key => key.includes('.pending.v2.create'))).toBe(true);

    service.discardPendingPublicMutation('create');
    expect(Object.keys(sessionStorage).some(key => key.includes('.pending.v2.create'))).toBe(false);
  });

  it('skips v2 discovery metadata that has no matching local capability', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);

    await expect(firstValueFrom(service.list())).resolves.toEqual([]);
    expect(requests[0]).toMatchObject({
      method: 'POST',
      url: 'https://webrtc.ananta.de/rendezvous/sessions/catalog',
      body: { memberships: [] },
    });
    expect(requests[0].headers).toBeUndefined();
    expect(() => service.peerIdForSession('listed-session'))
      .toThrow('pair_control_plane_binding_missing');
  });

  it('restores a legacy v1 binding without a capability and never adds a v2 secret header', async () => {
    listedResponses = [publicSession('legacy-session', legacyPeerId, 1)];
    const service = TestBed.inject(PairSessionControlPlaneService);
    await expect(firstValueFrom(service.list())).resolves.toEqual([
      expect.objectContaining({ id: 'legacy-session', local_peer_id: legacyPeerId }),
    ]);
    expect(requests[0]).toMatchObject({
      method: 'POST',
      url: 'https://webrtc.ananta.de/rendezvous/sessions/catalog',
      body: { memberships: [] },
    });
    requests.length = 0;

    await firstValueFrom(service.participants('legacy-session'));
    expect(service.requiresSignalEpoch('legacy-session')).toBe(false);
    expect(requests[0].headers).toEqual({ 'X-Ananta-Peer-Id': legacyPeerId });
    expect(requests[0].context?.get(SUPPRESS_GLOBAL_ERROR_NOTIFICATION)).toBe(true);
  });

  it('does not amplify caller-owned public read failures with an implicit HTTP retry', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    const core = TestBed.inject(HubApiCoreService);
    const terminal = { status: 404, error: { error: 'session_not_found' } };
    let participantAttempts = 0;
    vi.mocked(core.request).mockReturnValueOnce(defer(() => {
      participantAttempts += 1;
      return throwError(() => terminal);
    }));

    await expect(firstValueFrom(service.participants('created-session'))).rejects.toBe(terminal);
    expect(participantAttempts).toBe(1);

    let securityAttempts = 0;
    vi.mocked(core.request).mockReturnValueOnce(defer(() => {
      securityAttempts += 1;
      return throwError(() => terminal);
    }));
    await expect(firstValueFrom(service.securityGet('created-session', 'contract')))
      .rejects.toBe(terminal);
    expect(securityAttempts).toBe(1);
  });

  it('rejects a bound response for another device peer', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.request).mockReturnValueOnce(of({
      ok: true,
      local_peer_id: joinerPeerId,
      data: { participants: [], local_peer_id: joinerPeerId },
    }));

    await expect(firstValueFrom(service.participants('created-session')))
      .rejects.toThrow('public_local_peer_id_mismatch');
  });

  it('rejects a capability leaked inside a bound response payload', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    const core = TestBed.inject(HubApiCoreService);
    vi.mocked(core.request).mockReturnValueOnce(of({
      ok: true,
      local_peer_id: ownerPeerId,
      data: {
        participants: [],
        local_peer_id: ownerPeerId,
        membership_capability: 'X'.repeat(43),
      },
    }));

    await expect(firstValueFrom(service.participants('created-session')))
      .rejects.toThrow('public_membership_capability_exposed');
  });

  it('keeps capability on local leave and retires it only after confirmed end', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    const capabilities = TestBed.inject(PairMembershipCapabilityStore);
    const capability = capabilities.require('created-session', ownerPeerId, capabilityScope());

    service.forgetSession('created-session');
    expect(capabilities.require('created-session', ownerPeerId, capabilityScope())).toBe(capability);
    listedResponses = [publicSession('created-session', ownerPeerId)];
    await firstValueFrom(service.list());
    await firstValueFrom(service.end('created-session'));

    expect(() => capabilities.require('created-session', ownerPeerId, capabilityScope()))
      .toThrow('public_membership_capability_missing');
    expect(() => service.peerIdForSession('created-session'))
      .toThrow('pair_control_plane_binding_missing');
  });

  it('leaves only the exact authenticated public membership and retires its local proof', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    requests.length = 0;

    await firstValueFrom(service.leave('created-session'));

    expect(requests).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      method: 'DELETE',
      url: 'https://webrtc.ananta.de/rendezvous/sessions/created-session/membership',
      headers: {
        'X-Ananta-Peer-Id': ownerPeerId,
      },
    });
    expect(requests[0].headers?.['X-Ananta-Membership-Capability'])
      .toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(() => service.peerIdForSession('created-session'))
      .toThrow('pair_control_plane_binding_missing');
  });

  it('retries one ambiguous public membership leave sequentially', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    const core = TestBed.inject(HubApiCoreService);
    let attempts = 0;
    vi.mocked(core.request).mockReturnValueOnce(defer(() => {
      attempts += 1;
      return attempts === 1
        ? throwError(() => ({ status: 0 }))
        : of({ ok: true, local_peer_id: ownerPeerId, idempotent: true });
    }));

    await firstValueFrom(service.leave('created-session'));

    expect(attempts).toBe(2);
    expect(() => service.peerIdForSession('created-session'))
      .toThrow('pair_control_plane_binding_missing');
  });

  it('retries one RxJS timeout for an idempotent public membership leave', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    const core = TestBed.inject(HubApiCoreService);
    let attempts = 0;
    vi.mocked(core.request).mockReturnValueOnce(defer(() => {
      attempts += 1;
      return attempts === 1
        ? throwError(() => new TimeoutError())
        : of({ ok: true, local_peer_id: ownerPeerId, idempotent: true });
    }));

    await firstValueFrom(service.leave('created-session'));

    expect(attempts).toBe(2);
  });

  it('retires unusable local authority but still surfaces a forbidden leave', async () => {
    const service = TestBed.inject(PairSessionControlPlaneService);
    await firstValueFrom(service.create({ title: 'Pair' }));
    const core = TestBed.inject(HubApiCoreService);
    const forbidden = { status: 403, error: { error: 'forbidden' } };
    vi.mocked(core.request).mockReturnValueOnce(throwError(() => forbidden));

    await expect(firstValueFrom(service.leave('created-session'))).rejects.toBe(forbidden);
    expect(() => service.peerIdForSession('created-session'))
      .toThrow('pair_control_plane_binding_missing');
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

function seedBoundCapability(
  sessionId: string,
  localPeerId: string,
  oidcSubject = 'raw-oidc-sub',
): string {
  const store = TestBed.inject(PairMembershipCapabilityStore);
  const scope = {
    kind: 'create' as const,
    baseUrl: 'https://webrtc.ananta.de',
    oidcIssuer: PUBLIC_OIDC_ISSUER,
    oidcSubject,
  };
  const pending = store.begin(scope, { restore: sessionId });
  store.promote(scope, sessionId, localPeerId, pending.capability);
  return pending.capability;
}

function capabilityScope() {
  return {
    baseUrl: 'https://webrtc.ananta.de',
    oidcIssuer: PUBLIC_OIDC_ISSUER,
    oidcSubject: 'raw-oidc-sub',
  };
}

function publicSession(
  id: string,
  localPeerId = `peer:${'a'.repeat(64)}`,
  identityBindingVersion: 1 | 2 = 2,
  localRole: 'owner' | 'participant' = 'owner',
  localRuntimeState: 'active' | 'parked' = 'active',
): Record<string, unknown> {
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
    identity_binding_version: identityBindingVersion,
    permissions_version: 1,
    mode: 'p2p',
    transport: 'webrtc',
    permissions,
    allowed_permissions: { ...permissions },
    local_peer_id: localPeerId,
    security_epoch: 3,
    ...(identityBindingVersion === 2 ? {
      local_peer_ids: [localPeerId],
      local_role: localRole,
      local_runtime_state: localRuntimeState,
    } : {}),
  };
}

function jwt(payload: Record<string, unknown>): string {
  const encode = (value: object) => btoa(JSON.stringify(value)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
  return `${encode({ alg: 'none' })}.${encode(payload)}.signature`;
}
