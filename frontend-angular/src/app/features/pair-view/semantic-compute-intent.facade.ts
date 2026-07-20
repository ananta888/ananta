import { Injectable, OnDestroy, inject } from '@angular/core';
import { BehaviorSubject, Subscription, firstValueFrom } from 'rxjs';

import {
  CapabilityView,
  ComputeExplanationView,
  ComputeContractIntent,
  ComputeContractView,
  ComputeLeaseView,
  ComputeProfile,
  ComputeSuggestionView,
} from './pair-compute-contract-panel.component';
import {
  SemanticComputeContractApiService,
  SemanticComputeCapability,
  SemanticComputeCapabilityGrantReadModel,
  SemanticComputeContractReadModel,
  SemanticComputeLeaseReadModel,
} from '../../services/semantic-compute-contract-api.service';
import { CapabilityAdvertisement } from '../../services/peer-capability.types';
import { CapabilitySigner, PeerCapabilityService } from '../../services/peer-capability.service';

export interface SemanticComputePanelState {
  readonly contract: ComputeContractView;
  readonly localMeasurement?: CapabilityView;
  readonly peerClaim?: CapabilityView;
  readonly leases: readonly ComputeLeaseView[];
  readonly explanation?: ComputeExplanationView;
  readonly suggestion?: ComputeSuggestionView;
  readonly pending: boolean;
  readonly errorCode: string | null;
}

export interface SemanticComputeSessionContext {
  readonly hubUrl: string;
  readonly sessionId: string;
  readonly roomId?: string;
  readonly epoch: number;
  readonly senderId: string;
  readonly consentVersion: number;
}

const ABSENT_CONTRACT: ComputeContractView = Object.freeze({
  contractId: '', revision: 0, status: 'absent', profile: 'off', delayMs: 5_000, roles: {},
});

@Injectable()
export class SemanticComputeIntentFacade implements OnDestroy {
  private readonly api = inject(SemanticComputeContractApiService);
  private readonly capabilities = inject(PeerCapabilityService);
  private readonly subscriptions = new Subscription();
  private context: SemanticComputeSessionContext | null = null;
  private advertisement: CapabilityAdvertisement | null = null;
  private signer = new BrowserCapabilitySigner();
  private expiryTimer: ReturnType<typeof setTimeout> | null = null;
  private serial = 0;
  private readonly grants = new Map<SemanticComputeCapability, SemanticComputeCapabilityGrantReadModel>();
  private readonly grantRequests = new Map<SemanticComputeCapability, Promise<SemanticComputeCapabilityGrantReadModel>>();
  private grantsHydrated = false;
  private grantHydration: Promise<void> | null = null;

  readonly state$ = new BehaviorSubject<SemanticComputePanelState>(Object.freeze({
    contract: ABSENT_CONTRACT,
    leases: Object.freeze([]),
    pending: false,
    errorCode: null,
  }));

  bind(context: SemanticComputeSessionContext | null): void {
    this.releaseCapabilityGrants();
    this.clearExpiryTimer();
    this.capabilities.stop('semantic_compute_context_changed');
    this.context = context ? Object.freeze({ ...context }) : null;
    this.advertisement = null;
    this.grants.clear();
    this.grantRequests.clear();
    this.grantsHydrated = false;
    this.grantHydration = null;
    this.signer = new BrowserCapabilitySigner();
    this.state$.next(Object.freeze({
      contract: ABSENT_CONTRACT,
      leases: Object.freeze([]),
      pending: false,
      errorCode: context ? null : 'compute_session_missing',
    }));
    if (context) void this.refresh();
  }

  async refresh(): Promise<void> {
    const context = this.context;
    if (!context || this.state$.value.pending) return;
    this.patch({ pending: true, errorCode: null });
    try {
      const grant = await this.requireCapability(context, 'subscribe');
      const contracts = await firstValueFrom(
        this.api.list(context.hubUrl, context.sessionId, context.epoch, grant.grant_id),
      );
      if (context !== this.context) return;
      const selected = [...contracts].sort((left, right) => right.revision - left.revision)[0] ?? null;
      if (!selected) {
        this.clearExpiryTimer();
        this.patch({ contract: ABSENT_CONTRACT, leases: Object.freeze([]), pending: false });
        return;
      }
      this.patch({ contract: contractView(selected) });
      await this.loadAuthoritativeDetails(context, selected);
      if (context === this.context) this.patch({ pending: false });
    } catch (error) {
      this.invalidateCapabilityFromError(error);
      if (context === this.context) this.patch({ pending: false, errorCode: reasonCode(error, 'compute_load_failed') });
    }
  }

