import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import {
  SpeechEvidenceHubCurationResponse,
  SpeechEvidenceSyncApiService,
} from './speech-evidence-sync-api.service';
import {
  SpeechEvidenceMessage,
  canonicalJson,
  sha256Canonical,
} from './speech-evidence-sync.validators';
import {
  SpeechEvidenceHubCurationBinding,
  SpeechEvidenceHubCurationFacade,
} from './speech-evidence-hub-curation.facade';

const GROUP_ID = 'speech-group-1234567890abcdef1234567890abcdef12345678';

describe('SpeechEvidenceHubCurationFacade', () => {
  let api: {
    requestCuration: ReturnType<typeof vi.fn>;
    getCuration: ReturnType<typeof vi.fn>;
  };
  let facade: SpeechEvidenceHubCurationFacade;

  beforeEach(() => {
    api = {
      requestCuration: vi.fn(),
      getCuration: vi.fn(),
    };
    TestBed.configureTestingModule({ providers: [
      SpeechEvidenceHubCurationFacade,
      { provide: SpeechEvidenceSyncApiService, useValue: api },
    ] });
    facade = TestBed.inject(SpeechEvidenceHubCurationFacade);
  });

  it('accepts only a Hub-signed receipt bound to the exact offer, consent and group set', async () => {
    const response = await signedResponse();
    api.requestCuration.mockReturnValue(of(response));

    const result = await facade.request({
      hubUrl: 'http://hub.test',
      binding: binding(),
      message: {} as SpeechEvidenceMessage,
      groups: [{ groupId: GROUP_ID, chunksB64: ['YWJj'] }],
    });

    expect(result).toBe(response);
    expect(api.requestCuration).toHaveBeenCalledWith(
      'http://hub.test',
      'offer-a',
      expect.any(Object),
      [{ groupId: GROUP_ID, chunksB64: ['YWJj'] }],
    );
  });

  it('fails closed when parsed receipt fields diverge from the signed unsigned value', async () => {
    const response = await signedResponse();
    const tampered: SpeechEvidenceHubCurationResponse = {
      ...response,
      curation: {
        ...response.curation,
        receipt: { ...response.curation.receipt, policyDigest: '9'.repeat(64) },
      },
    };
    api.getCuration.mockReturnValue(of(tampered));

    await expect(facade.get('http://hub.test', binding())).rejects.toMatchObject({
      reasonCode: 'speech_evidence_hub_receipt_invalid',
    });
  });

  it('fails closed for a receipt decision that omits an admitted group', async () => {
    const response = await signedResponse({ acceptedGroupIds: [], quarantinedGroupIds: [GROUP_ID] });
    api.requestCuration.mockReturnValue(of({
      ...response,
      curation: { ...response.curation, state: 'admitted' },
    }));

    await expect(facade.request({
      hubUrl: 'http://hub.test',
      binding: binding(),
      message: {} as SpeechEvidenceMessage,
      groups: [{ groupId: GROUP_ID, chunksB64: ['YWJj'] }],
    })).rejects.toMatchObject({ reasonCode: 'speech_evidence_hub_receipt_decision_mismatch' });
  });
});

function binding(): SpeechEvidenceHubCurationBinding {
  return {
    offerId: 'offer-a',
    inventoryRootDigest: '2'.repeat(64),
    pairId: 'pair-a',
    direction: 'sender_to_receiver',
    consentDigest: '3'.repeat(64),
    groupIds: [GROUP_ID],
  };
}

async function signedResponse(
  decision: {
    acceptedGroupIds?: readonly string[];
    rejectedGroupIds?: readonly string[];
    quarantinedGroupIds?: readonly string[];
  } = {},
): Promise<SpeechEvidenceHubCurationResponse> {
  const acceptedGroupIds = decision.acceptedGroupIds ?? [GROUP_ID];
  const rejectedGroupIds = decision.rejectedGroupIds ?? [];
  const quarantinedGroupIds = decision.quarantinedGroupIds ?? [];
  const resultDigest = await sha256Canonical({
    accepted: [...acceptedGroupIds].sort(),
    quarantined: [...quarantinedGroupIds].sort(),
    rejected: [...rejectedGroupIds].sort(),
  });
  const unsignedValue = {
    version: 'ananta.speech-evidence-admission-receipt.v1',
    receipt_id: '1'.repeat(64),
    admission_digest: '4'.repeat(64),
    offer_id: 'offer-a',
    inventory_root_digest: '2'.repeat(64),
    resolution_digest: '5'.repeat(64),
    accepted_group_ids: acceptedGroupIds,
    rejected_group_ids: rejectedGroupIds,
    quarantined_group_ids: quarantinedGroupIds,
    consent_digest: '3'.repeat(64),
    policy_digest: '6'.repeat(64),
    result_digest: resultDigest,
    pair_id: 'pair-a',
    direction: 'sender_to_receiver',
    issued_at_ms: Date.now(),
    hub_key_id: 'hub-receipt-key-a',
  };
  const keys = await crypto.subtle.generateKey('Ed25519', true, ['sign', 'verify']) as CryptoKeyPair;
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', keys.publicKey));
  const signature = new Uint8Array(await crypto.subtle.sign(
    'Ed25519', keys.privateKey, new TextEncoder().encode(canonicalJson(unsignedValue)),
  ));
  return {
    hubReceiptKey: {
      keyId: 'hub-receipt-key-a',
      algorithm: 'Ed25519',
      publicKeyB64: encodeB64(publicKey),
    },
    curation: {
      curationId: 'curation-a',
      offerId: 'offer-a',
      admissionDigest: '4'.repeat(64),
      state: 'admitted',
      receipt: {
        version: 'ananta.speech-evidence-admission-receipt.v1',
        receiptId: '1'.repeat(64),
        admissionDigest: '4'.repeat(64),
        offerId: 'offer-a',
        inventoryRootDigest: '2'.repeat(64),
        resolutionDigest: '5'.repeat(64),
        acceptedGroupIds,
        rejectedGroupIds,
        quarantinedGroupIds,
        consentDigest: '3'.repeat(64),
        policyDigest: '6'.repeat(64),
        resultDigest,
        pairId: 'pair-a',
        direction: 'sender_to_receiver',
        issuedAtMs: Number(unsignedValue.issued_at_ms),
        hubKeyId: 'hub-receipt-key-a',
        signatureB64: encodeB64(signature),
        unsignedValue,
      },
      curationTaskId: 'task-a',
      datasetId: 'dataset-a',
      datasetParentDigest: null,
      datasetManifestDigest: null,
      consentVersion: 1,
      revocationEpoch: 0,
    },
  };
}

function encodeB64(value: Uint8Array): string {
  return btoa(String.fromCharCode(...value));
}
