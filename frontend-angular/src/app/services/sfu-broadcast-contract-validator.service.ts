import { Injectable } from '@angular/core';

import { sha256Fallback } from '../shared/crypto/sha256';
import {
  SFU_BROADCAST_CONTRACT_DEFINITIONS,
  SFU_BROADCAST_SCHEMA_DOCUMENTS,
  SFU_BROADCAST_STRUCTURAL_LIMITS,
  SfuBroadcastContractDefinition,
  SfuBroadcastContractDocument,
  SfuBroadcastContractId,
  SfuBroadcastJsonObject,
  SfuBroadcastJsonValue,
  ValidatedSfuBroadcastContract,
} from './sfu-broadcast-contracts';

export type SfuBroadcastValidationPhase = 'structural' | 'schema' | 'semantics' | 'signature' | 'accepted';
export type SfuBroadcastReasonCode =
  | 'ok'
  | 'contract_definition_unavailable'
  | 'contract_input_type_invalid'
  | 'contract_document_bytes_exceeded'
  | 'contract_depth_exceeded'
  | 'contract_node_count_exceeded'
  | 'contract_collection_count_exceeded'
  | 'contract_string_bytes_exceeded'
  | 'contract_total_string_bytes_exceeded'
  | 'contract_json_invalid'
  | 'contract_root_object_required'
  | 'contract_dangerous_property'
  | 'contract_unicode_normalization_invalid'
  | 'contract_integer_range_exceeded'
  | 'contract_schema_unavailable'
  | 'contract_unknown_property'
  | 'contract_schema_invalid'
  | 'contract_expiry_unavailable'
  | 'contract_expired'
  | 'contract_sequence_unavailable'
  | 'contract_replay'
  | 'contract_epoch_context_unavailable'
  | 'contract_stale_epoch'
  | 'contract_epoch_mismatch'
  | 'contract_scope_context_unavailable'
  | 'contract_cross_scope'
  | 'receiver_group_digest_context_unavailable'
  | 'receiver_group_member_digest_mismatch'
  | 'contract_signature_invalid';

export interface SfuBroadcastValidationContext {
  readonly nowEpochMs: number;
  readonly expectedScope: Readonly<Record<string, SfuBroadcastJsonValue>>;
  readonly latestEpochs: Readonly<Record<string, number>>;
  readonly acceptedSequences: ReadonlySet<number>;
  readonly lastSequence: number | null;
  readonly metadata: Readonly<Record<string, unknown>>;
  readonly verifySignature: (contractId: SfuBroadcastContractId, document: SfuBroadcastJsonObject) => boolean;
}

export type SfuBroadcastValidationResult =
  | Readonly<{
      valid: true;
      phase: 'accepted';
      reasonCode: 'ok';
      contractId: SfuBroadcastContractId;
      schemaVersion: '1';
      detail: null;
      contract: ValidatedSfuBroadcastContract;
    }>
  | Readonly<{
      valid: false;
      phase: Exclude<SfuBroadcastValidationPhase, 'accepted'>;
      reasonCode: Exclude<SfuBroadcastReasonCode, 'ok'>;
      contractId: string;
      schemaVersion: '1' | 'unknown';
      detail: string | null;
    }>;

interface SchemaIssue {
  readonly keyword: string;
  readonly path: string;
}

class SchemaUnavailable extends Error {}

