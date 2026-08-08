import { Injectable, inject } from '@angular/core';
import { BehaviorSubject, firstValueFrom } from 'rxjs';

import { PairSessionControlPlaneService } from './pair-session-control-plane.service';
import type { ShareSession } from './share-session.service';
import { SignedPeerKeyPackage, WebrtcPeerKeyService } from './webrtc-peer-key.service';
import {
  FinalStrictPairSecurityContractV1,
  validateFinalStrictPairSecurityContract,
} from './webrtc-security-negotiation';

const CONFIRMATION_REFRESH_MS = 120_000;

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
      if (!Array.isArray(response.packages)) throw new Error('key_package_response_invalid');
      if (response.packages.length > 1) throw new Error('strict_pair_cardinality_exceeded');
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
      });
      if (generation !== this.generation) return false;
      if (contract.digest !== response.security_contract_digest) {
        throw new Error('security_contract_digest_mismatch');
      }
      let current = this.peerKeys.currentBinding;
      if (
        !current || current.packageId !== peerPackage.package_id
        || current.scopeId !== session.id || current.epoch !== response.epoch
      ) {
        current = await this.peerKeys.verifyAndBind(peerPackage, {
          hubPublicKeyB64: response.hub_public_key_b64,
          expectedHubKeyId: response.hub_key_id,
          expectedTenantId: response.tenant_id,
          expectedScopeId: session.id,
          expectedEpoch: response.epoch,
          localPeerId,
          contractDigest: contract.digest,
        });
        this.lastPackageId = peerPackage.package_id;
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
      await firstValueFrom(this.controlPlane.securityPost(
        session.id, 'key-confirmations',
        {
          recipient_peer_id: current.remotePeerId,
          package_id: this.lastPackageId || current.packageId,
          epoch: current.epoch,
          confirmation_tag: confirmationTag,
        },
      ));
      if (generation !== this.generation) return false;
      const confirmation = await firstValueFrom(this.controlPlane.securityGet<{
        ok: boolean;
        confirmation: null | { confirmation_tag: string; package_id: string; epoch: number };
      }>(session.id, `key-confirmations?sender_peer_id=${encodeURIComponent(current.remotePeerId)}`));
      if (generation !== this.generation) return false;
      if (!confirmation.confirmation) {
        this.state$.next({ status: 'confirming', fingerprint: current.peerFingerprint });
        return false;
      }
      if (confirmation.confirmation.epoch !== current.epoch) throw new Error('epoch_mismatch');
      await this.peerKeys.acceptPeerConfirmation(confirmation.confirmation.confirmation_tag);
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
