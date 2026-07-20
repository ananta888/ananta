import { Injectable } from '@angular/core';

export interface SemanticValidationCriteria {
  readonly schema_valid: boolean;
  readonly binding_valid: boolean;
  readonly quality_score: number;
  readonly drift_score: number;
  readonly deadline_met: boolean;
}

export interface SemanticValidatorContext {
  readonly reportId: string; readonly sessionId: string; readonly contractId: string;
  readonly validatorLeaseId: string; readonly validatorId: string; readonly epoch: number;
  readonly sequence: number; readonly inputDigest: string; readonly outputDigest: string;
  readonly observedAtMs: number; readonly expiresAtMs: number;
}

export interface UnsignedSemanticValidatorReport {
  readonly schema: 'ananta.semantic-validator-report.v1';
  readonly report_id: string; readonly session_id: string; readonly contract_id: string;
  readonly validator_lease_id: string; readonly validator_id: string;
  readonly validator_role: 'validator'; readonly audience: 'hub'; readonly epoch: number;
  readonly sequence: number; readonly input_digest: string; readonly output_digest: string;
  readonly criteria: SemanticValidationCriteria; readonly verdict: 'pass' | 'fail';
  readonly observed_at_ms: number; readonly expires_at_ms: number;
}

export interface SemanticValidatorReport extends UnsignedSemanticValidatorReport { readonly signature: string }
export interface SemanticValidatorSignerPort { sign(canonicalPayload: string): Promise<string> }

@Injectable({ providedIn: 'root' })
export class SemanticValidatorService {
  async createReport(
    context: Readonly<SemanticValidatorContext>,
    criteria: Readonly<SemanticValidationCriteria>,
    signer: SemanticValidatorSignerPort,
  ): Promise<SemanticValidatorReport> {
    validateContext(context); validateCriteria(criteria);
    const verdict = criteria.schema_valid && criteria.binding_valid && criteria.deadline_met
      && criteria.quality_score >= 0.75 && criteria.drift_score <= 0.15 ? 'pass' : 'fail';
    const unsigned: UnsignedSemanticValidatorReport = deepFreeze({
      schema: 'ananta.semantic-validator-report.v1', report_id: context.reportId,
      session_id: context.sessionId, contract_id: context.contractId,
      validator_lease_id: context.validatorLeaseId, validator_id: context.validatorId,
      validator_role: 'validator', audience: 'hub', epoch: context.epoch, sequence: context.sequence,
      input_digest: context.inputDigest, output_digest: context.outputDigest,
      criteria: { ...criteria }, verdict, observed_at_ms: context.observedAtMs, expires_at_ms: context.expiresAtMs,
    }) as unknown as UnsignedSemanticValidatorReport;
    const signature = await signer.sign(canonical(unsigned));
    if (!/^[A-Za-z0-9._~+/-]{16,1024}={0,2}$/.test(signature)) throw new Error('validator_signature_invalid');
    return deepFreeze({ ...unsigned, signature }) as unknown as SemanticValidatorReport;
  }
}

function validateContext(value: SemanticValidatorContext): void {
  for (const field of ['reportId', 'sessionId', 'contractId', 'validatorLeaseId', 'validatorId'] as const) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(value[field])) throw new Error('validator_context_invalid');
  }
  for (const digest of [value.inputDigest, value.outputDigest]) {
    if (!/^[0-9a-f]{64}$/.test(digest)) throw new Error('validator_context_invalid');
  }
  if (!Number.isSafeInteger(value.epoch) || value.epoch < 1 || !Number.isSafeInteger(value.sequence)
      || value.sequence < 0 || !Number.isSafeInteger(value.observedAtMs) || value.observedAtMs < 0
      || !Number.isSafeInteger(value.expiresAtMs) || value.expiresAtMs <= value.observedAtMs
      || value.expiresAtMs - value.observedAtMs > 60_000) throw new Error('validator_context_invalid');
}
function validateCriteria(value: SemanticValidationCriteria): void {
  if (typeof value.schema_valid !== 'boolean' || typeof value.binding_valid !== 'boolean'
      || typeof value.deadline_met !== 'boolean' || !Number.isFinite(value.quality_score)
      || value.quality_score < 0 || value.quality_score > 1 || !Number.isFinite(value.drift_score)
      || value.drift_score < 0 || value.drift_score > 1) throw new Error('validator_criteria_invalid');
}
function canonical(value: object): string {
  const ordered = (item: any): any => Array.isArray(item) ? item.map(ordered)
    : item && typeof item === 'object' ? Object.fromEntries(Object.keys(item).sort().map(key => [key, ordered(item[key])])) : item;
  return JSON.stringify(ordered(value));
}
function deepFreeze<T>(value: T): Readonly<T> {
  if (value !== null && typeof value === 'object') {
    Object.values(value as Record<string, unknown>).forEach(item => deepFreeze(item)); Object.freeze(value);
  }
  return value;
}