const DANGEROUS_KEYS = new Set(['__proto__', 'prototype', 'constructor']);
const UTC_SECOND = /^[0-9]{4}-(0[1-9]|1[0-2])-([0-2][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$/;
const UTF8 = new TextEncoder();

@Injectable({ providedIn: 'root' })
export class SfuBroadcastContractValidatorService {
  private readonly registry = buildSchemaRegistry();

  validate(
    contractId: string,
    rawDocument: string | Uint8Array,
    context: SfuBroadcastValidationContext,
  ): SfuBroadcastValidationResult {
    const definition = contractDefinition(contractId);
    if (!definition) return rejection(contractId, 'unknown', 'schema', 'contract_definition_unavailable');

    const decoded = decodeBounded(rawDocument);
    if (typeof decoded === 'string') return rejection(contractId, '1', 'structural', decoded);
    const document = decoded;

    let issues: SchemaIssue[];
    try {
      issues = validateJsonSchema(definition.schemaDocument, document, this.registry);
    } catch {
      return rejection(contractId, '1', 'schema', 'contract_schema_unavailable');
    }
    if (issues.length > 0) {
      issues.sort((left, right) => left.path.localeCompare(right.path) || left.keyword.localeCompare(right.keyword));
      const first = issues[0];
      return rejection(
        contractId,
        '1',
        'schema',
        first.keyword === 'additionalProperties' || first.keyword === 'unevaluatedProperties'
          ? 'contract_unknown_property'
          : 'contract_schema_invalid',
        first.path || '/',
      );
    }

    const semanticFailure = validateSemantics(definition, document, context);
    if (semanticFailure) return rejection(contractId, '1', 'semantics', semanticFailure);

    let trusted = false;
    try {
      trusted = context.verifySignature(definition.contractId, document) === true;
    } catch {
      trusted = false;
    }
    if (!trusted) return rejection(contractId, '1', 'signature', 'contract_signature_invalid');

    const frozen = deepFreeze(document);
    return {
      valid: true,
      phase: 'accepted',
      reasonCode: 'ok',
      contractId: definition.contractId,
      schemaVersion: '1',
      detail: null,
      contract: validatedContract(definition.contractId, frozen),
    };
  }
}

function contractDefinition(contractId: string): SfuBroadcastContractDefinition | null {
  return Object.prototype.hasOwnProperty.call(SFU_BROADCAST_CONTRACT_DEFINITIONS, contractId)
    ? SFU_BROADCAST_CONTRACT_DEFINITIONS[contractId as SfuBroadcastContractId]
    : null;
}

function rejection(
  contractId: string,
  schemaVersion: '1' | 'unknown',
  phase: Exclude<SfuBroadcastValidationPhase, 'accepted'>,
  reasonCode: Exclude<SfuBroadcastReasonCode, 'ok'>,
  detail: string | null = null,
): SfuBroadcastValidationResult {
  return { valid: false, phase, reasonCode, contractId, schemaVersion, detail };
}

function decodeBounded(rawDocument: string | Uint8Array): SfuBroadcastJsonObject | Exclude<SfuBroadcastReasonCode, 'ok'> {
  if (typeof rawDocument !== 'string' && !(rawDocument instanceof Uint8Array)) return 'contract_input_type_invalid';
  const raw = typeof rawDocument === 'string' ? UTF8.encode(rawDocument) : rawDocument;
  if (raw.byteLength > SFU_BROADCAST_STRUCTURAL_LIMITS.maxDocumentBytes) return 'contract_document_bytes_exceeded';
  const scanFailure = scanRaw(raw);
  if (scanFailure) return scanFailure;

  let text: string;
  let value: unknown;
  try {
    text = typeof rawDocument === 'string'
      ? rawDocument
      : new TextDecoder('utf-8', { fatal: true }).decode(rawDocument);
    value = JSON.parse(text) as unknown;
  } catch {
    return 'contract_json_invalid';
  }
  if (!isPlainRecord(value)) return 'contract_root_object_required';
  const decodedFailure = inspectDecoded(value);
  return decodedFailure ?? value;
}

function scanRaw(raw: Uint8Array): Exclude<SfuBroadcastReasonCode, 'ok'> | null {
  let depth = 0;
  let inString = false;
  let escaped = false;
  let stringBytes = 0;
  let totalStringBytes = 0;
  for (const byte of raw) {
    if (inString) {
      if (escaped) { escaped = false; stringBytes += 1; continue; }
      if (byte === 0x5c) { escaped = true; stringBytes += 1; continue; }
      if (byte === 0x22) {
        inString = false;
        totalStringBytes += stringBytes;
        if (stringBytes > SFU_BROADCAST_STRUCTURAL_LIMITS.maxStringBytes) return 'contract_string_bytes_exceeded';
        if (totalStringBytes > SFU_BROADCAST_STRUCTURAL_LIMITS.maxTotalStringBytes) return 'contract_total_string_bytes_exceeded';
        stringBytes = 0;
        continue;
      }
      stringBytes += 1;
    } else if (byte === 0x22) {
      inString = true;
    } else if (byte === 0x7b || byte === 0x5b) {
      depth += 1;
      if (depth > SFU_BROADCAST_STRUCTURAL_LIMITS.maxDepth) return 'contract_depth_exceeded';
    } else if (byte === 0x7d || byte === 0x5d) {
      depth -= 1;
      if (depth < 0) return 'contract_json_invalid';
    }
  }
  return null;
}

function inspectDecoded(root: SfuBroadcastJsonObject): Exclude<SfuBroadcastReasonCode, 'ok'> | null {
  const stack: Array<Readonly<{ value: unknown; depth: number }>> = [{ value: root, depth: 1 }];
  let nodes = 0;
  let totalStringBytes = 0;
  while (stack.length > 0) {
    const entry = stack.pop();
    if (!entry) break;
    nodes += 1;
    if (nodes > SFU_BROADCAST_STRUCTURAL_LIMITS.maxNodes) return 'contract_node_count_exceeded';
    if (entry.depth > SFU_BROADCAST_STRUCTURAL_LIMITS.maxDepth) return 'contract_depth_exceeded';
    const value = entry.value;
    if (Array.isArray(value)) {
      if (value.length > SFU_BROADCAST_STRUCTURAL_LIMITS.maxCollectionItems) return 'contract_collection_count_exceeded';
      for (const child of value) stack.push({ value: child, depth: entry.depth + 1 });
      continue;
    }
    if (isPlainRecord(value)) {
      const keys = Object.keys(value);
      if (keys.length > SFU_BROADCAST_STRUCTURAL_LIMITS.maxCollectionItems) return 'contract_collection_count_exceeded';
      const normalized = new Set<string>();
      for (const key of keys) {
        if (DANGEROUS_KEYS.has(key)) return 'contract_dangerous_property';
        if (!isCanonicalUnicode(key) || normalized.has(key.normalize('NFC'))) return 'contract_unicode_normalization_invalid';
        normalized.add(key.normalize('NFC'));
        const bytes = UTF8.encode(key).byteLength;
        if (bytes > SFU_BROADCAST_STRUCTURAL_LIMITS.maxStringBytes) return 'contract_string_bytes_exceeded';
        totalStringBytes += bytes;
        stack.push({ value: value[key], depth: entry.depth + 1 });
      }
    } else if (typeof value === 'string') {
      if (!isCanonicalUnicode(value)) return 'contract_unicode_normalization_invalid';
      const bytes = UTF8.encode(value).byteLength;
      if (bytes > SFU_BROADCAST_STRUCTURAL_LIMITS.maxStringBytes) return 'contract_string_bytes_exceeded';
      totalStringBytes += bytes;
    } else if (typeof value === 'number') {
      if (!Number.isFinite(value)) return 'contract_json_invalid';
      if (Math.abs(value) > Number.MAX_SAFE_INTEGER || (Number.isInteger(value) && !Number.isSafeInteger(value))) {
        return 'contract_integer_range_exceeded';
      }
    } else if (value !== null && typeof value !== 'boolean') {
      return 'contract_json_invalid';
    }
    if (totalStringBytes > SFU_BROADCAST_STRUCTURAL_LIMITS.maxTotalStringBytes) return 'contract_total_string_bytes_exceeded';
  }
  return null;
}

function isCanonicalUnicode(value: string): boolean {
  if (value.normalize('NFC') !== value) return false;
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return false;
    }
  }
  return true;
}

