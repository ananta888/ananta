import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { AgentDirectoryService } from './agent-directory.service';
import { HubApiCoreService } from './hub-api-core.service';
import {
  NetworkProfileService,
  SEMANTIC_MEDIA_FEATURE_DEFAULTS,
  normalizeIceServers,
  normalizePairTransportOrder,
  normalizeSemanticMediaFeatureFlags,
} from './network-profile.service';

describe('NetworkProfileService semantic media flags', () => {
  afterEach(() => localStorage.clear());

  it('advertises no Hub relay capability for the public rendezvous profile', () => {
    expect(normalizePairTransportOrder({
      profile_id: 'public-ananta', public_rendezvous: true, transport_order: ['webrtc', 'hub_relay'],
    })).toEqual(['webrtc']);
  });
  it('drops credential-less TURN entries before constructing RTCPeerConnection', () => {
    expect(normalizeIceServers([
      { urls: 'turn:webrtc.invalid:3478' },
      { urls: ['stun:webrtc.invalid:3478', 'turns:webrtc.invalid:5349'] },
      { urls: 'turn:webrtc.invalid:3478', username: 'peer', credential: 'secret' },
    ])).toEqual([
      { urls: ['stun:webrtc.invalid:3478'] },
      { urls: 'turn:webrtc.invalid:3478', username: 'peer', credential: 'secret' },
    ]);
  });

  it('fails closed for missing, malformed and dependency-inconsistent values', () => {
    expect(normalizeSemanticMediaFeatureFlags(undefined)).toEqual(SEMANTIC_MEDIA_FEATURE_DEFAULTS);
    expect(normalizeSemanticMediaFeatureFlags({ semantic_visual_capture: 'true' }))
      .toEqual(SEMANTIC_MEDIA_FEATURE_DEFAULTS);
    expect(normalizeSemanticMediaFeatureFlags({ speech_adapter_routing: true }).speech_adapter_routing)
      .toBe(false);
  });

  it('starts private and retains the all-false fallback when the Hub cannot be reached', async () => {
    TestBed.configureTestingModule({
      providers: [
        NetworkProfileService,
        { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub' }] } },
        { provide: HubApiCoreService, useValue: { get: () => throwError(() => new Error('offline')) } },
      ],
    });
    const service = TestBed.inject(NetworkProfileService);
    await service.load();
    expect(service.current.profile_id).toBe('local');
    expect(service.current.public_rendezvous).toBe(false);
    expect(service.current.semantic_media_feature_flags).toEqual(SEMANTIC_MEDIA_FEATURE_DEFAULTS);
  });

  it('uses the public fallback only after an explicit persisted opt-in', async () => {
    TestBed.configureTestingModule({
      providers: [
        NetworkProfileService,
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
        { provide: HubApiCoreService, useValue: { get: () => { throw new Error('must-not-run'); } } },
      ],
    });
    const service = TestBed.inject(NetworkProfileService);

    expect(service.current.profile_id).toBe('local');
    await service.enablePublicPair();

    expect(service.current.profile_id).toBe('public-ananta');
    expect(service.current.public_rendezvous).toBe(true);
    expect(service.current.transport_order).toEqual(['webrtc']);
    expect(service.publicPairOptedIn).toBe(true);
  });

  it('does not let a direct load call bypass the public opt-in', async () => {
    TestBed.configureTestingModule({
      providers: [
        NetworkProfileService,
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
        { provide: HubApiCoreService, useValue: { get: () => { throw new Error('must-not-run'); } } },
      ],
    });
    const service = TestBed.inject(NetworkProfileService);

    await service.load('public-ananta');

    expect(service.current.profile_id).toBe('local');
    expect(service.publicPairOptedIn).toBe(false);
  });

  it('restores an explicit public selection but never invents one', async () => {
    localStorage.setItem('ananta.network-profile-selection.v1', 'public-ananta');
    TestBed.configureTestingModule({
      providers: [
        NetworkProfileService,
        { provide: AgentDirectoryService, useValue: { list: () => [] } },
        { provide: HubApiCoreService, useValue: { get: () => { throw new Error('must-not-run'); } } },
      ],
    });
    const service = TestBed.inject(NetworkProfileService);

    await service.load();
    expect(service.current.profile_id).toBe('public-ananta');

    await service.useLocalProfile();
    expect(service.current.profile_id).toBe('local');
    expect(localStorage.getItem('ananta.network-profile-selection.v1')).toBeNull();
  });

  it('keeps the pinned public profile independent from mutable Hub projections', async () => {
    const profile = {
      profile_id: 'public-ananta', label: 'Test', oidc: {
        issuer: '', client_id: '', audience: '', pkce_required: true,
      },
      rendezvous: { base_url: '', signaling_url: '', transport_order: ['hub_relay'] },
      ice_servers: [], require_e2e_payload_encryption: true, signaling_url: '',
      transport_order: ['hub_relay'], warning: '',
      semantic_media_feature_flags: {
        ...SEMANTIC_MEDIA_FEATURE_DEFAULTS,
        semantic_media_broadcast: true,
        semantic_media_receiver_groups: true,
        semantic_media_fleet_admission: true,
        semantic_media_turn_cost_controls: true,
        semantic_speech_runtime: true,
        semantic_media_background_operations: true,
        peer_evidence_sync: true,
      },
    };
    const get = vi.fn(() => of({ ok: true, profile }));
    TestBed.configureTestingModule({
      providers: [
        NetworkProfileService,
        { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub' }] } },
        { provide: HubApiCoreService, useValue: { get } },
      ],
    });
    const service = TestBed.inject(NetworkProfileService);
    await service.enablePublicPair();
    await service.load();
    expect(service.current.semantic_media_feature_flags).toEqual(SEMANTIC_MEDIA_FEATURE_DEFAULTS);
    expect(service.current.rendezvous?.base_url).toBe('https://webrtc.ananta.de');
    expect(get).not.toHaveBeenCalled();
  });

  it('accepts a dependency-complete semantic media projection for a private profile', async () => {
    const profile = {
      profile_id: 'local', label: 'Private', oidc: {
        issuer: '', client_id: '', audience: '', pkce_required: true,
      },
      rendezvous: { base_url: '', signaling_url: '', transport_order: ['hub_relay'] },
      ice_servers: [], require_e2e_payload_encryption: false, signaling_url: '',
      transport_order: ['hub_relay'], warning: '',
      semantic_media_feature_flags: {
        ...SEMANTIC_MEDIA_FEATURE_DEFAULTS,
        semantic_media_broadcast: true,
        semantic_media_receiver_groups: true,
        semantic_media_fleet_admission: true,
        semantic_media_turn_cost_controls: true,
        semantic_speech_runtime: true,
        semantic_media_background_operations: true,
        peer_evidence_sync: true,
      },
    };
    TestBed.configureTestingModule({
      providers: [
        NetworkProfileService,
        { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub' }] } },
        { provide: HubApiCoreService, useValue: { get: () => of({ ok: true, profile }) } },
      ],
    });
    const service = TestBed.inject(NetworkProfileService);
    await service.load();

    expect(service.current.semantic_media_feature_flags.peer_evidence_sync).toBe(true);
    expect(service.current.semantic_media_feature_flags.speech_reconciliation).toBe(false);
    expect(service.current.semantic_media_feature_flags.semantic_media_broadcast).toBe(true);
    expect(service.current.semantic_media_feature_flags.semantic_media_receiver_groups).toBe(true);
    expect(service.current.semantic_media_feature_flags.semantic_media_fleet_admission).toBe(true);
    expect(service.current.semantic_media_feature_flags.semantic_media_turn_cost_controls).toBe(true);
  });
});