  async handleIntent(intent: ComputeContractIntent): Promise<void> {
    const context = this.context;
    const current = this.state$.value.contract;
    if (!context || this.state$.value.pending || intent.expectedRevision !== current.revision) return;
    this.patch({ pending: true, errorCode: null });
    try {
      const needsAdvertisement = current.revision === 0 || intent.kind === 'accept' || intent.kind === 'activate';
      const advertisement = needsAdvertisement ? await this.measure(context) : null;
      let updated: SemanticComputeContractReadModel;
      if (current.revision === 0) {
        if (intent.kind !== 'profile' && intent.kind !== 'delay') throw new Error('compute_contract_missing');
        const profile = intent.kind === 'profile' ? intent.value as ComputeProfile : 'conservative';
        const delayMs = intent.kind === 'delay' ? Number(intent.value) : 5_000;
        const publishGrant = await this.requireCapability(context, 'publish');
        updated = await firstValueFrom(this.api.createOffer(context.hubUrl, {
          sessionId: context.sessionId,
          roomId: context.roomId,
          epoch: context.epoch,
          policyVersion: 'semantic-compute-v1',
          consentVersion: context.consentVersion,
          proposal: proposal(profile, delayMs),
          advertisements: [advertisement as unknown as Readonly<Record<string, unknown>>],
        }, this.key('offer'), publishGrant.grant_id));
      } else {
        const action = actionFor(intent.kind);
        const mutationProposal = proposalForIntent(intent, current);
        const publishGrant = await this.requireCapability(context, 'publish');
        updated = await firstValueFrom(this.api.mutate(
          context.hubUrl,
          current.contractId,
          action,
          {
            sessionId: context.sessionId,
            epoch: context.epoch,
            expectedRevision: current.revision,
            consentVersion: context.consentVersion,
            proposal: mutationProposal,
            advertisements: action === 'accept' || action === 'activate'
              ? [advertisement as unknown as Readonly<Record<string, unknown>>]
              : [],
          },
          this.key(action),
          publishGrant.grant_id,
        ));
      }
      if (context !== this.context) return;
      this.patch({
        contract: contractView(updated),
        leases: Object.freeze([]),
        explanation: undefined,
        suggestion: undefined,
      });
      if (updated.status === 'active' && intent.kind === 'activate') {
        const computeGrant = await this.requireCapability(context, 'compute');
        await firstValueFrom(this.api.schedule(
          context.hubUrl,
          updated,
          {
            sessionId: context.sessionId,
            epoch: context.epoch,
            taskType: preferredTaskType(updated),
            audience: context.senderId,
            sequenceStart: 0,
            sequenceEnd: 0,
            deadlineEpochMs: Date.now() + Math.max(100, updated.deadline_ms - 250),
            resourceBudget: {
              cpu_ms: Math.min(2_000, Math.max(100, updated.deadline_ms)),
              memory_bytes: 64 * 1024 * 1024,
              artifact_bytes: Math.min(64 * 1024, updated.max_artifact_bytes),
            },
          },
          this.key('schedule'),
          computeGrant.grant_id,
        ));
      }
      await this.loadAuthoritativeDetails(context, updated);
      if (context === this.context) this.patch({ pending: false, errorCode: null });
    } catch (error) {
      this.invalidateCapabilityFromError(error);
      if (context === this.context) this.patch({ pending: false, errorCode: reasonCode(error, 'compute_mutation_failed') });
    }
  }

  ngOnDestroy(): void {
    this.releaseCapabilityGrants();
    this.clearExpiryTimer();
    this.subscriptions.unsubscribe();
    this.capabilities.stop('semantic_compute_facade_destroyed');
    this.state$.complete();
  }

