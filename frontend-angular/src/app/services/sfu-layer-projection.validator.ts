import { Inject, Injectable, InjectionToken } from '@angular/core';

import type {
  SfuBroadcastJsonObject,
  SfuBroadcastJsonValue,
  ValidatedSfuBroadcastContract,
} from './sfu-broadcast-contracts';
import {
  SfuBroadcastContractValidatorService,
  type SfuBroadcastValidationContext,
} from './sfu-broadcast-contract-validator.service';
import type { SfuLayerProjectionApiResult, SfuLayerProjectionKind } from './sfu-layer-projection-api.service';

export interface SfuProjectionSignatureVerifierPort {
  verify(input: Readonly<{
    contractId: string; document: SfuBroadcastJsonObject; digest: string;
    signature: string; keyId: string; algorithm: 'Ed25519' | 'HMAC-SHA-256';
    algorithmVersion: number; keyVersion: number;
  }>): boolean | Promise<boolean>;
}

export const SFU_PROJECTION_SIGNATURE_VERIFIER = new InjectionToken<SfuProjectionSignatureVerifierPort>(
  'SFU_PROJECTION_SIGNATURE_VERIFIER',
  { providedIn: 'root', factory: () => Object.freeze({ verify: () => false }) },
);

export interface SfuProjectionValidationBinding {
  readonly kind: SfuLayerProjectionKind;
  readonly tenantRef: string;
  readonly roomRef: string;
  readonly subjectRef: string;
  readonly membershipEpoch: number;
  readonly admissionEpoch?: number;
  readonly routeEpoch?: number;
  readonly topologyEpoch?: number;
  readonly keyEpoch?: number;
  readonly sessionProjectionVersion?: number;
}

export type ValidatedLayerProjection = Readonly<{
  brand: 'validated-layer-projection';
  kind: SfuLayerProjectionKind;
  scopeKey: string;
  version: number;
  fencingToken: number;
  contract: Extract<ValidatedSfuBroadcastContract, {
    contractId:
      | 'ananta.sfu-room-session-projection.v1'
      | 'ananta.sfu-publisher-layer-projection.v1'
      | 'ananta.sfu-receiver-layer-projection.v1';
  }>;
}>;

export type SfuProjectionValidationResult =
  | Readonly<{ valid: true; reasonCode: 'ok'; projection: ValidatedLayerProjection }>
  | Readonly<{ valid: false; reasonCode: string; projection: null }>;

interface ProjectionState { version: number; fencingToken: number; }

@Injectable({ providedIn: 'root' })
export class SfuLayerProjectionValidator {
  private readonly state = new Map<string, ProjectionState>();

  constructor(
    @Inject(SfuBroadcastContractValidatorService) private readonly contracts: SfuBroadcastContractValidatorService,
    @Inject(SFU_PROJECTION_SIGNATURE_VERIFIER) private readonly signatures: SfuProjectionSignatureVerifierPort,
  ) {}

  validate(
    response: Extract<SfuLayerProjectionApiResult, { status: 'projection' }>,
    binding: SfuProjectionValidationBinding,
    nowEpochMs = Date.now(),
  ): SfuProjectionValidationResult {
    const signature = this.signatures.verify(signatureInput(response, binding));
    if (isPromiseLike(signature)) return rejected('contract_signature_invalid');
    return this.validateVerified(response, binding, nowEpochMs, signature, true);
  }

  async validateAsync(
    response: Extract<SfuLayerProjectionApiResult, { status: 'projection' }>,
    binding: SfuProjectionValidationBinding,
    nowEpochMs = Date.now(),
    commit = true,
  ): Promise<SfuProjectionValidationResult> {
    let signatureValid = false;
    try {
      signatureValid = await this.signatures.verify(signatureInput(response, binding));
    } catch {
      signatureValid = false;
    }
    return this.validateVerified(response, binding, nowEpochMs, signatureValid, commit);
  }

  commit(projection: ValidatedLayerProjection): boolean {
    const previous = this.state.get(projection.scopeKey);
    if (previous && (projection.version <= previous.version || projection.fencingToken <= previous.fencingToken)) {
      return false;
    }
    this.state.set(projection.scopeKey, {
      version: projection.version,
      fencingToken: projection.fencingToken,
    });
    return true;
  }

