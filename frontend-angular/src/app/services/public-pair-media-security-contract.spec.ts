import {
  PUBLIC_PAIR_MEDIA_GRANTS,
  PUBLIC_PAIR_MEDIA_SLOTS,
  PublicPairMediaContractError,
  validatePublicPairMediaSecurityContract,
} from './public-pair-media-security-contract';
import type { FinalStrictPairSecurityContractV1 } from './webrtc-security-negotiation';
import type { SignedPeerKeyPackage } from './webrtc-peer-key.service';
import { canonicalSecurityJson, encodeB64 } from './webrtc-secure-envelope';

const NOW = 1_000_000;
const BASE_DIGEST = 'd'.repeat(64);
const LOCAL_FP = 'a'.repeat(64);
const REMOTE_FP = 'b'.repeat(64);

const baseContract = {
  version: 1,
  negotiation_id: 'neg:test',
  offer: { sender_id: 'member:owner', recipient_id: 'member:guest', expires_at_ms: NOW + 60_000 },
  answer: { sender_id: 'member:guest', recipient_id: 'member:owner', expires_at_ms: NOW + 60_000 },
  digest: BASE_DIGEST,
  signature: 'c'.repeat(64),
  signature_algorithm: 'HMAC-SHA256',
} as unknown as FinalStrictPairSecurityContractV1;

async function fixture() {
  const authority = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']);
  const publicBytes = new Uint8Array(await crypto.subtle.exportKey('raw', authority.publicKey));
  const authorityKeyId = `rv:${(await digestBytes(publicBytes)).slice(0, 24)}`;
  const unsigned = {
    domain: 'ananta.public-pair.media-security-contract.v1',
    version: 1,
    session_id: 'session-a',
    epoch: 3,
    identity_binding_version: 2,
    base_security_contract_digest: BASE_DIGEST,
    memberships: [
      {
        membership_id: 'member:owner', membership_version: 1,
        peer_id: `peer:${'1'.repeat(64)}`, device_key_fingerprint: LOCAL_FP,
        public_media_e2ee_version: 1,
      },
      {
        membership_id: 'member:guest', membership_version: 1,
        peer_id: `peer:${'2'.repeat(64)}`, device_key_fingerprint: REMOTE_FP,
        public_media_e2ee_version: 1,
      },
    ],
    grants: [...PUBLIC_PAIR_MEDIA_GRANTS],
    slots: PUBLIC_PAIR_MEDIA_SLOTS.map(value => ({ ...value })),
    transform: 'RTCRtpScriptTransform',
    algorithms: { aead: 'AES-256-GCM', kdf: 'HKDF-SHA-256' },
    expires_at_ms: NOW + 60_000,
    authority_key_id: authorityKeyId,
  };
  const digest = await digestText(canonicalSecurityJson(unsigned));
  const signed = { ...unsigned, digest, signature_algorithm: 'Ed25519' };
  const signature = await crypto.subtle.sign(
    'Ed25519', authority.privateKey,
    new TextEncoder().encode(canonicalSecurityJson(signed)),
  );
  const contract = { ...signed, signature_b64: encodeB64(signature) };
  const remotePackage = {
    membership_id: 'member:guest', membership_version: 1,
    peer_id: `peer:${'2'.repeat(64)}`, device_key_fingerprint: REMOTE_FP,
  } as SignedPeerKeyPackage;
  const options = {
    sessionId: 'session-a', epoch: 3,
    localPeerId: `peer:${'1'.repeat(64)}`,
    localMembershipId: 'member:owner', localDeviceFingerprint: LOCAL_FP,
    remotePackage, baseContract, authorityKeyId,
    authorityPublicKeyB64: encodeB64(publicBytes), nowMs: NOW,
  };
  return { contract, options };
}

describe('Public Pair media security contract', () => {
  it('verifies the exact profile, membership binding, digest and authority signature', async () => {
    const { contract, options } = await fixture();
    await expect(validatePublicPairMediaSecurityContract(contract, options))
      .resolves.toMatchObject({ digest: contract.digest, transform: 'RTCRtpScriptTransform' });
  });

  it('rejects profile expansion, unknown fields and signature tampering', async () => {
    const { contract, options } = await fixture();
    await expect(validatePublicPairMediaSecurityContract({
      ...contract, grants: [...contract.grants, 'h264'],
    }, options)).rejects.toMatchObject<Partial<PublicPairMediaContractError>>({
      reasonCode: 'public_media_contract_grants_invalid',
    });
    await expect(validatePublicPairMediaSecurityContract({ ...contract, extra: true }, options))
      .rejects.toMatchObject<Partial<PublicPairMediaContractError>>({
        reasonCode: 'public_media_contract_fields_invalid',
      });
    await expect(validatePublicPairMediaSecurityContract({
      ...contract, signature_b64: encodeB64(new Uint8Array(64)),
    }, options)).rejects.toMatchObject<Partial<PublicPairMediaContractError>>({
      reasonCode: 'public_media_contract_signature_invalid',
    });
  });
});

async function digestText(value: string): Promise<string> {
  return digestBytes(new TextEncoder().encode(value));
}

async function digestBytes(value: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', Uint8Array.from(value).buffer);
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