function buildSchemaRegistry(): ReadonlyMap<string, SfuBroadcastJsonObject> {
  const registry = new Map<string, SfuBroadcastJsonObject>();
  for (const candidate of SFU_BROADCAST_SCHEMA_DOCUMENTS) {
    if (!isPlainRecord(candidate) || typeof candidate['$id'] !== 'string') throw new SchemaUnavailable();
    registry.set(candidate['$id'], candidate);
  }
  return registry;
}

function validateJsonSchema(
  schemaDocument: unknown,
  value: SfuBroadcastJsonValue,
  registry: ReadonlyMap<string, SfuBroadcastJsonObject>,
): SchemaIssue[] {
  if (!isPlainRecord(schemaDocument)) throw new SchemaUnavailable();
  const issues: SchemaIssue[] = [];
  evaluateSchema(schemaDocument, value, schemaDocument, '', registry, issues, 0);
  return issues;
}

function evaluateSchema(
  schema: unknown,
  value: SfuBroadcastJsonValue,
  root: SfuBroadcastJsonObject,
  path: string,
  registry: ReadonlyMap<string, SfuBroadcastJsonObject>,
  issues: SchemaIssue[],
  refDepth: number,
): void {
  if (schema === true) return;
  if (schema === false) { issues.push({ keyword: 'falseSchema', path }); return; }
  if (!isPlainRecord(schema) || refDepth > 64) throw new SchemaUnavailable();

  const reference = schema['$ref'];
  if (reference !== undefined) {
    if (typeof reference !== 'string') throw new SchemaUnavailable();
    const resolved = resolveSchemaReference(reference, root, registry);
    evaluateSchema(resolved.schema, value, resolved.root, path, registry, issues, refDepth + 1);
  }
  const allOf = schema['allOf'];
  if (Array.isArray(allOf)) for (const child of allOf) evaluateSchema(child, value, root, path, registry, issues, refDepth);
  const oneOf = schema['oneOf'];
  if (Array.isArray(oneOf)) {
    let matches = 0;
    for (const child of oneOf) {
      const branch: SchemaIssue[] = [];
      evaluateSchema(child, value, root, path, registry, branch, refDepth);
      if (branch.length === 0) matches += 1;
    }
    if (matches !== 1) issues.push({ keyword: 'oneOf', path });
  }
  const conditional = schema['if'];
  if (conditional !== undefined) {
    const conditionIssues: SchemaIssue[] = [];
    evaluateSchema(conditional, value, root, path, registry, conditionIssues, refDepth);
    if (conditionIssues.length === 0 && schema['then'] !== undefined) {
      evaluateSchema(schema['then'], value, root, path, registry, issues, refDepth);
    } else if (conditionIssues.length > 0 && schema['else'] !== undefined) {
      evaluateSchema(schema['else'], value, root, path, registry, issues, refDepth);
    }
  }
  if (schema['not'] !== undefined) {
    const inverse: SchemaIssue[] = [];
    evaluateSchema(schema['not'], value, root, path, registry, inverse, refDepth);
    if (inverse.length === 0) issues.push({ keyword: 'not', path });
  }

  if (schema['const'] !== undefined && !jsonEqual(value, schema['const'])) issues.push({ keyword: 'const', path });
  if (Array.isArray(schema['enum']) && !schema['enum'].some(candidate => jsonEqual(value, candidate))) issues.push({ keyword: 'enum', path });
  const expectedType = schema['type'];
  if (typeof expectedType === 'string' && !matchesType(value, expectedType)) {
    issues.push({ keyword: 'type', path });
    return;
  }

  if (typeof value === 'string') validateStringSchema(schema, value, path, issues);
  if (typeof value === 'number') validateNumberSchema(schema, value, path, issues);
  if (Array.isArray(value)) validateArraySchema(schema, value, root, path, registry, issues, refDepth);
  if (isPlainRecord(value)) validateObjectSchema(schema, value, root, path, registry, issues, refDepth);
}