  private validateVerified(
    response: Extract<SfuLayerProjectionApiResult, { status: 'projection' }>,
    binding: SfuProjectionValidationBinding,
    nowEpochMs: number,
    signatureValid: boolean,
    commit: boolean,
  ): SfuProjectionValidationResult {
    const contractId = contractIdFor(binding.kind);
    const scopeKey = projectionScopeKey(binding);
    const previous = this.state.get(scopeKey) ?? { version: 0, fencingToken: 0 };
    const document = response.document;
    const version = integer(document['projection_version']);
    const fencing = record(document['fencing']);
    const token = fencing ? integer(fencing['fencing_token']) : null;
    const expectedPrevious = fencing ? integer(fencing['expected_previous_projection_version']) : null;
    if (version === null || token === null || expectedPrevious === null) return rejected('sfu_projection_fencing_invalid');
    if (previous.version > 0 && (version <= previous.version || token <= previous.fencingToken)) {
      return rejected('sfu_projection_reordered');
    }
    if (previous.version > 0 && (version !== previous.version + 1 || expectedPrevious !== previous.version)) {
      return rejected('sfu_projection_gap');
    }
    if (binding.sessionProjectionVersion !== undefined
        && document['session_projection_version'] !== binding.sessionProjectionVersion) {
      return rejected('sfu_projection_parent_mismatch');
    }
    const expectedScope: Record<string, SfuBroadcastJsonValue> = {
      tenant_ref: binding.tenantRef, room_ref: binding.roomRef,
    };
    if (binding.kind === 'publisher') expectedScope['publication_ref'] = binding.subjectRef;
    if (binding.kind === 'receiver') expectedScope['subscription_ref'] = binding.subjectRef;
    const latestEpochs: Record<string, number> = { membership_epoch: binding.membershipEpoch };
    if (binding.admissionEpoch !== undefined) latestEpochs['admission_epoch'] = binding.admissionEpoch;
    if (binding.routeEpoch !== undefined) latestEpochs['route_epoch'] = binding.routeEpoch;
    if (binding.topologyEpoch !== undefined) latestEpochs['topology_epoch'] = binding.topologyEpoch;
    if (binding.keyEpoch !== undefined) latestEpochs['key_epoch'] = binding.keyEpoch;
    const context: SfuBroadcastValidationContext = {
      nowEpochMs, expectedScope, latestEpochs,
      acceptedSequences: new Set<number>(), lastSequence: previous.version || null,
      metadata: Object.freeze({ sessionProjectionVersion: binding.sessionProjectionVersion ?? null }),
      verifySignature: (id, checked) => id === contractId
        && signatureValid
        && jsonEquivalent(checked, response.document),
    };
    const result = this.contracts.validate(contractId, response.rawDocument, context);
    if (!result.valid) return rejected(result.reasonCode);
    const projection = Object.freeze({
      brand: 'validated-layer-projection' as const, kind: binding.kind, scopeKey,
      version, fencingToken: token, contract: result.contract as ValidatedLayerProjection['contract'],
    });
    if (commit && !this.commit(projection)) return rejected('sfu_projection_reordered');
    return {
      valid: true, reasonCode: 'ok',
      projection,
    };
  }

  reset(binding: SfuProjectionValidationBinding): void {
    this.state.delete(projectionScopeKey(binding));
  }
}

function contractIdFor(kind: SfuLayerProjectionKind): string {
  if (kind === 'room') return 'ananta.sfu-room-session-projection.v1';
  return kind === 'publisher'
    ? 'ananta.sfu-publisher-layer-projection.v1'
    : 'ananta.sfu-receiver-layer-projection.v1';
}

function projectionScopeKey(binding: SfuProjectionValidationBinding): string {
  return [
    binding.kind, binding.tenantRef, binding.roomRef, binding.subjectRef,
    binding.membershipEpoch, binding.admissionEpoch ?? 0, binding.routeEpoch ?? 0, binding.topologyEpoch ?? 0,
    binding.keyEpoch ?? 0, binding.sessionProjectionVersion ?? 0,
  ].join('\u0000');
}

function signatureInput(
  response: Extract<SfuLayerProjectionApiResult, { status: 'projection' }>,
  binding: SfuProjectionValidationBinding,
) {
  return Object.freeze({
    contractId: contractIdFor(binding.kind), document: response.document,
    digest: response.digest, signature: response.signature, keyId: response.signatureKeyId,
    algorithm: response.signatureAlgorithm,
    algorithmVersion: response.signatureAlgorithmVersion,
    keyVersion: response.signatureKeyVersion,
  });
}

function isPromiseLike(value: boolean | Promise<boolean>): value is Promise<boolean> {
  return typeof value === 'object' && value !== null && typeof value.then === 'function';
}

function jsonEquivalent(left: SfuBroadcastJsonValue, right: SfuBroadcastJsonValue): boolean {
  if (left === right) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => jsonEquivalent(value, right[index]));
  }
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  const leftKeys = Object.keys(left).sort();
  const rightKeys = Object.keys(right).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index]
      && jsonEquivalent(left[key], right[key]));
}

function record(value: unknown): SfuBroadcastJsonObject | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as SfuBroadcastJsonObject : null;
}

function integer(value: unknown): number | null {
  return Number.isSafeInteger(value) && Number(value) >= 0 ? Number(value) : null;
}

function rejected(reasonCode: string): SfuProjectionValidationResult {
  return Object.freeze({ valid: false, reasonCode, projection: null });
}
