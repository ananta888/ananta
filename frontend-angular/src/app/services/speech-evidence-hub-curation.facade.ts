import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import {
  SpeechEvidenceHubCurationResponse,
  SpeechEvidenceSyncApiService,
} from './speech-evidence-sync-api.service';
import {
  SpeechEvidenceMessage,
  SpeechEvidenceValidationError,
  canonicalJson,
  sha256Canonical,
} from './speech-evidence-sync.validators';

export interface SpeechEvidenceHubCurationBinding {
  readonly offerId: string;
  readonly inventoryRootDigest: string;
  readonly pairId: string;
  readonly direction: string;
  readonly consentDigest: string;
  readonly groupIds: readonly string[];
}

export interface SpeechEvidenceHubCurationRequest {
  readonly hubUrl: string;
  readonly binding: SpeechEvidenceHubCurationBinding;
  readonly message: SpeechEvidenceMessage;
  readonly groups: readonly Readonly<{ groupId: string; chunksB64: readonly string[] }>[];
}

/**
 * Product boundary for Hub-owned peer-evidence curation.
 *
 * The transport client only parses response shapes. This facade additionally
 * verifies the Hub signature and binds the decision to the exact offer,
 * consent and group set before feature code may consume a receipt.
 */
@Injectable({ providedIn: 'root' })
export class SpeechEvidenceHubCurationFacade {
  private readonly api = inject(SpeechEvidenceSyncApiService);

  async request(value: SpeechEvidenceHubCurationRequest): Promise<SpeechEvidenceHubCurationResponse> {
    const response = await firstValueFrom(this.api.requestCuration(
      value.hubUrl,
      value.binding.offerId,
      value.message,
      value.groups,
    ));
    await this.assertVerified(response, value.binding);
    return response;
  }

  async get(
    hubUrl: string,
    binding: SpeechEvidenceHubCurationBinding,
  ): Promise<SpeechEvidenceHubCurationResponse> {
    const response = await firstValueFrom(this.api.getCuration(hubUrl, binding.offerId));
    await this.assertVerified(response, binding);
    return response;
  }

  private async assertVerified(
    response: SpeechEvidenceHubCurationResponse,
    binding: SpeechEvidenceHubCurationBinding,
  ): Promise<void> {
    const receipt = response.curation.receipt;
    const allGroups = [
      ...receipt.acceptedGroupIds,
      ...receipt.rejectedGroupIds,
      ...receipt.quarantinedGroupIds,
    ];
    const expected = [...binding.groupIds].sort();
    const expectedUnsigned = {
      version: receipt.version,
      receipt_id: receipt.receiptId,
      admission_digest: receipt.admissionDigest,
      offer_id: receipt.offerId,
      inventory_root_digest: receipt.inventoryRootDigest,
      resolution_digest: receipt.resolutionDigest,
      accepted_group_ids: receipt.acceptedGroupIds,
      rejected_group_ids: receipt.rejectedGroupIds,
      quarantined_group_ids: receipt.quarantinedGroupIds,
      consent_digest: receipt.consentDigest,
      policy_digest: receipt.policyDigest,
      result_digest: receipt.resultDigest,
      pair_id: receipt.pairId,
      direction: receipt.direction,
      issued_at_ms: receipt.issuedAtMs,
      hub_key_id: receipt.hubKeyId,
    };
    const resultDigest = await sha256Canonical({
      accepted: [...receipt.acceptedGroupIds].sort(),
      quarantined: [...receipt.quarantinedGroupIds].sort(),
      rejected: [...receipt.rejectedGroupIds].sort(),
    });
    if (
      response.hubReceiptKey.algorithm !== 'Ed25519'
      || response.hubReceiptKey.keyId !== receipt.hubKeyId
      || response.curation.offerId !== binding.offerId
      || response.curation.admissionDigest !== receipt.admissionDigest
      || receipt.offerId !== binding.offerId
      || receipt.inventoryRootDigest !== binding.inventoryRootDigest
      || receipt.pairId !== binding.pairId
      || receipt.direction !== binding.direction
      || receipt.consentDigest !== binding.consentDigest
      || new Set(allGroups).size !== allGroups.length
      || canonicalJson([...allGroups].sort()) !== canonicalJson(expected)
      || resultDigest !== receipt.resultDigest
      || canonicalJson(receipt.unsignedValue) !== canonicalJson(expectedUnsigned)
      || !await verifyReceiptSignature(response)
    ) {
      throw new SpeechEvidenceValidationError('speech_evidence_hub_receipt_invalid');
    }
    if (
      (response.curation.state === 'admitted' || response.curation.state === 'dataset_published')
      && receipt.acceptedGroupIds.length !== expected.length
    ) {
      throw new SpeechEvidenceValidationError('speech_evidence_hub_receipt_decision_mismatch');
    }
  }
}

async function verifyReceiptSignature(response: SpeechEvidenceHubCurationResponse): Promise<boolean> {
  const keyBytes = decodeBoundedBase64(response.hubReceiptKey.publicKeyB64, 32);
  const signature = decodeBoundedBase64(response.curation.receipt.signatureB64, 64);
  try {
    const key = await crypto.subtle.importKey(
      'raw', copiedBuffer(keyBytes), { name: 'Ed25519' }, false, ['verify'],
    );
    return crypto.subtle.verify(
      'Ed25519',
      key,
      copiedBuffer(signature),
      copiedBuffer(new TextEncoder().encode(canonicalJson(response.curation.receipt.unsignedValue))),
    );
  } catch {
    return false;
  } finally {
    keyBytes.fill(0);
    signature.fill(0);
  }
}

function decodeBoundedBase64(value: string, expectedLength: number): Uint8Array {
  let binary: string;
  try {
    binary = atob(value);
  } catch {
    throw new SpeechEvidenceValidationError('speech_evidence_hub_receipt_encoding_invalid');
  }
  if (binary.length !== expectedLength) {
    throw new SpeechEvidenceValidationError('speech_evidence_hub_receipt_encoding_invalid');
  }
  return Uint8Array.from(binary, character => character.charCodeAt(0));
}

function copiedBuffer(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength);
  copy.set(value);
  return copy.buffer;
}
