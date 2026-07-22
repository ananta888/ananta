import { createHmac } from 'node:crypto';

import corpusSource from '../../../../tests/contracts/sfu_broadcast/corpus.v1.json';
import browserBase from '../../../../tests/fixtures/webrtc/browser_media_capability_observation/valid_unknown.v1.json';
import browserMaximum from '../../../../tests/fixtures/webrtc/browser_media_capability_observation/valid_partial.v1.json';
import accountingBase from '../../../../tests/fixtures/webrtc/fanout_accounting_window/valid_measured.v1.json';
import routeBase from '../../../../tests/fixtures/webrtc/fanout_route_intent/valid_livekit_native.v1.json';
import routeMaximum from '../../../../tests/fixtures/webrtc/fanout_route_intent/valid_maximal_livekit_native.v1.json';
import groupBase from '../../../../tests/fixtures/webrtc/receiver_group_intent/valid_group.v1.json';
import groupMaximum from '../../../../tests/fixtures/webrtc/receiver_group_intent/valid_maximal_group.v1.json';
import qualityBase from '../../../../tests/fixtures/webrtc/receiver_quality_observation/valid_privacy_bounded.v1.json';
import qualityMaximum from '../../../../tests/fixtures/webrtc/receiver_quality_observation/valid_max_samples.v1.json';
import nodeBase from '../../../../tests/fixtures/webrtc/sfu_node_observation/positive.runtime-mtls.json';
import receiptBase from '../../../../tests/fixtures/webrtc/sfu_layer_projections/valid_receipt_applied.v1.json';
import receiptMaximum from '../../../../tests/fixtures/webrtc/sfu_layer_projections/valid_receipt_unsupported_fallback.v1.json';
import publisherBase from '../../../../tests/fixtures/webrtc/sfu_layer_projections/valid_publisher_projection.v1.json';
import receiverBase from '../../../../tests/fixtures/webrtc/sfu_layer_projections/valid_receiver_projection.v1.json';
import receiverMaximum from '../../../../tests/fixtures/webrtc/sfu_layer_projections/valid_receiver_unknown_safe_layer.v1.json';
import roomBase from '../../../../tests/fixtures/webrtc/sfu_layer_projections/valid_room_adaptive_receive_only.v1.json';
import roomMaximum from '../../../../tests/fixtures/webrtc/sfu_layer_projections/valid_room_manual_quality.v1.json';

import {
  SFU_BROADCAST_CONTRACT_DEFINITIONS,
  SFU_BROADCAST_STRUCTURAL_LIMITS,
  SfuBroadcastContractId,
  SfuBroadcastJsonObject,
  SfuBroadcastJsonValue,
} from './sfu-broadcast-contracts';
import {
  SfuBroadcastContractValidatorService,
  SfuBroadcastValidationContext,
} from './sfu-broadcast-contract-validator.service';

interface FixtureWrapper {
  readonly document: SfuBroadcastJsonObject;
  readonly validationContext: Readonly<Record<string, unknown>>;
}

interface CorpusCase {
  readonly probe: string;
  readonly expected_code: string;
}

interface CorpusContract {
  readonly contract_id: SfuBroadcastContractId;
  readonly cases: Readonly<Record<string, CorpusCase>>;
}

interface CorpusManifest {
  readonly contracts: readonly CorpusContract[];
  readonly known_blockers: readonly Readonly<{ disposition: string; code: string }>[];
  readonly structural_parity_cases: readonly Readonly<{
    id: string;
    raw_document: string;
    expected_code: string;
  }>[];
}

const corpus = corpusSource as unknown as CorpusManifest;
const BASE_FIXTURES: Readonly<Record<SfuBroadcastContractId, unknown>> = {
  'ananta.webrtc.receiver-group-intent.v1': groupBase,
  'ananta.webrtc.fanout-route-intent.v1': routeBase,
  'ananta.receiver-quality-observation.v1': qualityBase,
  'ananta.webrtc.fanout-accounting-window.v1': accountingBase,
  'sfu_node_observation.v1': nodeBase,
  'ananta.browser-media-capability-observation.v1': browserBase,
  'ananta.sfu-room-session-projection.v1': roomBase,
  'ananta.sfu-publisher-layer-projection.v1': publisherBase,
  'ananta.sfu-receiver-layer-projection.v1': receiverBase,
  'ananta.sfu-layer-projection-receipt.v1': receiptBase,
};
const MAXIMUM_FIXTURES: Readonly<Record<SfuBroadcastContractId, unknown>> = {
  'ananta.webrtc.receiver-group-intent.v1': groupMaximum,
  'ananta.webrtc.fanout-route-intent.v1': routeMaximum,
  'ananta.receiver-quality-observation.v1': qualityMaximum,
  'ananta.webrtc.fanout-accounting-window.v1': accountingBase,
  'sfu_node_observation.v1': nodeBase,
  'ananta.browser-media-capability-observation.v1': browserMaximum,
  'ananta.sfu-room-session-projection.v1': roomMaximum,
  'ananta.sfu-publisher-layer-projection.v1': publisherBase,
  'ananta.sfu-receiver-layer-projection.v1': receiverMaximum,
  'ananta.sfu-layer-projection-receipt.v1': receiptMaximum,
};