function validateStringSchema(schema: SfuBroadcastJsonObject, value: string, path: string, issues: SchemaIssue[]): void {
  const length = Array.from(value).length;
  if (typeof schema['minLength'] === 'number' && length < schema['minLength']) issues.push({ keyword: 'minLength', path });
  if (typeof schema['maxLength'] === 'number' && length > schema['maxLength']) issues.push({ keyword: 'maxLength', path });
  if (typeof schema['pattern'] === 'string') {
    let pattern: RegExp;
    try { pattern = new RegExp(schema['pattern'], 'u'); } catch { throw new SchemaUnavailable(); }
    if (!pattern.test(value)) issues.push({ keyword: 'pattern', path });
  }
  if (schema['format'] === 'date-time' && !validDateTime(value)) issues.push({ keyword: 'format', path });
}

function validateNumberSchema(schema: SfuBroadcastJsonObject, value: number, path: string, issues: SchemaIssue[]): void {
  if (typeof schema['minimum'] === 'number' && value < schema['minimum']) issues.push({ keyword: 'minimum', path });
  if (typeof schema['maximum'] === 'number' && value > schema['maximum']) issues.push({ keyword: 'maximum', path });
  if (typeof schema['exclusiveMinimum'] === 'number' && value <= schema['exclusiveMinimum']) issues.push({ keyword: 'exclusiveMinimum', path });
  if (typeof schema['exclusiveMaximum'] === 'number' && value >= schema['exclusiveMaximum']) issues.push({ keyword: 'exclusiveMaximum', path });
  if (typeof schema['multipleOf'] === 'number' && Math.abs(value / schema['multipleOf'] - Math.round(value / schema['multipleOf'])) > Number.EPSILON) {
    issues.push({ keyword: 'multipleOf', path });
  }
}

