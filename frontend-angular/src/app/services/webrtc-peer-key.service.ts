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
  authorityKeyId: string;
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
  readonly authorityKeyId: string;
}

export class PeerKeyError extends Error {
  constructor(readonly reasonCode: string) { super(reasonCode); }
}

const KEY_PACKAGE_MAX_LIFETIME_MS = 5 * 60_000;
const KEY_PACKAGE_CLOCK_SKEW_MS = 30_000;
const PACKAGE_ID_RE = /^[a-f0-9]{64}$/;
const PEER_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/;

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
    return this.bindVerifiedPackage(verifiedPackage);
  }

  /**
   * Re-verifies every fetched signed package, including expiry and signer.
   * Confirmation state survives only when the refreshed package describes
   * the exact same cryptographic identity and authority.
   */
  async verifyAndRefreshBinding(
    rawPackage: SignedPeerKeyPackage,
    options: PeerPackageVerificationOptions,
  ): Promise<Readonly<VerifiedPeerBinding>> {
    const verifiedPackage = await this.verifyPackage(rawPackage, options);
    const current = this.binding;
    if (
      !current
      || current.packageId !== verifiedPackage.packageId
      || current.scopeId !== verifiedPackage.scopeId
      || current.epoch !== verifiedPackage.epoch
    ) return this.bindVerifiedPackage(verifiedPackage);
    if (current.authorityKeyId !== verifiedPackage.authorityKeyId) {
      throw new PeerKeyError('hub_key_changed');
    }
    if (!sameVerifiedIdentity(current, verifiedPackage)) {
      throw new PeerKeyError('key_package_refresh_identity_mismatch');
    }
    return current;
  }

  private bindVerifiedPackage(
    verifiedPackage: Readonly<VerifiedPeerPackage>,
  ): Readonly<VerifiedPeerBinding> {
    const trustKey = `ananta.peer-fingerprint.v1:${verifiedPackage.tenantId}:${verifiedPackage.remotePeerId}:${verifiedPackage.deviceId}`;
    let previous: string | null = null;
    try { previous = localStorage.getItem(trustKey); } catch { /* explicit reapproval remains required */ }
    const fingerprintChanged = previous !== null && previous !== verifiedPackage.peerFingerprint;
    if (previous === null) {
      try { localStorage.setItem(trustKey, verifiedPackage.peerFingerprint); } catch { /* Authority signature remains authoritative. */ }
    }
    this.binding = {
      ...verifiedPackage,
      confirmed: false,
      fingerprintChanged,
    };
    this.localConfirmationSent = false;
    return this.binding;
  }

  /** Verify an authority-addressed peer package without changing the active pair binding. */
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
    let verified = false;
    try {
      const rawHubKey = decodeB64(options.hubPublicKeyB64);
      const signature = decodeB64(packageValue.signature_b64);
      if (rawHubKey.byteLength !== 32 || signature.byteLength !== 64) {
        throw new PeerKeyError('key_package_signature_invalid');
      }
      if (packageValue.hub_key_id.startsWith('rv:')) {
        const derivedKeyId = `rv:${(await sha256HexBytes(rawHubKey)).slice(0, 24)}`;
        if (derivedKeyId !== packageValue.hub_key_id || derivedKeyId !== options.expectedHubKeyId) {
          throw new PeerKeyError('hub_key_unknown');
        }
      }
      const hubKey = await crypto.subtle.importKey(
        'raw', arrayBuffer(rawHubKey), { name: 'Ed25519' }, false, ['verify'],
      );
      verified = await crypto.subtle.verify(
        'Ed25519', hubKey, arrayBuffer(signature),
        arrayBuffer(new TextEncoder().encode(canonicalSecurityJson(unsigned))),
      );
    } catch (error) {
      if (error instanceof PeerKeyError) throw error;
      throw new PeerKeyError('key_package_signature_invalid');
    }
    if (!verified) throw new PeerKeyError('key_package_signature_invalid');
    try {
      await crypto.subtle.importKey(
        'spki', arrayBuffer(decodeB64(packageValue.ecdh_public_key_spki_b64)),
        { name: 'ECDH', namedCurve: 'P-256' }, false, [],
      );
    } catch {
      throw new PeerKeyError('device_key_invalid');
    }
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
      authorityKeyId: packageValue.hub_key_id,
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
    if (
      !Number.isSafeInteger(value.issued_at_ms)
      || !Number.isSafeInteger(value.expires_at_ms)
      || value.expires_at_ms <= nowMs
      || value.issued_at_ms > nowMs + KEY_PACKAGE_CLOCK_SKEW_MS
      || value.issued_at_ms < nowMs - KEY_PACKAGE_MAX_LIFETIME_MS - KEY_PACKAGE_CLOCK_SKEW_MS
      || value.expires_at_ms > value.issued_at_ms + KEY_PACKAGE_MAX_LIFETIME_MS + KEY_PACKAGE_CLOCK_SKEW_MS
    ) {
      throw new PeerKeyError('key_package_expired');
    }
    if (
      !Number.isSafeInteger(value.epoch) || value.epoch < 1
      || !Number.isSafeInteger(value.membership_version) || value.membership_version < 1
    ) {
      throw new PeerKeyError('key_package_number_invalid');
    }
    if (
      !PACKAGE_ID_RE.test(value.package_id)
      || !PACKAGE_ID_RE.test(value.security_contract_digest)
      || !PACKAGE_ID_RE.test(value.device_key_fingerprint)
      || !PEER_ID_RE.test(value.membership_id)
      || !PEER_ID_RE.test(value.tenant_id)
      || !PEER_ID_RE.test(value.scope_id)
      || !PEER_ID_RE.test(value.peer_id)
      || !PEER_ID_RE.test(value.recipient_peer_id)
      || !PEER_ID_RE.test(value.device_id)
      || !PEER_ID_RE.test(value.hub_key_id)
      || (value.scope_kind !== 'session' && value.scope_kind !== 'room')
    ) throw new PeerKeyError('key_package_identity_invalid');
    return value;
  }
}

function sameVerifiedIdentity(
  binding: VerifiedPeerBinding,
  refreshed: Readonly<VerifiedPeerPackage>,
): boolean {
  return binding.packageId === refreshed.packageId
    && binding.tenantId === refreshed.tenantId
    && binding.deviceId === refreshed.deviceId
    && binding.membershipId === refreshed.membershipId
    && binding.membershipVersion === refreshed.membershipVersion
    && binding.peerFingerprint === refreshed.peerFingerprint
    && binding.transcriptDigest === refreshed.transcriptDigest
    && binding.scopeKind === refreshed.scopeKind
    && binding.scopeId === refreshed.scopeId
    && binding.localPeerId === refreshed.localPeerId
    && binding.remotePeerId === refreshed.remotePeerId
    && binding.peerPublicKeySpkiB64 === refreshed.peerPublicKeySpkiB64
    && binding.epoch === refreshed.epoch
    && binding.keyId === refreshed.keyId
    && binding.contractDigest === refreshed.contractDigest;
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

async function sha256HexBytes(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', arrayBuffer(value));
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