describe('SfuBroadcastContractValidatorService', () => {
  const validator = new SfuBroadcastContractValidatorService();

  it('matches every accept/reject reason in the shared corpus after resolving CON002 fixtures', () => {
    expect(corpus.known_blockers).toEqual([]);

    for (const contract of corpus.contracts) {
      for (const [category, corpusCase] of Object.entries(contract.cases)) {
        const wrapper = fixture(contract.contract_id, category === 'maximum');
        let document = clone(wrapper.document);
        let raw = JSON.stringify(document);
        let context = contextFor(contract.contract_id, document, wrapper.validationContext, true);

        if (category === 'unknown_property') {
          document = { ...document, unknown_contract_field: true };
          raw = JSON.stringify(document);
        } else if (category === 'oversize') {
          raw += ' '.repeat(SFU_BROADCAST_STRUCTURAL_LIMITS.maxDocumentBytes + 1 - new TextEncoder().encode(raw).byteLength);
        } else if (category === 'expiry') {
          context = { ...context, nowEpochMs: 8_000_000_000_000 };
        } else if (category === 'replay') {
          const sequence = pointer(document, SFU_BROADCAST_CONTRACT_DEFINITIONS[contract.contract_id].sequencePointer);
          context = { ...context, acceptedSequences: new Set([number(sequence)]) };
        } else if (category === 'stale_epoch') {
          const first = Object.keys(context.latestEpochs)[0];
          context = { ...context, latestEpochs: { ...context.latestEpochs, [first]: context.latestEpochs[first] + 1 } };
        } else if (category === 'cross_scope') {
          const first = Object.keys(context.expectedScope)[0];
          context = { ...context, expectedScope: { ...context.expectedScope, [first]: `${String(context.expectedScope[first])}-other` } };
        } else if (category === 'signature') {
          if (contract.contract_id === 'ananta.webrtc.receiver-group-intent.v1') {
            document = independentlyRepairReceiverDigests(document, wrapper.validationContext);
            raw = JSON.stringify(document);
          }
          context = { ...context, verifySignature: () => false };
        }

        const result = validator.validate(contract.contract_id, raw, context);
        expect(result.reasonCode, `${contract.contract_id}:${category}:${corpusCase.probe}`).toBe(corpusCase.expected_code);
        expect(result.valid).toBe(corpusCase.expected_code === 'ok');
      }
    }
  });

  it('rejects every raw structural parity case before schema and trust', () => {
    const wrapper = fixture('ananta.receiver-quality-observation.v1', false);
    for (const corpusCase of corpus.structural_parity_cases) {
      const result = validator.validate(
        'ananta.receiver-quality-observation.v1',
        corpusCase.raw_document,
        contextFor(
          'ananta.receiver-quality-observation.v1',
          wrapper.document,
          wrapper.validationContext,
          true,
        ),
      );
      expect(result.valid, corpusCase.id).toBe(false);
      expect(result.reasonCode, corpusCase.id).toBe(corpusCase.expected_code);
    }
  });

  it.each([
    ['prototype pollution', '{"__proto__":{"polluted":true}}', 'contract_dangerous_property'],
    ['non-NFC value', JSON.stringify({ value: 'e\u0301' }), 'contract_unicode_normalization_invalid'],
    ['unsafe integer', JSON.stringify({ epoch: 9_007_199_254_740_992 }), 'contract_integer_range_exceeded'],
    ['wrong epoch type', JSON.stringify({ ...fixture('ananta.receiver-quality-observation.v1', false).document, route_epoch: '9' }), 'contract_schema_invalid'],
  ])('rejects %s before a document can reach storage or UI binding', (_name, raw, reason) => {
    const wrapper = fixture('ananta.receiver-quality-observation.v1', false);
    const result = validator.validate(
      'ananta.receiver-quality-observation.v1',
      raw,
      contextFor('ananta.receiver-quality-observation.v1', wrapper.document, wrapper.validationContext, true),
    );
    expect(result.valid).toBe(false);
    expect(result.reasonCode).toBe(reason);
  });

  it('returns a frozen, contract-id discriminated document only after all gates pass', () => {
    const wrapper = fixture('ananta.webrtc.fanout-route-intent.v1', false);
    const result = validator.validate(
      'ananta.webrtc.fanout-route-intent.v1',
      JSON.stringify(wrapper.document),
      contextFor('ananta.webrtc.fanout-route-intent.v1', wrapper.document, wrapper.validationContext, true),
    );
    expect(result.valid).toBe(true);
    if (!result.valid) return;
    expect(result.contract.contractId).toBe('ananta.webrtc.fanout-route-intent.v1');
    expect(Object.isFrozen(result.contract.document)).toBe(true);
  });
});