function validateArraySchema(
  schema: SfuBroadcastJsonObject,
  value: readonly SfuBroadcastJsonValue[],
  root: SfuBroadcastJsonObject,
  path: string,
  registry: ReadonlyMap<string, SfuBroadcastJsonObject>,
  issues: SchemaIssue[],
  refDepth: number,
): void {
  if (typeof schema['minItems'] === 'number' && value.length < schema['minItems']) issues.push({ keyword: 'minItems', path });
  if (typeof schema['maxItems'] === 'number' && value.length > schema['maxItems']) issues.push({ keyword: 'maxItems', path });
  if (schema['uniqueItems'] === true) {
    const seen = new Set<string>();
    for (const item of value) {
      const key = canonicalJson(item);
      if (seen.has(key)) { issues.push({ keyword: 'uniqueItems', path }); break; }
      seen.add(key);
    }
  }
  if (schema['items'] !== undefined) {
    value.forEach((item, index) => evaluateSchema(schema['items'], item, root, `${path}/${index}`, registry, issues, refDepth));
  }
}

function validateObjectSchema(
  schema: SfuBroadcastJsonObject,
  value: SfuBroadcastJsonObject,
  root: SfuBroadcastJsonObject,
  path: string,
  registry: ReadonlyMap<string, SfuBroadcastJsonObject>,
  issues: SchemaIssue[],
  refDepth: number,
): void {
  const keys = Object.keys(value);
  if (typeof schema['minProperties'] === 'number' && keys.length < schema['minProperties']) issues.push({ keyword: 'minProperties', path });
  if (typeof schema['maxProperties'] === 'number' && keys.length > schema['maxProperties']) issues.push({ keyword: 'maxProperties', path });
  const required = schema['required'];
  if (Array.isArray(required)) {
    for (const key of required) if (typeof key === 'string' && !own(value, key)) issues.push({ keyword: 'required', path });
  }
  const properties = isPlainRecord(schema['properties']) ? schema['properties'] : null;
  if (properties) {
    for (const key of keys) {
      if (own(properties, key)) evaluateSchema(properties[key], value[key], root, `${path}/${escapePointer(key)}`, registry, issues, refDepth);
      else if (schema['additionalProperties'] === false) issues.push({ keyword: 'additionalProperties', path });
      else if (isPlainRecord(schema['additionalProperties']) || typeof schema['additionalProperties'] === 'boolean') {
        evaluateSchema(schema['additionalProperties'], value[key], root, `${path}/${escapePointer(key)}`, registry, issues, refDepth);
      }
    }
  }
  if (schema['propertyNames'] !== undefined) {
    for (const key of keys) evaluateSchema(schema['propertyNames'], key, root, `${path}/${escapePointer(key)}`, registry, issues, refDepth);
  }
  const dependent = isPlainRecord(schema['dependentRequired']) ? schema['dependentRequired'] : null;
  if (dependent) {
    for (const key of keys) {
      const dependencies = dependent[key];
      if (!Array.isArray(dependencies)) continue;
      for (const dependency of dependencies) {
        if (typeof dependency === 'string' && !own(value, dependency)) issues.push({ keyword: 'dependentRequired', path });
      }
    }
  }
}

function resolveSchemaReference(
  reference: string,
  currentRoot: SfuBroadcastJsonObject,
  registry: ReadonlyMap<string, SfuBroadcastJsonObject>,
): Readonly<{ schema: unknown; root: SfuBroadcastJsonObject }> {
  const hash = reference.indexOf('#');
  const base = hash >= 0 ? reference.slice(0, hash) : reference;
  const fragment = hash >= 0 ? reference.slice(hash + 1) : '';
  const root = base ? registry.get(base) : currentRoot;
  if (!root) throw new SchemaUnavailable();
  const schema = fragment ? resolvePointer(root, fragment) : root;
  if (schema === undefined) throw new SchemaUnavailable();
  return { schema, root };
}