  async requestSuggestion(): Promise<void> {
    const context = this.context;
    const contract = this.state$.value.contract;
    if (!context || contract.revision < 1 || this.state$.value.pending) return;
    this.patch({ pending: true, errorCode: null });
    try {
      const current = await this.currentContract(context, contract.contractId);
      const validateGrant = await this.requireCapability(context, 'validate');
      const value = await firstValueFrom(this.api.suggest(context.hubUrl, current, {
        profile: reduceProfile(current.profile),
        delay_ms: current.delay_ms,
        rationale: 'AI-Snake-Vorschlag zur Verringerung lokaler Compute-Last.',
      }, validateGrant.grant_id));
      if (context !== this.context) return;
      this.patch({
        suggestion: {
          profile: value.suggested_values.profile,
          delayMs: value.suggested_values.delay_ms,
          rationale: value.rationale,
          authoritative: false,
          requiresSeparateHubMutation: true,
        },
        pending: false,
      });
    } catch (error) {
      this.invalidateCapabilityFromError(error);
      if (context === this.context) {
        this.patch({ pending: false, errorCode: reasonCode(error, 'compute_suggestion_failed') });
      }
    }
  }

  private async measure(context: SemanticComputeSessionContext): Promise<CapabilityAdvertisement> {
    if (this.advertisement && this.advertisement.expires_at_ms > Date.now() + 2_000) return this.advertisement;
    const value = await this.capabilities.measureAndAdvertise({
      consent: { granted: true, version: context.consentVersion },
      sessionId: context.sessionId,
      roomId: context.roomId,
      epoch: context.epoch,
      senderId: context.senderId,
      limits: { cpu: 'medium', memory: 'medium', maxDelayMs: 20_000, maxArtifactBytes: 1_048_576 },
      algorithms: ['heuristic-visual-v1', 'speech-features-v1'],
      roles: ['executor', 'validator', 'standby'],
      taskTypes: ['visual_extract', 'visual_validate', 'speech_features', 'speech_validate'],
      signer: this.signer,
    });
    const registration = await this.signer.registration(value.expires_at_ms + 30_000);
    const computeGrant = await this.requireCapability(context, 'compute');
    await firstValueFrom(this.api.registerCandidateKey(context.hubUrl, {
      sessionId: context.sessionId,
      epoch: context.epoch,
      ...registration,
    }, computeGrant.grant_id));
    await firstValueFrom(this.api.advertiseCapability(
      context.hubUrl,
      value as unknown as Readonly<Record<string, unknown>>,
      computeGrant.grant_id,
    ));
    if (context !== this.context) throw new Error('compute_context_changed');
    this.advertisement = value;
    this.patch({ localMeasurement: { ...value.resource_profile, expiresAtMs: value.measurements_expires_at_ms } });
    return value;
  }

  private async loadAuthoritativeDetails(
    context: SemanticComputeSessionContext,
    contract: SemanticComputeContractReadModel,
  ): Promise<void> {
    const subscribeGrant = await this.requireCapability(context, 'subscribe');
    const [claims, leases, explanation] = await Promise.all([
      firstValueFrom(this.api.candidateClaims(
        context.hubUrl, context.sessionId, context.epoch, subscribeGrant.grant_id, context.roomId,
      )),
      firstValueFrom(this.api.leases(
        context.hubUrl, contract.contract_id, context.sessionId, context.epoch, subscribeGrant.grant_id,
      )),
      firstValueFrom(this.api.explain(context.hubUrl, contract, subscribeGrant.grant_id)),
    ]);
    if (context !== this.context) return;
    const peer = claims.find(value => value.sender_id !== context.senderId);
    this.patch({
      contract: contractView(contract),
      peerClaim: peer ? { ...peer.resource_profile, expiresAtMs: peer.expires_at_ms } as CapabilityView : undefined,
      leases: Object.freeze(leases.map(leaseView)),
      explanation: {
        message: explanation.message,
        reasonCode: explanation.reason_code,
        authoritativeSource: 'hub',
      },
    });
    this.scheduleExpiryRefresh(context, contract, leases);
  }