function fixture(contractId: SfuBroadcastContractId, maximum: boolean): FixtureWrapper {
  const source = (maximum ? MAXIMUM_FIXTURES : BASE_FIXTURES)[contractId];
  const raw = maximum ? materializeFixture(contractId, source) : source;
  const wrapper = record(raw);
  const document = contractId === 'sfu_node_observation.v1' ? record(wrapper['document']) : record(wrapper['instance']);
  const validationContext = contractId === 'sfu_node_observation.v1' ? record(wrapper['context']) : record(wrapper['validation_context']);
  return { document, validationContext };
}

function materializeFixture(contractId: SfuBroadcastContractId, source: unknown): unknown {
  const descriptor = record(source);
  if (!('base_fixture' in descriptor)) return source;
  const wrapper = clone(record(BASE_FIXTURES[contractId]));
  applyFixtureMutations(wrapper, descriptor['mutations'], 'instance');
  applyFixtureMutations(wrapper, descriptor['context_mutations'], 'validation_context');
  return wrapper;
}

function applyFixtureMutations(
  wrapper: SfuBroadcastJsonObject,
  source: unknown,
  defaultTarget: 'instance' | 'validation_context',
): void {
  if (source === undefined) return;
  for (const rawMutation of array(source)) {
    const mutation = record(rawMutation);
    if (mutation['operation'] !== 'replace') throw new Error('fixture_mutation_operation_unsupported');
    const pointerValue = string(mutation['json_pointer']);
    const explicitTarget = mutation['target'];
    const target = typeof explicitTarget === 'string'
      ? record(wrapper[explicitTarget])
      : pointerValue.startsWith('/instance/') || pointerValue.startsWith('/validation_context/')
        ? wrapper
        : record(wrapper[defaultTarget]);
    replaceFixturePointer(target, pointerValue, mutation['value']);
  }
}

function replaceFixturePointer(root: SfuBroadcastJsonObject, path: string, value: unknown): void {
  const segments = path.slice(1).split('/').map(segment => segment.replace(/~1/g, '/').replace(/~0/g, '~'));
  let cursor: unknown = root;
  for (const segment of segments.slice(0, -1)) {
    if (!cursor || typeof cursor !== 'object') {
      throw new Error(`fixture_mutation_pointer_missing:${path}:${segment}`);
    }
    cursor = Array.isArray(cursor)
      ? cursor[Number(segment)]
      : record(cursor)[segment];
  }
  const key = segments.at(-1);
  if (key === undefined) throw new Error('fixture_mutation_pointer_invalid');
  if (!cursor || typeof cursor !== 'object') {
    throw new Error(`fixture_mutation_pointer_missing:${path}:${key}`);
  }
  if (Array.isArray(cursor)) cursor[Number(key)] = value;
  else (record(cursor) as Record<string, unknown>)[key] = value;
}

function contextFor(
  contractId: SfuBroadcastContractId,
  document: SfuBroadcastJsonObject,
  fixtureContext: Readonly<Record<string, unknown>>,
  trusted: boolean,
): SfuBroadcastValidationContext {
  const definition = SFU_BROADCAST_CONTRACT_DEFINITIONS[contractId];
  const expectedScope: Record<string, SfuBroadcastJsonValue> = {};
  const latestEpochs: Record<string, number> = {};
  for (const [name, path] of Object.entries(definition.scopePointers)) expectedScope[name] = jsonValue(pointer(document, path));
  for (const [name, path] of Object.entries(definition.epochPointers)) latestEpochs[name] = number(pointer(document, path));
  const nowValue = fixtureContext['now_ms'] ?? fixtureContext['now'] ?? fixtureContext['received_at'];
  return {
    nowEpochMs: typeof nowValue === 'number' ? nowValue : Date.parse(string(nowValue)),
    expectedScope,
    latestEpochs,
    acceptedSequences: new Set<number>(),
    lastSequence: null,
    metadata: fixtureContext,
    verifyReceiverGroupMemberDigest: request => {
      const keys = fixtureContext['test_only_hmac_keys_hex'];
      if (!keys || typeof keys !== 'object' || Array.isArray(keys)) return false;
      const keyHex = (keys as Readonly<Record<string, unknown>>)[request.keyRef];
      if (typeof keyHex !== 'string') return false;
      const expected = createHmac('sha256', Buffer.from(keyHex, 'hex'))
        .update(Buffer.concat([
          Buffer.from('ananta.webrtc.receiver-group.member-digest.v1'),
          Buffer.from([0]),
          Buffer.from(canonical(request.digestInput)),
        ]))
        .digest('hex');
      return expected === request.supplied;
    },
    verifySignature: () => trusted,
  };
}

