import { Injectable, inject } from '@angular/core';

import { E2eEncryptionService, PeerCipherContext } from './e2e-encryption.service';
import { canonicalSecurityJson, decodeB64 } from './webrtc-secure-envelope';

export interface SignedPeerKeyPackage {
  version: 1;
  package_id: string;
  membership_id: string;
  membership_version: number;
  tenant_id: string;
  scope_kind: 'session' | 'room';
  scope_id: string;
  epoch: number;
  peer_id: string;
  recipient_peer_id: string;
  device_id: string;
  device_key_fingerprint: string;
  ecdh_public_key_spki_b64: string;
  issued_at_ms: number;
  expires_at_ms: number;
  hub_key_id: string;
  security_contract_digest: string;
  signature_b64: string;
}

export interface VerifiedPeerBinding extends PeerCipherContext {
  packageId: string;
  tenantId: string;
  deviceId: string;
  membershipId: string;
  membershipVersion: number;
  peerFingerprint: string;
  confirmed: boolean;
  fingerprintChanged: boolean;
  transcriptDigest: string;
}

export interface PeerPackageVerificationOptions {
  hubPublicKeyB64: string;
  expectedHubKeyId: string;
  expectedTenantId: string;
  expectedScopeId: string;
  expectedEpoch: number;
  localPeerId: string;
  contractDigest: string;
  nowMs?: number;
}

export interface VerifiedPeerPackage extends PeerCipherContext {
  readonly packageId: string;
  readonly tenantId: string;
  readonly deviceId: string;
  readonly membershipId: string;
  readonly membershipVersion: number;
  readonly peerFingerprint: string;
  readonly transcriptDigest: string;
}

export class PeerKeyError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

@Injectable({ providedIn: 'root' })
export class WebrtcPeerKeyService {
  private readonly crypto = inject(E2eEncryptionService);
  private binding: VerifiedPeerBinding | null = null;
  private localConfirmationSent = false;

  get currentBinding(): Readonly<VerifiedPeerBinding> | null { return this.binding; }

  async verifyAndBind(
    rawPackage: SignedPeerKeyPackage,
    options: PeerPackageVerificationOptions,
  ): Promise<Readonly<VerifiedPeerBinding>> {
    const verifiedPackage = await this.verifyPackage(rawPackage, options);
    const trustKey = `ananta.peer-fingerprint.v1:${verifiedPackage.tenantId}:${verifiedPackage.remotePeerId}:${verifiedPackage.deviceId}`;
    let previous: string | null = null;
    try { previous = localStorage.getItem(trustKey); } catch { /* explicit reapproval remains required */ }
    const fingerprintChanged = previous !== null && previous !== verifiedPackage.peerFingerprint;
    if (previous === null) {
      try { localStorage.setItem(trustKey, verifiedPackage.peerFingerprint); } catch { /* Hub signature remains authoritative. */ }
    }
    this.binding = {
      ...verifiedPackage,
      confirmed: false,
      fingerprintChanged,
    };
    this.localConfirmationSent = false;
    return this.binding;
  }

  /** Verify a Hub-addressed peer package without changing the active pair binding. */
  async verifyPackage(
    rawPackage: SignedPeerKeyPackage,
    options: PeerPackageVerificationOptions,
  ): Promise<Readonly<VerifiedPeerPackage>> {
    const packageValue = this.validatePackage(rawPackage, options.nowMs ?? Date.now());
    if (packageValue.hub_key_id !== options.expectedHubKeyId) throw new PeerKeyError('hub_key_unknown');
    if (packageValue.tenant_id !== options.expectedTenantId) throw new PeerKeyError('tenant_mismatch');
    if (packageValue.scope_id !== options.expectedScopeId) throw new PeerKeyError('scope_mismatch');
    if (packageValue.epoch !== options.expectedEpoch) throw new PeerKeyError('epoch_mismatch');
    if (packageValue.security_contract_digest !== options.contractDigest) {
      throw new PeerKeyError('security_contract_mismatch');
    }
    if (packageValue.recipient_peer_id !== options.localPeerId) throw new PeerKeyError('unknown_key_share');
    if (packageValue.peer_id === options.localPeerId) throw new PeerKeyError('reflection_detected');
    const unsigned = { ...packageValue } as Record<string, unknown>;
    delete unsigned['signature_b64'];
    const hubKey = await crypto.subtle.importKey(
      'raw', arrayBuffer(decodeB64(options.hubPublicKeyB64)), { name: 'Ed25519' }, false, ['verify'],
    );
    const verified = await crypto.subtle.verify(
      'Ed25519', hubKey, arrayBuffer(decodeB64(packageValue.signature_b64)),
      arrayBuffer(new TextEncoder().encode(canonicalSecurityJson(unsigned))),
    );
    if (!verified) throw new PeerKeyError('key_package_signature_invalid');
    const fingerprint = await this.crypto.fingerprintSpki(packageValue.ecdh_public_key_spki_b64);
    if (fingerprint !== packageValue.device_key_fingerprint) throw new PeerKeyError('device_key_substitution');
    const transcriptDigest = await sha256Hex(canonicalSecurityJson({
      domain: 'ananta.webrtc.key-confirmation.v1',
      scope_id: packageValue.scope_id,
      epoch: packageValue.epoch,
      peers: [packageValue.peer_id, options.localPeerId].sort(),
      contract_digest: options.contractDigest,
    }));
    const sessionKeyId = await sha256Hex(canonicalSecurityJson({
      domain: 'ananta.webrtc.session-key.v1',
      scope_id: packageValue.scope_id,
      epoch: packageValue.epoch,
      peers: [packageValue.peer_id, options.localPeerId].sort(),
      contract_digest: options.contractDigest,
    }));
    return Object.freeze({
      scopeKind: packageValue.scope_kind,
      scopeId: packageValue.scope_id,
      localPeerId: options.localPeerId,
      remotePeerId: packageValue.peer_id,
      peerPublicKeySpkiB64: packageValue.ecdh_public_key_spki_b64,
      epoch: packageValue.epoch,
      keyId: sessionKeyId,
      contractDigest: options.contractDigest,
      packageId: packageValue.package_id,
      tenantId: packageValue.tenant_id,
      deviceId: packageValue.device_id,
      membershipId: packageValue.membership_id,
      membershipVersion: packageValue.membership_version,
      peerFingerprint: fingerprint,
      transcriptDigest,
    });
  }

