import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { PeerCapabilityService } from '../../services/peer-capability.service';
import { SemanticComputeContractApiService } from '../../services/semantic-compute-contract-api.service';
import { SemanticComputeIntentFacade } from './semantic-compute-intent.facade';

const advertisement = {
  schema: 'ananta.semantic-capability-advertisement.v1' as const,
  advertisement_id: 'cap-a', session_id: 'session-a', epoch: 3, sender_id: 'alice',
  algorithms: ['heuristic-visual-v1'], roles: ['executor' as const], task_types: ['visual_extract' as const],
  resource_profile: { cpu: 'medium' as const, memory: 'medium' as const, gpu: 'unknown' as const,
    codec: 'unknown' as const, battery: 'unknown' as const, network: 'normal' as const },
  measurements_expires_at_ms: Date.now() + 60_000, expires_at_ms: Date.now() + 60_000,
  max_delay_ms: 10_000, max_artifact_bytes: 1_048_576,
  signature: { algorithm: 'ed25519' as const, key_id: 'cap-key', value: 'a'.repeat(64) },
};

function contract(status: 'offered' | 'accepted' | 'active' | 'revoked' = 'offered', revision = 1) {
  return {
    contract_id: 'semantic-contract-a', session_id: 'session-a', room_id: null, epoch: 3, revision,
    digest: 'a'.repeat(64), status, profile: 'balanced' as const, quality_level: 'standard' as const,
    security_mode: 'strict_e2ee' as const, consent_version: 2, policy_version: 'semantic-compute-v1',
    delay_ms: 5_000, roles: {}, task_types: ['visual_extract'], max_artifact_bytes: 1_048_576,
    deadline_ms: 5_000, expires_at_ms: Date.now() + 60_000, reason_code: null,
  };
}

function grant(
  capability: 'publish' | 'subscribe' | 'compute' | 'validate',
  patch: Partial<{ epoch: number; scope_kind: 'session' | 'room'; scope_id: string }> = {},
) {
  return {
    grant_id: `grant-${capability}`, subject_id: 'alice', capability,
    scope_kind: 'session' as const, scope_id: 'session-a',
    direction: capability === 'subscribe' ? 'ingress' as const : 'egress' as const,
    epoch: 3, expires_at_ms: Date.now() + 300_000, revoked: false, ...patch,
  };
}