function independentlyRepairReceiverDigests(
  source: SfuBroadcastJsonObject,
  metadata: Readonly<Record<string, unknown>>,
): SfuBroadcastJsonObject {
  const document = clone(source);
  const publications = record(document['publications']);
  const digests = array(publications['member_digests']).map(rawDigest => {
    const digest = record(rawDigest);
    const publicationRef = string(digest['publication_ref']);
    const keyRef = string(digest['key_ref']);
    const input = receiverDigestInput(document, metadata, publicationRef);
    const value = createHmac('sha256', Buffer.from(string(record(metadata['test_only_hmac_keys_hex'])[keyRef]), 'hex'))
      .update(Buffer.concat([Buffer.from('ananta.webrtc.receiver-group.member-digest.v1'), Buffer.from([0]), Buffer.from(canonical(input))]))
      .digest('hex');
    return { ...digest, value };
  });
  return { ...document, publications: { ...publications, member_digests: digests } };
}

function receiverDigestInput(document: SfuBroadcastJsonObject, metadata: Readonly<Record<string, unknown>>, publicationRef: string): SfuBroadcastJsonObject {
  const publications = record(document['publications']);
  const scope = record(document['scope']);
  const epochs = record(document['epochs']);
  const policy = record(document['policy']);
  const members = array(record(metadata['resolved_audience'])['members']);
  const bindings = members.map(rawMember => {
    const member = record(rawMember);
    const grant = record(array(member['publication_grants']).find(raw => record(raw)['publication_ref'] === publicationRef));
    return { grant_ref: string(grant['grant_ref']), member_ref: string(member['member_ref']), subscription_ref: string(grant['subscription_ref']) };
  }).sort((left, right) => Buffer.compare(Buffer.from(left.member_ref), Buffer.from(right.member_ref)) || Buffer.compare(Buffer.from(left.grant_ref), Buffer.from(right.grant_ref)) || Buffer.compare(Buffer.from(left.subscription_ref), Buffer.from(right.subscription_ref)));
  return {
    allowed_publication_refs: [string(publications['primary_publication_ref']), ...array(publications['shared_publication_refs']).map(string)].sort((left, right) => Buffer.compare(Buffer.from(left), Buffer.from(right))),
    audience_epoch: jsonValue(epochs['audience_epoch']), audience_snapshot_ref: jsonValue(document['audience_snapshot_ref']),
    consent_epoch: jsonValue(epochs['consent_epoch']), consent_scope_ref: jsonValue(scope['consent_scope_ref']),
    group_id: jsonValue(document['group_id']), key_epoch: jsonValue(epochs['key_epoch']), member_bindings: bindings,
    membership_epoch: jsonValue(epochs['membership_epoch']), policy_ref: jsonValue(policy['policy_ref']), policy_version: jsonValue(policy['version']),
    privacy_scope: jsonValue(scope['privacy_scope']), publication_ref: publicationRef, publication_scope_ref: jsonValue(scope['publication_scope_ref']),
    room_ref: jsonValue(scope['room_ref']), tenant_ref: jsonValue(scope['tenant_ref']),
  };
}

function pointer(document: unknown, path: string): unknown {
  let value = document;
  for (const raw of path.slice(1).split('/')) value = record(value)[raw.replace(/~1/g, '/').replace(/~0/g, '~')];
  return value;
}

function canonical(value: SfuBroadcastJsonValue): string {
  if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
}

function clone(value: SfuBroadcastJsonObject): SfuBroadcastJsonObject {
  return JSON.parse(JSON.stringify(value)) as SfuBroadcastJsonObject;
}

function record(value: unknown): SfuBroadcastJsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('fixture_object_required');
  return value as SfuBroadcastJsonObject;
}

function array(value: unknown): readonly SfuBroadcastJsonValue[] {
  if (!Array.isArray(value)) throw new Error('fixture_array_required');
  return value;
}

function string(value: unknown): string {
  if (typeof value !== 'string') throw new Error('fixture_string_required');
  return value;
}

function number(value: unknown): number {
  if (!Number.isSafeInteger(value)) throw new Error('fixture_integer_required');
  return value;
}

function jsonValue(value: unknown): SfuBroadcastJsonValue {
  if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' || Array.isArray(value) || (typeof value === 'object' && value !== null)) return value as SfuBroadcastJsonValue;
  throw new Error('fixture_json_value_required');
}