  approveFingerprintChange(): void {
    if (!this.binding) throw new PeerKeyError('peer_binding_missing');
    const trustKey = `ananta.peer-fingerprint.v1:${this.binding.tenantId}:${this.binding.remotePeerId}:${this.binding.deviceId}`;
    try { localStorage.setItem(trustKey, this.binding.peerFingerprint); } catch {
      throw new PeerKeyError('fingerprint_approval_persist_failed');
    }
    this.binding = { ...this.binding, fingerprintChanged: false };
  }

  async createConfirmation(): Promise<string> {
    const binding = this.requireBinding(false);
    if (binding.fingerprintChanged) throw new PeerKeyError('fingerprint_reapproval_required');
    const directedDigest = await sha256Hex(
      `${binding.transcriptDigest}:${binding.localPeerId}->${binding.remotePeerId}`,
    );
    const tag = await this.crypto.confirmationTag(binding, directedDigest);
    this.localConfirmationSent = true;
    return tag;
  }

  async acceptPeerConfirmation(tag: string): Promise<void> {
    const binding = this.requireBinding(false);
    if (!this.localConfirmationSent) throw new PeerKeyError('local_confirmation_required');
    const directedDigest = await sha256Hex(
      `${binding.transcriptDigest}:${binding.remotePeerId}->${binding.localPeerId}`,
    );
    const expected = await this.crypto.confirmationTag(binding, directedDigest);
    if (!constantTimeEqual(tag, expected)) throw new PeerKeyError('key_confirmation_failed');
    this.binding = { ...binding, confirmed: true };
  }

  requireBinding(requireConfirmed = true): Readonly<VerifiedPeerBinding> {
    if (!this.binding) throw new PeerKeyError('peer_binding_missing');
    if (this.binding.fingerprintChanged) throw new PeerKeyError('fingerprint_reapproval_required');
    if (requireConfirmed && !this.binding.confirmed) throw new PeerKeyError('key_confirmation_required');
    return this.binding;
  }

  clear(): void {
    if (this.binding) this.crypto.forgetEpoch(this.binding.keyId, this.binding.epoch);
    this.binding = null;
    this.localConfirmationSent = false;
  }

  private validatePackage(value: SignedPeerKeyPackage, nowMs: number): SignedPeerKeyPackage {
    const expected = [
      'version', 'package_id', 'membership_id', 'membership_version', 'tenant_id',
      'scope_kind', 'scope_id', 'epoch', 'peer_id', 'recipient_peer_id', 'device_id',
      'device_key_fingerprint', 'ecdh_public_key_spki_b64', 'issued_at_ms',
      'expires_at_ms', 'hub_key_id', 'signature_b64',
      'security_contract_digest',
    ];
    if (!value || typeof value !== 'object' || Object.keys(value).some((key) => !expected.includes(key))
      || expected.some((key) => !(key in value))) throw new PeerKeyError('key_package_fields_invalid');
    if (value.version !== 1) throw new PeerKeyError('key_package_version_invalid');
    if (value.expires_at_ms <= nowMs || value.issued_at_ms > nowMs + 30_000) {
      throw new PeerKeyError('key_package_expired');
    }
    if (!Number.isInteger(value.epoch) || value.epoch < 1 || !Number.isInteger(value.membership_version)) {
      throw new PeerKeyError('key_package_number_invalid');
    }
    if (!/^[a-f0-9]{64}$/.test(value.device_key_fingerprint)) throw new PeerKeyError('device_key_invalid');
    return value;
  }
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

function arrayBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength); copy.set(value); return copy.buffer;
}
