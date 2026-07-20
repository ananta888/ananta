import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import { SemanticSfuGroupKeyApiService } from './semantic-sfu-group-key-api.service';

const roomId = `sfu-${'a'.repeat(32)}`;
const authorization = {
  version: 1, authorization_id: 'auth-1', tenant_id: 'tenant-a', room_id: roomId,
  publication_id: 'mic-a', epoch: 2, previous_epoch: 1, member_set_digest: 'b'.repeat(64),
  member_ids: ['alice', 'bob'], key_package_refs: { alice: 'pkg-a', bob: 'pkg-b' },
  valid_from_ms: 1_000, expires_at_ms: 100_000, rekey_deadline_ms: 2_000,
  reason: 'join', hub_key_id: 'hub-key', membership_epoch: 7, signature_b64: 'A'.repeat(88),
};

describe('SemanticSfuGroupKeyApiService', () => {
  it('sends the closed prepare contract and parses signed membership metadata', async () => {
    const request = vi.fn(() => of({
      ok: true, authorization, hub_key_id: 'hub-key', hub_public_key_b64: 'A'.repeat(44),
    }));
    TestBed.configureTestingModule({ providers: [
      SemanticSfuGroupKeyApiService,
      { provide: HubApiCoreService, useValue: { request } },
    ] });
    const result = await firstValueFrom(TestBed.inject(SemanticSfuGroupKeyApiService).prepareEpoch(
      'http://hub.test', {
        sessionId: 'session-a', membershipEpoch: 7, publicationId: 'mic-a',
        keyPackageRefs: { alice: 'pkg-a', bob: 'pkg-b' }, idempotencyKey: 'prepare-a',
      },
    ));
    expect(request).toHaveBeenCalledWith(
      'POST', 'http://hub.test/v1/semantic-media/sfu/group-keys/epochs', 'http://hub.test',
      { body: {
        session_id: 'session-a', membership_epoch: 7, publication_id: 'mic-a',
        key_package_refs: { alice: 'pkg-a', bob: 'pkg-b' }, idempotency_key: 'prepare-a',
      } },
    );
    expect(result.authorization).toMatchObject({ epoch: 2, membership_epoch: 7, member_ids: ['alice', 'bob'] });
  });

  it('rejects extra authority fields instead of projecting them', async () => {
    const request = vi.fn(() => of({
      ok: true, authorization: { ...authorization, content_key: 'forbidden' },
      hub_key_id: 'hub-key', hub_public_key_b64: 'A'.repeat(44),
    }));
    TestBed.configureTestingModule({ providers: [
      SemanticSfuGroupKeyApiService,
      { provide: HubApiCoreService, useValue: { request } },
    ] });
    await expect(firstValueFrom(TestBed.inject(SemanticSfuGroupKeyApiService).prepareEpoch(
      'http://hub.test', {
        sessionId: 'session-a', membershipEpoch: 7, publicationId: 'mic-a',
        keyPackageRefs: { alice: 'pkg-a', bob: 'pkg-b' }, idempotencyKey: 'prepare-a',
      },
    ))).rejects.toThrow('sfu_group_authorization_invalid');
  });
});
