import { describe, expect, it, vi } from 'vitest';

import { SfuLayerProjectionValidator } from './sfu-layer-projection.validator';

function response(version: number, previous: number, token: number) {
  const document = {
    projection_version: version,
    tenant_ref: 'tenant-a', room_ref: 'room-a', membership_epoch: 2,
    fencing: { fencing_token: token, expected_previous_projection_version: previous },
  };
  return {
    status: 'projection' as const, cursor: version, etag: null, document,
    rawDocument: JSON.stringify(document), digest: 'a'.repeat(64), signature: 'signed', signatureKeyId: 'key-1',
  };
}

const binding = {
  kind: 'room' as const, tenantRef: 'tenant-a', roomRef: 'room-a',
  subjectRef: 'room-a', membershipEpoch: 2,
};

describe('SfuLayerProjectionValidator', () => {
  it('accepts one signed contiguous projection and rejects replay', () => {
    const contracts = {
      validate: vi.fn((_id: string, _raw: string, context: {
        verifySignature: (contractId: string, document: object) => boolean;
      }) => ({
        valid: context.verifySignature('ananta.sfu-room-session-projection.v1', response(1, 0, 1).document),
        reasonCode: 'ok',
        contract: { contractId: 'ananta.sfu-room-session-projection.v1', document: response(1, 0, 1).document },
      })),
    };
    const validator = new SfuLayerProjectionValidator(contracts as never, { verify: () => true });
    expect(validator.validate(response(1, 0, 1), binding).valid).toBe(true);
    expect(validator.validate(response(1, 0, 1), binding)).toMatchObject({ valid: false, reasonCode: 'sfu_projection_reordered' });
  });

  it('rejects gaps before schema or signature application', () => {
    const contracts = { validate: vi.fn(() => ({
      valid: true, reasonCode: 'ok',
      contract: { contractId: 'ananta.sfu-room-session-projection.v1', document: response(1, 0, 1).document },
    })) };
    const validator = new SfuLayerProjectionValidator(contracts as never, { verify: () => true });
    expect(validator.validate(response(1, 0, 1), binding).valid).toBe(true);
    contracts.validate.mockClear();
    expect(validator.validate(response(3, 2, 3), binding)).toMatchObject({ valid: false, reasonCode: 'sfu_projection_gap' });
    expect(contracts.validate).not.toHaveBeenCalled();
  });

  it('fails closed when detached signature verification fails', () => {
    const contracts = {
      validate: (_id: string, _raw: string, context: { verifySignature: (id: string, value: object) => boolean }) => ({
        valid: context.verifySignature('ananta.sfu-room-session-projection.v1', response(1, 0, 1).document),
        reasonCode: 'contract_signature_invalid',
      }),
    };
    const validator = new SfuLayerProjectionValidator(contracts as never, { verify: () => false });
    expect(validator.validate(response(1, 0, 1), binding)).toMatchObject({ valid: false, reasonCode: 'contract_signature_invalid' });
  });

  it('supports async WebCrypto verification and commits staged state only after apply', async () => {
    const contracts = {
      validate: vi.fn((_id: string, _raw: string, context: {
        verifySignature: (contractId: string, document: object) => boolean;
      }) => ({
        valid: context.verifySignature('ananta.sfu-room-session-projection.v1', response(1, 0, 1).document),
        reasonCode: 'ok',
        contract: { contractId: 'ananta.sfu-room-session-projection.v1', document: response(1, 0, 1).document },
      })),
    };
    const validator = new SfuLayerProjectionValidator(
      contracts as never,
      { verify: async () => true },
    );
    const staged = await validator.validateAsync(response(1, 0, 1), binding, Date.now(), false);
    expect(staged.valid).toBe(true);
    expect(await validator.validateAsync(response(1, 0, 1), binding, Date.now(), false))
      .toMatchObject({ valid: true });
    expect(staged.valid && validator.commit(staged.projection)).toBe(true);
    expect(await validator.validateAsync(response(1, 0, 1), binding))
      .toMatchObject({ valid: false, reasonCode: 'sfu_projection_reordered' });
  });
});