function validateSemantics(
  definition: SfuBroadcastContractDefinition,
  document: SfuBroadcastJsonObject,
  context: SfuBroadcastValidationContext,
): Exclude<SfuBroadcastReasonCode, 'ok'> | null {
  const expiry = resolveExpiry(document, definition);
  if (expiry === null || !Number.isSafeInteger(context.nowEpochMs)) return 'contract_expiry_unavailable';
  if (context.nowEpochMs >= expiry) return 'contract_expired';

  const sequence = resolvePointer(document, definition.sequencePointer);
  if (!Number.isSafeInteger(sequence)) return 'contract_sequence_unavailable';
  if (context.acceptedSequences.has(sequence) || (context.lastSequence !== null && sequence <= context.lastSequence)) return 'contract_replay';

  for (const [epochName, pointer] of Object.entries(definition.epochPointers)) {
    if (!own(context.latestEpochs, epochName)) return 'contract_epoch_context_unavailable';
    const candidate = resolvePointer(document, pointer);
    const current = context.latestEpochs[epochName];
    if (!Number.isSafeInteger(candidate) || !Number.isSafeInteger(current)) return 'contract_epoch_context_unavailable';
    if (candidate < current) return 'contract_stale_epoch';
    if (candidate > current) return 'contract_epoch_mismatch';
  }
  for (const [scopeName, pointer] of Object.entries(definition.scopePointers)) {
    if (!own(context.expectedScope, scopeName)) return 'contract_scope_context_unavailable';
    if (!jsonEqual(resolvePointer(document, pointer), context.expectedScope[scopeName])) return 'contract_cross_scope';
  }
  if (definition.receiverGroupDigestRequired) return validateReceiverGroupDigests(document, context.metadata);
  return null;
}

function resolveExpiry(document: SfuBroadcastJsonObject, definition: SfuBroadcastContractDefinition): number | null {
  const rule = definition.expiry;
  const value = resolvePointer(document, rule.valuePointer);
  if (rule.kind === 'epoch_ms') return Number.isSafeInteger(value) ? value : null;
  if (rule.kind === 'utc') return typeof value === 'string' ? parseUtc(value) : null;
  const issuedAt = typeof value === 'string' ? parseUtc(value) : Number.isSafeInteger(value) ? value : null;
  if (issuedAt === null) return null;
  if (rule.kind === 'issued_at_fixed_ttl_ms') return safeSum(issuedAt, rule.fixedTtlMs);
  const ttl = resolvePointer(document, rule.ttlPointer);
  if (!Number.isSafeInteger(ttl)) return null;
  return safeSum(issuedAt, rule.kind === 'issued_at_ttl_seconds' ? ttl * 1_000 : ttl);
}

