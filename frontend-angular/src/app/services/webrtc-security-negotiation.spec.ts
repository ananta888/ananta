import { describe, expect, it } from 'vitest';

import { canonicalSecurityJson } from './webrtc-secure-envelope';
import {
  SecurityNegotiationError,
  validateFinalStrictPairSecurityContract,
} from './webrtc-security-negotiation';

const proposal = (sender: string, recipient: string) => ({
  version: 1,
  negotiation_id: 'neg:0123456789abcdef',
  scope_kind: 'session',
  scope_id: 'session-a',
  sender_id: sender,
  recipient_id: recipient,
  minimum_mode: 'strict_e2ee',
  selected_mode: 'strict_e2ee',
  algorithms: ['AES-256-GCM', 'ECDH-P256-HKDF-SHA256'],
  key_epoch: 3,
  payload_classes: ['bulk', 'control', 'semantic'],
  expires_at_ms: 9_007_199_254_740_991,
});

async function fixture() {
  const offer = proposal('owner-session-a', 'participant-a');
  const answer = proposal('participant-a', 'owner-session-a');
  const digest = await sha256(canonicalSecurityJson({
    domain: 'ananta.webrtc.security-negotiation.v1', offer, answer,
  }));
  return {
    version: 1,
    negotiation_id: 'neg:0123456789abcdef',
    offer,
    answer,
    digest,
    signature: 'a'.repeat(64),
    signature_algorithm: 'HMAC-SHA256',
  };
}

describe('final strict Pair security negotiation', () => {
  it('recomputes and accepts the closed bilateral Offer/Answer digest', async () => {
    const value = await fixture();
    await expect(validateFinalStrictPairSecurityContract(value, {
      scopeId: 'session-a', epoch: 3, remoteMembershipId: 'participant-a',
      localMembershipId: 'owner-session-a', nowMs: 1,
    })).resolves.toMatchObject({ digest: value.digest, signature_algorithm: 'HMAC-SHA256' });
  });

  it('rejects a transcript that omits the authenticated local membership', async () => {
    const value = await fixture();
    await expect(validateFinalStrictPairSecurityContract(value, {
      scopeId: 'session-a', epoch: 3, remoteMembershipId: 'participant-a',
      localMembershipId: 'different-member', nowMs: 1,
    })).rejects.toMatchObject<Partial<SecurityNegotiationError>>({ reasonCode: 'negotiation_binding_mismatch' });
  });

  it('rejects removed E2EE flags, algorithm mutations and unknown fields', async () => {
    const downgraded = await fixture();
    downgraded.answer.selected_mode = 'transport_only';
    await expect(validateFinalStrictPairSecurityContract(downgraded, {
      scopeId: 'session-a', epoch: 3, remoteMembershipId: 'participant-a', nowMs: 1,
    })).rejects.toMatchObject<Partial<SecurityNegotiationError>>({ reasonCode: 'security_downgrade_rejected' });

    const algorithmMutation = await fixture();
    algorithmMutation.offer.algorithms = ['AES-256-GCM'];
    await expect(validateFinalStrictPairSecurityContract(algorithmMutation, {
      scopeId: 'session-a', epoch: 3, remoteMembershipId: 'participant-a', nowMs: 1,
    })).rejects.toMatchObject<Partial<SecurityNegotiationError>>({ reasonCode: 'algorithm_invalid' });

    const unsupportedMedia = await fixture();
    unsupportedMedia.offer.payload_classes = ['bulk', 'control', 'media', 'semantic'];
    await expect(validateFinalStrictPairSecurityContract(unsupportedMedia, {
      scopeId: 'session-a', epoch: 3, remoteMembershipId: 'participant-a', nowMs: 1,
    })).rejects.toMatchObject<Partial<SecurityNegotiationError>>({ reasonCode: 'payload_class_invalid' });

    const unknown = { ...(await fixture()), e2ee: false };
    await expect(validateFinalStrictPairSecurityContract(unknown, {
      scopeId: 'session-a', epoch: 3, remoteMembershipId: 'participant-a', nowMs: 1,
    })).rejects.toMatchObject<Partial<SecurityNegotiationError>>({ reasonCode: 'security_contract_fields_invalid' });
  });
});

async function sha256(value: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('');
}
