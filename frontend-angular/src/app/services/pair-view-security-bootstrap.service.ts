import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, firstValueFrom } from 'rxjs';

import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import type { ShareSession } from './share-session.service';
import { SignedPeerKeyPackage, WebrtcPeerKeyService } from './webrtc-peer-key.service';
import {
  PUBLIC_RENDEZVOUS_SIGNING_KEY_ID,
  PUBLIC_RENDEZVOUS_SIGNING_PUBLIC_KEY_B64,
} from './public-ananta-endpoints';
import {
  FinalStrictPairSecurityContractV1,
  validateFinalStrictPairSecurityContract,
} from './webrtc-security-negotiation';

const CONFIRMATION_REFRESH_MS = 120_000;
const CONFIRMATION_MAX_AGE_MS = 5 * 60_000;
const CLOCK_SKEW_MS = 30_000;

export type PairSecurityBootstrapState =
  | { status: 'idle' | 'legacy' | 'waiting_for_peer' | 'confirming' | 'ready'; fingerprint?: string }
  | { status: 'fingerprint_changed'; fingerprint: string }
  | { status: 'failed'; reasonCode: string };

interface KeyPackageResponse {
  ok: boolean;
  epoch: number;
  tenant_id: string;
  security_contract_digest: string | null;
  security_contract: FinalStrictPairSecurityContractV1 | null;
  hub_key_id: string;
  hub_public_key_b64: string;
  packages: SignedPeerKeyPackage[];
  local_membership_id?: string;
  local_peer_id?: string;
  /** Local device package addressed to the remote peer (opposite direction). */
  local_package_id?: string | null;
}

interface KeyConfirmation {
  confirmation_tag: string;
  package_id: string;
  epoch: number;
  created_at_ms?: number;
  expires_at_ms?: number;
  /** Backward-compatible Hub projection; seconds since epoch. */
  expires_at?: number;
}

@Injectable({ providedIn: 'root' })
export class PairViewSecurityBootstrapService {
  private readonly controlPlane = inject(PairSessionControlPlaneService);
  private readonly peerKeys = inject(WebrtcPeerKeyService);
  private inFlight: { key: string; promise: Promise<boolean> } | null = null;
  private lastPackageId = '';
  private lastConfirmationRefreshAt = 0;
  private generation = 0;
  currentEpoch = 0;

  readonly state$ = new BehaviorSubject<PairSecurityBootstrapState>({ status: 'idle' });

  /**
   * The signaling audience is exposed only after the peer package and the
   * bidirectional key confirmation have both been verified. Callers must not
   * derive a signaling recipient from participants or other display data.
   */
  get confirmedRemotePeerId(): string {
    const binding = this.peerKeys.currentBinding;
    return binding?.confirmed ? binding.remotePeerId : '';
  }

  ensure(session: ShareSession, localPeerId: string): Promise<boolean> {
    const key = `${session.id}:${session.security_epoch ?? 0}:${localPeerId}`;
    if (this.inFlight?.key === key) return this.inFlight.promise;
    const generation = ++this.generation;
    const promise = this.run(session, localPeerId, generation).finally(() => {
      if (this.inFlight?.promise === promise) this.inFlight = null;
    });
    this.inFlight = { key, promise };
    return promise;
  }

  approveFingerprintChange(): void {
    this.peerKeys.approveFingerprintChange();
    this.state$.next({ status: 'confirming' });
  }

  markLegacy(): void {
    this.generation += 1;
    this.inFlight = null;
    this.lastPackageId = '';
    this.lastConfirmationRefreshAt = 0;
    this.currentEpoch = 0;
    this.peerKeys.clear();
    this.state$.next({ status: 'legacy' });
  }

  clear(): void {
    this.generation += 1;
    this.inFlight = null;
    this.lastPackageId = '';
    this.lastConfirmationRefreshAt = 0;
    this.currentEpoch = 0;
    this.peerKeys.clear();
    this.state$.next({ status: 'idle' });
  }