function validateReceiverGroupDigests(
  document: SfuBroadcastJsonObject,
  metadata: Readonly<Record<string, unknown>>,
): Exclude<SfuBroadcastReasonCode, 'ok'> | null {
  const audience = metadata['resolved_audience'];
  const keys = metadata['test_only_hmac_keys_hex'];
  const publications = document['publications'];
  const scope = document['scope'];
  const epochs = document['epochs'];
  const policy = document['policy'];
  if (!isPlainRecord(audience) || !isPlainRecord(keys) || !isPlainRecord(publications) || !isPlainRecord(scope) || !isPlainRecord(epochs) || !isPlainRecord(policy)) {
    return 'receiver_group_digest_context_unavailable';
  }
  const members = audience['members'];
  const primary = publications['primary_publication_ref'];
  const shared = publications['shared_publication_refs'];
  const digests = publications['member_digests'];
  if (!Array.isArray(members) || typeof primary !== 'string' || !Array.isArray(shared) || !shared.every(item => typeof item === 'string') || !Array.isArray(digests)) {
    return 'receiver_group_digest_context_unavailable';
  }
  const allowed = [primary, ...shared].sort(compareUtf8);
  for (const digest of digests) {
    if (!isPlainRecord(digest)) return 'receiver_group_digest_context_unavailable';
    const publicationRef = digest['publication_ref'];
    const keyRef = digest['key_ref'];
    const supplied = digest['value'];
    const keyHex = typeof keyRef === 'string' ? keys[keyRef] : undefined;
    if (typeof publicationRef !== 'string' || typeof keyRef !== 'string' || typeof supplied !== 'string' || typeof keyHex !== 'string') {
      return 'receiver_group_digest_context_unavailable';
    }
    const bindings: Array<Readonly<{ grant_ref: string; member_ref: string; subscription_ref: string }>> = [];
    for (const member of members) {
      if (!isPlainRecord(member) || !Array.isArray(member['publication_grants']) || typeof member['member_ref'] !== 'string') return 'receiver_group_digest_context_unavailable';
      const matching = member['publication_grants'].filter(grant => isPlainRecord(grant) && grant['publication_ref'] === publicationRef);
      if (matching.length !== 1 || !isPlainRecord(matching[0])) return 'receiver_group_digest_context_unavailable';
      const grant = matching[0];
      if (typeof grant['grant_ref'] !== 'string' || typeof grant['subscription_ref'] !== 'string') return 'receiver_group_digest_context_unavailable';
      bindings.push({ grant_ref: grant['grant_ref'], member_ref: member['member_ref'], subscription_ref: grant['subscription_ref'] });
    }
    bindings.sort((left, right) => compareUtf8(left.member_ref, right.member_ref) || compareUtf8(left.grant_ref, right.grant_ref) || compareUtf8(left.subscription_ref, right.subscription_ref));
    const digestInput: SfuBroadcastJsonObject = {
      allowed_publication_refs: allowed,
      audience_epoch: jsonValue(epochs['audience_epoch']), audience_snapshot_ref: jsonValue(document['audience_snapshot_ref']),
      consent_epoch: jsonValue(epochs['consent_epoch']), consent_scope_ref: jsonValue(scope['consent_scope_ref']),
      group_id: jsonValue(document['group_id']), key_epoch: jsonValue(epochs['key_epoch']), member_bindings: bindings,
      membership_epoch: jsonValue(epochs['membership_epoch']), policy_ref: jsonValue(policy['policy_ref']), policy_version: jsonValue(policy['version']),
      privacy_scope: jsonValue(scope['privacy_scope']), publication_ref: publicationRef,
      publication_scope_ref: jsonValue(scope['publication_scope_ref']), room_ref: jsonValue(scope['room_ref']), tenant_ref: jsonValue(scope['tenant_ref']),
    };
    const key = decodeHex(keyHex);
    if (!key) return 'receiver_group_digest_context_unavailable';
    const expected = hex(hmacSha256(key, concatBytes(UTF8.encode('ananta.webrtc.receiver-group.member-digest.v1'), new Uint8Array([0]), UTF8.encode(canonicalJson(digestInput)))));
    if (!constantTimeEqual(expected, supplied)) return 'receiver_group_member_digest_mismatch';
  }
  return null;
}

function hmacSha256(key: Uint8Array, message: Uint8Array): Uint8Array {
  const normalized = key.byteLength > 64 ? sha256Fallback(key) : key;
  const innerPad = new Uint8Array(64).fill(0x36);
  const outerPad = new Uint8Array(64).fill(0x5c);
  normalized.forEach((byte, index) => { innerPad[index] ^= byte; outerPad[index] ^= byte; });
  return sha256Fallback(concatBytes(outerPad, sha256Fallback(concatBytes(innerPad, message))));
}

function validatedContract(contractId: SfuBroadcastContractId, document: SfuBroadcastJsonObject): ValidatedSfuBroadcastContract {
  switch (contractId) {
    case 'ananta.webrtc.receiver-group-intent.v1': return { contractId, document: document as unknown as Extract<SfuBroadcastContractDocument, { schema: typeof contractId }> };
    case 'ananta.webrtc.fanout-route-intent.v1': return { contractId, document: document as unknown as Extract<SfuBroadcastContractDocument, { schema: typeof contractId }> };
    case 'ananta.receiver-quality-observation.v1': return { contractId, document: document as unknown as Extract<SfuBroadcastContractDocument, { schema: typeof contractId }> };
    case 'ananta.webrtc.fanout-accounting-window.v1': return { contractId, document: document as unknown as Extract<SfuBroadcastContractDocument, { schema: typeof contractId }> };
    case 'sfu_node_observation.v1': return { contractId, document: document as unknown as Extract<SfuBroadcastContractDocument, { schema_version: typeof contractId }> };
    case 'ananta.browser-media-capability-observation.v1': return { contractId, document: document as unknown as Extract<SfuBroadcastContractDocument, { schema: typeof contractId }> };
    case 'ananta.sfu-room-session-projection.v1': return { contractId, document: document as unknown as Extract<SfuBroadcastContractDocument, { schema: typeof contractId }> };
    case 'ananta.sfu-publisher-layer-projection.v1': return { contractId, document: document as unknown as Extract<SfuBroadcastContractDocument, { schema: typeof contractId }> };
    case 'ananta.sfu-receiver-layer-projection.v1': return { contractId, document: document as unknown as Extract<SfuBroadcastContractDocument, { schema: typeof contractId }> };
    case 'ananta.sfu-layer-projection-receipt.v1': return { contractId, document: document as unknown as Extract<SfuBroadcastContractDocument, { schema: typeof contractId }> };
  }
}