describe('SemanticComputeIntentFacade', () => {
  const api = {
    issueCapabilityGrant: vi.fn(),
    listCapabilityGrants: vi.fn(),
    revokeCapabilityGrant: vi.fn(),
    list: vi.fn(),
    createOffer: vi.fn(),
    mutate: vi.fn(),
    registerCandidateKey: vi.fn(),
    advertiseCapability: vi.fn(),
    candidateClaims: vi.fn(),
    schedule: vi.fn(),
    leases: vi.fn(),
    explain: vi.fn(),
    suggest: vi.fn(),
  };
  const capability = { measureAndAdvertise: vi.fn(), stop: vi.fn() };
  let facade: SemanticComputeIntentFacade;

  beforeEach(() => {
    vi.clearAllMocks();
    api.listCapabilityGrants.mockReturnValue(of([]));
    api.issueCapabilityGrant.mockImplementation((_hub: string, value: {
      capability: ReturnType<typeof grant>['capability']; epoch: number; roomId?: string;
    }) => (
      of(grant(value.capability, {
        epoch: value.epoch,
        scope_kind: value.roomId ? 'room' : 'session',
        scope_id: value.roomId ?? 'session-a',
      }))
    ));
    api.revokeCapabilityGrant.mockImplementation((_hub: string, grantId: string) => of({
      ...grant(grantId.replace('grant-', '') as ReturnType<typeof grant>['capability']), revoked: true,
    }));
    api.list.mockReturnValue(of([]));
    api.createOffer.mockReturnValue(of(contract()));
    api.mutate.mockReturnValue(of(contract('revoked', 2)));
    api.registerCandidateKey.mockReturnValue(of(undefined));
    api.advertiseCapability.mockReturnValue(of(undefined));
    api.candidateClaims.mockReturnValue(of([]));
    api.schedule.mockReturnValue(of([]));
    api.leases.mockReturnValue(of([]));
    api.explain.mockImplementation((_hub: string, value: ReturnType<typeof contract>) => of({
      state: value.status,
      reason_code: value.status === 'revoked' ? 'revoked_by_user' : 'offer_accepted',
      message: 'Hub explanation',
      revision: value.revision,
      contract_digest: value.digest,
      profile: value.profile,
      delay_ms: value.delay_ms,
      authoritative_source: 'hub' as const,
    }));
    api.suggest.mockReturnValue(of({
      authoritative: false,
      requires_separate_hub_mutation: true,
      suggested_values: { profile: 'conservative', delay_ms: 5_000 },
      rationale: 'Reduce load',
    }));
    capability.measureAndAdvertise.mockImplementation(async (options: {
      signer: { sign(payload: Readonly<Record<string, unknown>>): Promise<typeof advertisement.signature> };
    }) => {
      const { signature: _discarded, ...unsigned } = advertisement;
      return { ...unsigned, signature: await options.signer.sign(unsigned) };
    });
    TestBed.configureTestingModule({ providers: [
      SemanticComputeIntentFacade,
      { provide: SemanticComputeContractApiService, useValue: api },
      { provide: PeerCapabilityService, useValue: capability },
    ] });
    facade = TestBed.inject(SemanticComputeIntentFacade);
  });

  afterEach(() => facade.ngOnDestroy());

  it('creates a real Hub offer from the first profile intent and exposes coarse local inputs', async () => {
    facade.bind({ hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 3, senderId: 'alice', consentVersion: 2 });
    await vi.waitFor(() => expect(facade.state$.value.pending).toBe(false));
    await facade.handleIntent({ kind: 'profile', expectedRevision: 0, value: 'balanced' });
    expect(api.createOffer).toHaveBeenCalledOnce();
    expect(api.registerCandidateKey).toHaveBeenCalledOnce();
    expect(api.advertiseCapability).toHaveBeenCalledOnce();
    expect(api.registerCandidateKey.mock.calls[0][1].keyId)
      .toBe(api.advertiseCapability.mock.calls[0][1].signature.key_id);
    expect(api.createOffer.mock.calls[0][1]).toMatchObject({
      sessionId: 'session-a', consentVersion: 2, proposal: { profile: 'balanced', security_mode: 'strict_e2ee' },
    });
    expect(facade.state$.value.localMeasurement).toMatchObject({ cpu: 'medium', memory: 'medium' });
    expect(facade.state$.value.contract.status).toBe('offered');
  });

  it('never makes safe revoke depend on a new browser measurement', async () => {
    api.list.mockReturnValue(of([contract()]));
    facade.bind({ hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 3, senderId: 'alice', consentVersion: 2 });
    await vi.waitFor(() => expect(facade.state$.value.pending).toBe(false));
    capability.measureAndAdvertise.mockClear();
    await facade.handleIntent({ kind: 'revoke', expectedRevision: 1 });
    expect(capability.measureAndAdvertise).not.toHaveBeenCalled();
    expect(api.mutate.mock.calls[0][2]).toBe('revoke');
    expect(facade.state$.value.contract.status).toBe('revoked');
    expect(api.leases).toHaveBeenCalled();
  });

  it('asks the Hub to schedule and then displays only Hub-read leases on activation', async () => {
    const accepted = contract('accepted');
    api.list.mockReturnValue(of([accepted]));
    api.mutate.mockReturnValue(of(contract('active', 2)));
    api.leases.mockReturnValue(of([{
      lease_id: 'lease-a', contract_id: 'semantic-contract-a', contract_digest: 'a'.repeat(64),
      session_id: 'session-a', epoch: 3, task_type: 'visual_extract', audience: 'alice', role: 'primary',
      executor_id: 'alice', fencing_token: 1, resource_budget: {
        cpu_ms: 1_000, memory_bytes: 67_108_864, artifact_bytes: 65_536,
      }, status: 'active', expires_at_ms: Date.now() + 5_000, deadline_at_ms: Date.now() + 5_000, version: 1,
    }]));
    facade.bind({ hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 3, senderId: 'alice', consentVersion: 2 });
    await vi.waitFor(() => expect(facade.state$.value.pending).toBe(false));
    await facade.handleIntent({ kind: 'activate', expectedRevision: 1 });
    expect(api.schedule).toHaveBeenCalledOnce();
    expect(facade.state$.value.leases).toEqual([
      expect.objectContaining({ leaseId: 'lease-a', executorId: 'alice', status: 'active' }),
    ]);
  });

  it('consumes redacted explanations and keeps suggestions non-authoritative until a normal intent', async () => {
    api.list.mockReturnValue(of([contract()]));
    facade.bind({ hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 3, senderId: 'alice', consentVersion: 2 });
    await vi.waitFor(() => expect(facade.state$.value.pending).toBe(false));
    await facade.requestSuggestion();
    expect(api.explain).toHaveBeenCalled();
    expect(api.suggest).toHaveBeenCalledOnce();
    expect(api.mutate).not.toHaveBeenCalled();
    expect(facade.state$.value.suggestion).toMatchObject({
      profile: 'conservative', authoritative: false, requiresSeparateHubMutation: true,
    });
  });

  it('invalidates and revokes cached Hub authority when the session epoch changes', async () => {
    facade.bind({ hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 3, senderId: 'alice', consentVersion: 2 });
    await vi.waitFor(() => expect(facade.state$.value.pending).toBe(false));
    expect(api.issueCapabilityGrant).toHaveBeenCalledWith(
      'http://hub.test', expect.objectContaining({ capability: 'subscribe', epoch: 3 }), expect.any(String),
    );
    facade.bind({ hubUrl: 'http://hub.test', sessionId: 'session-a', epoch: 4, senderId: 'alice', consentVersion: 2 });
    expect(api.revokeCapabilityGrant).toHaveBeenCalledWith('http://hub.test', 'grant-subscribe');
    await vi.waitFor(() => expect(api.listCapabilityGrants)
      .toHaveBeenLastCalledWith('http://hub.test', 'session-a', 4, undefined));
  });
});