  private scheduleExpiryRefresh(
    context: SemanticComputeSessionContext,
    contract: SemanticComputeContractReadModel,
    leases: readonly SemanticComputeLeaseReadModel[],
  ): void {
    this.clearExpiryTimer();
    const now = Date.now();
    const expiries = [
      contract.expires_at_ms,
      ...leases.filter(value => value.status === 'active').map(value => value.expires_at_ms),
    ].filter(value => value > now);
    if (expiries.length === 0) return;
    const delay = Math.min(2_147_000_000, Math.max(1, Math.min(...expiries) - now + 10));
    this.expiryTimer = setTimeout(() => {
      this.expiryTimer = null;
      if (context !== this.context) return;
      const currentNow = Date.now();
      this.patch({
        leases: Object.freeze(this.state$.value.leases.map(value => (
          value.status === 'active' && value.expiresAtMs <= currentNow
            ? Object.freeze({ ...value, status: 'expired' as const })
            : value
        ))),
      });
      void this.refresh();
    }, delay);
  }

  private clearExpiryTimer(): void {
    if (this.expiryTimer !== null) clearTimeout(this.expiryTimer);
    this.expiryTimer = null;
  }

  private async currentContract(
    context: SemanticComputeSessionContext,
    contractId: string,
  ): Promise<SemanticComputeContractReadModel> {
    const subscribeGrant = await this.requireCapability(context, 'subscribe');
    const contracts = await firstValueFrom(
      this.api.list(context.hubUrl, context.sessionId, context.epoch, subscribeGrant.grant_id),
    );
    const current = contracts.find(value => value.contract_id === contractId);
    if (!current) throw new Error('compute_contract_missing');
    return current;
  }

  private async requireCapability(
    context: SemanticComputeSessionContext,
    capability: SemanticComputeCapability,
  ): Promise<SemanticComputeCapabilityGrantReadModel> {
    if (context !== this.context) throw new Error('compute_context_changed');
    await this.hydrateCapabilityGrants(context);
    const cached = this.grants.get(capability);
    if (cached && this.grantMatches(cached, context, capability, Date.now() + 30_000)) return cached;
    const pending = this.grantRequests.get(capability);
    if (pending) return pending;
    const request = this.issueReplacementGrant(context, capability, cached);
    this.grantRequests.set(capability, request);
    try {
      return await request;
    } finally {
      if (this.grantRequests.get(capability) === request) this.grantRequests.delete(capability);
    }
  }

  private async hydrateCapabilityGrants(context: SemanticComputeSessionContext): Promise<void> {
    if (this.grantsHydrated) return;
    if (!this.grantHydration) {
      this.grantHydration = (async () => {
        const rows = await firstValueFrom(this.api.listCapabilityGrants(
          context.hubUrl, context.sessionId, context.epoch, context.roomId,
        ));
        if (context !== this.context) throw new Error('compute_context_changed');
        for (const capability of ['publish', 'subscribe', 'compute', 'validate'] as const) {
          const best = rows
            .filter(row => this.grantMatches(row, context, capability, Date.now() + 30_000))
            .sort((left, right) => right.expires_at_ms - left.expires_at_ms)[0];
          if (best) this.grants.set(capability, best);
        }
        this.grantsHydrated = true;
      })();
    }
    try {
      await this.grantHydration;
    } finally {
      if (!this.grantsHydrated) this.grantHydration = null;
    }
  }

  private async issueReplacementGrant(
    context: SemanticComputeSessionContext,
    capability: SemanticComputeCapability,
    previous: SemanticComputeCapabilityGrantReadModel | undefined,
  ): Promise<SemanticComputeCapabilityGrantReadModel> {
    const direction = capability === 'subscribe' ? 'ingress' : 'egress';
    const issued = await firstValueFrom(this.api.issueCapabilityGrant(context.hubUrl, {
      sessionId: context.sessionId,
      roomId: context.roomId,
      epoch: context.epoch,
      subjectId: context.senderId,
      capability,
      direction,
      expiresAtMs: Date.now() + 300_000,
    }, this.key(`grant-${capability}`)));
    if (!this.grantMatches(issued, context, capability, Date.now() + 30_000)) {
      void this.revokeGrant(context.hubUrl, issued.grant_id);
      throw new Error('compute_capability_grant_invalid');
    }
    if (context !== this.context) {
      void this.revokeGrant(context.hubUrl, issued.grant_id);
      throw new Error('compute_context_changed');
    }
    if (previous && !previous.revoked && previous.expires_at_ms > Date.now()) {
      try {
        await firstValueFrom(this.api.revokeCapabilityGrant(context.hubUrl, previous.grant_id));
      } catch {
        void this.revokeGrant(context.hubUrl, issued.grant_id);
        throw new Error('compute_capability_renew_revoke_failed');
      }
    }
    this.grants.set(capability, issued);
    return issued;
  }