function matchesType(value: unknown, type: string): boolean {
  switch (type) {
    case 'object': return isPlainRecord(value);
    case 'array': return Array.isArray(value);
    case 'string': return typeof value === 'string';
    case 'integer': return Number.isSafeInteger(value);
    case 'number': return typeof value === 'number' && Number.isFinite(value);
    case 'boolean': return typeof value === 'boolean';
    case 'null': return value === null;
    default: throw new SchemaUnavailable();
  }
}

function resolvePointer(document: unknown, pointer: string): unknown {
  if (pointer === '') return document;
  if (!pointer.startsWith('/')) return undefined;
  let value = document;
  for (const raw of pointer.slice(1).split('/')) {
    const component = raw.replace(/~1/g, '/').replace(/~0/g, '~');
    if (Array.isArray(value)) {
      if (!/^(0|[1-9][0-9]*)$/.test(component)) return undefined;
      value = value[Number(component)];
    } else if (isPlainRecord(value) && own(value, component)) {
      value = value[component];
    } else {
      return undefined;
    }
  }
  return value;
}

function jsonValue(value: unknown): SfuBroadcastJsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' || Array.isArray(value) || isPlainRecord(value)) {
    return value as SfuBroadcastJsonValue;
  }
  throw new SchemaUnavailable();
}

function isPlainRecord(value: unknown): value is SfuBroadcastJsonObject {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function own(value: object, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function jsonEqual(left: unknown, right: unknown): boolean {
  try { return canonicalJson(jsonValue(left)) === canonicalJson(jsonValue(right)); } catch { return false; }
}

function canonicalJson(value: SfuBroadcastJsonValue): string {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER) throw new SchemaUnavailable();
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(item => canonicalJson(item)).join(',')}]`;
  return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

function deepFreeze<T extends SfuBroadcastJsonValue>(value: T): T {
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

function validDateTime(value: string): boolean {
  return UTC_SECOND.test(value) && parseUtc(value) !== null;
}

function parseUtc(value: string): number | null {
  if (!UTC_SECOND.test(value)) return null;
  const parsed = Date.parse(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

function safeSum(left: number, right: number): number | null {
  const result = left + right;
  return Number.isSafeInteger(result) ? result : null;
}

function compareUtf8(left: string, right: string): number {
  const a = UTF8.encode(left);
  const b = UTF8.encode(right);
  for (let index = 0; index < Math.min(a.length, b.length); index += 1) if (a[index] !== b[index]) return a[index] - b[index];
  return a.length - b.length;
}

function concatBytes(...parts: readonly Uint8Array[]): Uint8Array {
  const output = new Uint8Array(parts.reduce((total, part) => total + part.byteLength, 0));
  let offset = 0;
  for (const part of parts) { output.set(part, offset); offset += part.byteLength; }
  return output;
}

function decodeHex(value: string): Uint8Array | null {
  if (!/^(?:[0-9a-fA-F]{2})+$/.test(value)) return null;
  const output = new Uint8Array(value.length / 2);
  for (let index = 0; index < output.length; index += 1) output[index] = Number.parseInt(value.slice(index * 2, index * 2 + 2), 16);
  return output;
}

function hex(value: Uint8Array): string {
  return Array.from(value).map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function constantTimeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  return difference === 0;
}

function escapePointer(value: string): string {
  return value.replace(/~/g, '~0').replace(/\//g, '~1');
}