  private async run(session: ShareSession, localPeerId: string, generation: number): Promise<boolean> {
    if (
      session.security_contract_version !== 1
      || session.security_mode !== 'strict_e2ee'
      || !session.security_epoch
      || !localPeerId
    ) {
      this.state$.next({ status: 'failed', reasonCode: 'strict_security_context_missing' });
      return false;
    }
    try {
      const response = await firstValueFrom(
        this.controlPlane.securityGet<KeyPackageResponse>(session.id, 'key-packages'),
      );
      if (generation !== this.generation) return false;
      const publicSession = this.controlPlane.isPublicSession(session.id);
      validateKeyPackageResponse(response, session, localPeerId, publicSession);
      const peerPackage = response.packages[0];
      this.currentEpoch = response.epoch;
      if (!peerPackage) {
        this.lastPackageId = '';
        this.lastConfirmationRefreshAt = 0;
        this.peerKeys.clear();
        this.state$.next({ status: 'waiting_for_peer' });
        return false;
      }
      if (!response.security_contract || !response.security_contract_digest) {
        throw new Error('security_contract_missing');
      }
      const contract = await validateFinalStrictPairSecurityContract(response.security_contract, {
        scopeId: session.id,
        epoch: response.epoch,
        remoteMembershipId: peerPackage.membership_id,
        localMembershipId: response.local_membership_id,
      });
      if (generation !== this.generation) return false;
      if (contract.digest !== response.security_contract_digest) {
        throw new Error('security_contract_digest_mismatch');
      }
      const priorBinding = this.peerKeys.currentBinding;
      const current = await this.peerKeys.verifyAndRefreshBinding(peerPackage, {
        hubPublicKeyB64: response.hub_public_key_b64,
        expectedHubKeyId: response.hub_key_id,
        expectedTenantId: response.tenant_id,
        expectedScopeId: session.id,
        expectedEpoch: response.epoch,
        localPeerId,
        contractDigest: contract.digest,
      });
      this.lastPackageId = peerPackage.package_id;
      if (priorBinding !== current) {
        this.lastConfirmationRefreshAt = 0;
      }
      if (current.confirmed && Date.now() - this.lastConfirmationRefreshAt < CONFIRMATION_REFRESH_MS) {
        this.state$.next({ status: 'ready', fingerprint: current.peerFingerprint });
        return true;
      }
      if (current.fingerprintChanged) {
        this.state$.next({ status: 'fingerprint_changed', fingerprint: current.peerFingerprint });
        return false;
      }
      if (!current.confirmed) {
        this.state$.next({ status: 'confirming', fingerprint: current.peerFingerprint });
      }
      const confirmationTag = await this.peerKeys.createConfirmation();
      const postedConfirmation = await firstValueFrom(this.controlPlane.securityPost<{
        ok: boolean;
        local_peer_id?: string;
        created_at_ms?: number;
        expires_at_ms?: number;
      }>(
        session.id, 'key-confirmations',
        {
          recipient_peer_id: current.remotePeerId,
          package_id: this.lastPackageId || current.packageId,
          epoch: current.epoch,
          confirmation_tag: confirmationTag,
        },
      ));
      if (generation !== this.generation) return false;
      if (publicSession && postedConfirmation.local_peer_id !== localPeerId) {
        throw new Error('public_local_peer_id_mismatch');
      }
      const confirmation = await firstValueFrom(this.controlPlane.securityGet<{
        ok: boolean;
        confirmation: null | KeyConfirmation;
        local_peer_id?: string;
      }>(session.id, `key-confirmations?sender_peer_id=${encodeURIComponent(current.remotePeerId)}`));
      if (generation !== this.generation) return false;
      if (publicSession && confirmation.local_peer_id !== localPeerId) {
        throw new Error('public_local_peer_id_mismatch');
      }
      if (!confirmation.confirmation) {
        this.state$.next({ status: 'confirming', fingerprint: current.peerFingerprint });
        return false;
      }
      const verifiedConfirmation = validateKeyConfirmation(
        confirmation.confirmation,
        response.local_package_id,
        current.epoch,
        publicSession,
      );
      await this.peerKeys.acceptPeerConfirmation(verifiedConfirmation.confirmation_tag);
      this.lastConfirmationRefreshAt = Date.now();
      this.state$.next({ status: 'ready', fingerprint: current.peerFingerprint });
      return true;
    } catch (error) {
      if (generation !== this.generation) return false;
      const responseCode = (error as { error?: { error?: unknown } } | null)?.error?.error;
      const reasonCode = typeof responseCode === 'string'
        ? responseCode
        : error instanceof Error ? error.message : 'security_bootstrap_failed';
      this.state$.next({ status: 'failed', reasonCode });
      return false;
    }
  }
}