  private grantMatches(
    grant: SemanticComputeCapabilityGrantReadModel,
    context: SemanticComputeSessionContext,
    capability: SemanticComputeCapability,
    validAfterMs: number,
  ): boolean {
    const scopeKind = context.roomId ? 'room' : 'session';
    const scopeId = context.roomId ?? context.sessionId;
    const direction = capability === 'subscribe' ? 'ingress' : 'egress';
    return !grant.revoked
      && grant.subject_id === context.senderId
      && grant.capability === capability
      && grant.scope_kind === scopeKind
      && grant.scope_id === scopeId
      && grant.epoch === context.epoch
      && (grant.direction === direction || grant.direction === 'bidirectional')
      && grant.expires_at_ms > validAfterMs;
  }

  private releaseCapabilityGrants(): void {
    const context = this.context;
    const grants = [...this.grants.values()];
    this.grants.clear();
    this.grantRequests.clear();
    if (!context) return;
    for (const grant of grants) void this.revokeGrant(context.hubUrl, grant.grant_id);
  }

  private async revokeGrant(hubUrl: string, grantId: string): Promise<void> {
    try {
      await firstValueFrom(this.api.revokeCapabilityGrant(hubUrl, grantId));
    } catch {
      // Server expiry/revocation stays authoritative; local state is already cleared.
    }
  }

  private invalidateCapabilityFromError(error: unknown): void {
    if (!CAPABILITY_AUTHORITY_ERRORS.has(reasonCode(error, 'unknown'))) return;
    this.grants.clear();
    this.grantsHydrated = false;
    this.grantHydration = null;
  }

  private patch(patch: Partial<SemanticComputePanelState>): void {
    this.state$.next(Object.freeze({ ...this.state$.value, ...patch }));
  }

  private key(operation: string): string {
    this.serial += 1;
    return `semantic-compute-${operation}-${this.serial}-${crypto.randomUUID()}`;
  }
}

class BrowserCapabilitySigner implements CapabilitySigner {
  private signingKey: CryptoKey | null = null;
  private publicKeyRaw: Uint8Array | null = null;
  private keyId = '';

  async sign(payload: Readonly<Record<string, unknown>>) {
    await this.ensureKeys();
    const bytes = new TextEncoder().encode(canonicalJson(payload));
    const signature = new Uint8Array(await crypto.subtle.sign('Ed25519', this.signingKey!, bytes));
    return { algorithm: 'ed25519' as const, key_id: this.keyId, value: bytesToB64(signature) };
  }

  async registration(expiresAtMs: number): Promise<{
    keyId: string; publicKeyB64: string; expiresAtMs: number;
  }> {
    const raw = await this.ensureKeys();
    return { keyId: this.keyId, publicKeyB64: bytesToB64(raw), expiresAtMs };
  }

  private async ensureKeys(): Promise<Uint8Array> {
    if (!this.signingKey || !this.publicKeyRaw) {
      const generated = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']) as CryptoKeyPair;
      const exportedPublic = await crypto.subtle.exportKey('raw', generated.publicKey);
      const raw = new Uint8Array(exportedPublic);
      const exportedPrivate = await crypto.subtle.exportKey('pkcs8', generated.privateKey);
      this.signingKey = await crypto.subtle.importKey('pkcs8', exportedPrivate, 'Ed25519', false, ['sign']);
      new Uint8Array(exportedPrivate).fill(0);
      this.publicKeyRaw = raw;
      const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', exportedPublic));
      this.keyId = `cap-${hex(digest).slice(0, 32)}`;
    }
    return new Uint8Array(this.publicKeyRaw);
  }
}

function contractView(value: SemanticComputeContractReadModel): ComputeContractView {
  return Object.freeze({
    contractId: value.contract_id,
    digest: value.digest,
    revision: value.revision,
    status: value.status,
    profile: value.profile,
    qualityLevel: value.quality_level,
    delayMs: value.delay_ms,
    expiresAtMs: value.expires_at_ms,
    reasonCode: value.reason_code ?? undefined,
    roles: {
      primary: value.roles.primary ? [...value.roles.primary] : undefined,
      validator: value.roles.validator ? [...value.roles.validator] : undefined,
      standby: value.roles.standby ? [...value.roles.standby] : undefined,
    },
  });
}

function leaseView(value: SemanticComputeLeaseReadModel): ComputeLeaseView {
  return Object.freeze({
    leaseId: value.lease_id,
    contractDigest: value.contract_digest,
    role: value.role,
    executorId: value.executor_id,
    status: value.status,
    expiresAtMs: value.expires_at_ms,
    deadlineAtMs: value.deadline_at_ms,
    resourceBudget: {
      cpuMs: value.resource_budget.cpu_ms,
      memoryBytes: value.resource_budget.memory_bytes,
      artifactBytes: value.resource_budget.artifact_bytes,
    },
  });
}

function preferredTaskType(value: SemanticComputeContractReadModel): string {
  for (const taskType of ['visual_extract', 'speech_features']) {
    if (value.task_types.includes(taskType)) return taskType;
  }
  const taskType = value.task_types[0];
  if (!taskType) throw new Error('compute_task_type_missing');
  return taskType;
}

function actionFor(kind: ComputeContractIntent['kind']) {
  if (kind === 'revoke') return 'revoke' as const;
  if (kind === 'fallback') return 'fallback' as const;
  if (kind === 'accept') return 'accept' as const;
  if (kind === 'activate') return 'activate' as const;
  return 'counter' as const;
}

function proposalForIntent(intent: ComputeContractIntent, current: ComputeContractView): Readonly<Record<string, unknown>> {
  if (intent.kind === 'profile') return { profile: intent.value };
  if (intent.kind === 'delay') return { delay_ms: intent.value };
  if (intent.kind === 'reduce') return { profile: reduceProfile(current.profile) };
  return {};
}

function proposal(profile: ComputeProfile, delayMs: number): Readonly<Record<string, unknown>> {
  return {
    profile,
    quality_level: 'standard',
    delay_ms: delayMs,
    security_mode: 'strict_e2ee',
    trusted_compute_grant: false,
    task_types: ['visual_extract', 'visual_validate', 'speech_features', 'speech_validate'],
    max_artifact_bytes: 1_048_576,
    deadline_ms: Math.min(delayMs, 10_000),
    expires_at_ms: Date.now() + 300_000,
  };
}

function reduceProfile(value: ComputeProfile): ComputeProfile {
  return ({ custom: 'balanced', balanced: 'conservative', conservative: 'off', off: 'off' } as const)[value];
}

function reasonCode(error: unknown, fallback: string): string {
  if (error && typeof error === 'object') {
    const payload = error as { error?: { error?: { code?: unknown } | string }; message?: unknown };
    const nested = payload.error?.error;
    if (nested && typeof nested === 'object' && typeof nested.code === 'string') return nested.code;
    if (typeof nested === 'string') return nested;
    if (typeof payload.message === 'string' && /^[a-z][a-z0-9_]{2,119}$/.test(payload.message)) return payload.message;
  }
  return fallback;
}

const CAPABILITY_AUTHORITY_ERRORS = new Set([
  'capability_grant_required',
  'capability_not_found',
  'capability_not_persisted',
  'capability_revoked',
  'capability_expired',
  'capability_persistence_mismatch',
  'capability_signature_invalid',
  'capability_mismatch',
  'scope_mismatch',
  'direction_mismatch',
  'epoch_mismatch',
]);

function canonicalJson(value: unknown): string {
  if (value === null || typeof value === 'string' || typeof value === 'boolean' || typeof value === 'number') {
    if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('compute_capability_non_finite');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (!value || typeof value !== 'object') throw new Error('compute_capability_invalid');
  const row = value as Record<string, unknown>;
  return `{${Object.keys(row).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(row[key])}`).join(',')}}`;
}

function bytesToB64(value: Uint8Array): string {
  let binary = ''; for (const byte of value) binary += String.fromCharCode(byte); return btoa(binary);
}
function hex(value: Uint8Array): string { return [...value].map(byte => byte.toString(16).padStart(2, '0')).join(''); }