function validateKeyPackageResponse(
  response: KeyPackageResponse,
  session: ShareSession,
  localPeerId: string,
  publicSession: boolean,
): void {
  if (!response || typeof response !== 'object' || response.ok !== true) {
    throw new Error('key_package_response_invalid');
  }
  if (!Number.isSafeInteger(response.epoch) || response.epoch < 1) {
    throw new Error('key_package_epoch_invalid');
  }
  if (session.security_epoch && response.epoch < session.security_epoch) {
    throw new Error('key_package_epoch_stale');
  }
  if (!identifier(response.tenant_id) || !Array.isArray(response.packages)) {
    throw new Error('key_package_response_invalid');
  }
  if (publicSession && (
    response.local_peer_id !== localPeerId
    || !identifier(response.local_membership_id)
  )) throw new Error('public_key_package_identity_mismatch');
  if (publicSession && (
    !/^rv:[a-f0-9]{24}$/.test(String(response.hub_key_id || ''))
    || !validEd25519PublicKey(response.hub_public_key_b64)
  )) {
    throw new Error('public_hub_key_id_invalid');
  }
  if (publicSession && (
    response.hub_key_id !== PUBLIC_RENDEZVOUS_SIGNING_KEY_ID
    || response.hub_public_key_b64 !== PUBLIC_RENDEZVOUS_SIGNING_PUBLIC_KEY_B64
  )) throw new Error('public_hub_authority_untrusted');
  if (response.packages.length > 1) throw new Error('strict_pair_cardinality_exceeded');
  if (response.packages.length === 0) {
    if (response.security_contract !== null || response.security_contract_digest !== null) {
      throw new Error('unexpected_security_contract');
    }
    return;
  }
  if (
    !response.security_contract
    || !/^[a-f0-9]{64}$/.test(String(response.security_contract_digest || ''))
    || !identifier(response.hub_key_id)
    || !validEd25519PublicKey(response.hub_public_key_b64)
  ) throw new Error('key_package_response_invalid');
  if (response.local_membership_id !== undefined && !identifier(response.local_membership_id)) {
    throw new Error('local_membership_id_invalid');
  }
  if (publicSession && (
    !PACKAGE_ID_RE.test(String(response.local_package_id || ''))
    || response.local_package_id === response.packages[0]?.package_id
  )) throw new Error('public_local_package_id_invalid');
}

function validateKeyConfirmation(
  value: KeyConfirmation,
  expectedPackageId: string | null | undefined,
  expectedEpoch: number,
  requirePackageMatch: boolean,
  nowMs = Date.now(),
): KeyConfirmation {
  if (!value || typeof value !== 'object') throw new Error('key_confirmation_invalid');
  if (!PACKAGE_ID_RE.test(value.package_id)) throw new Error('key_confirmation_invalid');
  if (requirePackageMatch && value.package_id !== expectedPackageId) {
    throw new Error('key_confirmation_package_mismatch');
  }
  if (value.epoch !== expectedEpoch) throw new Error('epoch_mismatch');
  if (typeof value.confirmation_tag !== 'string' || !validBase64(value.confirmation_tag, 32)) {
    throw new Error('key_confirmation_invalid');
  }
  const createdAtMs = value.created_at_ms;
  const expiresAtMs = value.expires_at_ms
    ?? (typeof value.expires_at === 'number' ? Math.round(value.expires_at * 1000) : Number.NaN);
  if (
    !Number.isSafeInteger(expiresAtMs)
    || expiresAtMs <= nowMs
    || expiresAtMs > nowMs + CONFIRMATION_MAX_AGE_MS + CLOCK_SKEW_MS
  ) throw new Error('key_confirmation_stale');
  if (createdAtMs !== undefined && (
    !Number.isSafeInteger(createdAtMs)
    || createdAtMs > nowMs + CLOCK_SKEW_MS
    || createdAtMs < nowMs - CONFIRMATION_MAX_AGE_MS
    || expiresAtMs > createdAtMs + CONFIRMATION_MAX_AGE_MS + CLOCK_SKEW_MS
  )) throw new Error('key_confirmation_stale');
  return value;
}

function identifier(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(value);
}

const PACKAGE_ID_RE = /^[a-f0-9]{64}$/;

function validEd25519PublicKey(value: unknown): boolean {
  return typeof value === 'string' && validBase64(value, 32);
}

function validBase64(value: string, expectedBytes: number): boolean {
  try {
    const decoded = atob(value);
    return decoded.length === expectedBytes && btoa(decoded) === value;
  } catch {
    return false;
  }
}
