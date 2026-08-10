import {
  PairMediaE2eeFrameCipher,
  PairMediaE2eeFrameContext,
} from '../services/pair-media-e2ee-frame-codec';
import {
  PUBLIC_PAIR_MEDIA_SLOTS,
  PublicPairMediaSlot,
} from '../services/public-pair-media-security-contract';

type TransformOperation = 'encrypt' | 'decrypt';

interface EncodedFrame {
  data: ArrayBuffer;
  readonly type?: string;
}

interface TransformOptions {
  readonly version: 1;
  readonly transformId: string;
  readonly operation: TransformOperation;
}

interface TransformState extends TransformOptions {
  key: CryptoKey | null;
  cipher: PairMediaE2eeFrameCipher | null;
  failed: boolean;
}

interface OutboundPublicationGate {
  readonly revision: number;
  readonly enabled: boolean;
  readonly slots: readonly PublicPairMediaSlot[];
  readonly expiresAtMs: number;
}

interface InstallEntry {
  readonly transformId: string;
  readonly context: PairMediaE2eeFrameContext;
  readonly key: CryptoKey;
}

interface PreparedInstall {
  readonly state: TransformState;
  readonly key: CryptoKey;
  readonly cipher: PairMediaE2eeFrameCipher;
}

interface WorkerTransformEvent {
  readonly transformer: {
    readonly options: unknown;
    readonly readable: ReadableStream<EncodedFrame>;
    readonly writable: WritableStream<EncodedFrame>;
  };
}

interface EncodedTransformWorkerScope {
  onrtctransform: ((event: WorkerTransformEvent) => void) | null;
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  postMessage(message: unknown): void;
}

const workerScope = globalThis as unknown as EncodedTransformWorkerScope;
const transforms = new Map<string, TransformState>();
let installedOnce = false;
let installedSessionId = '';
let installedContractExpiresAtMs = 0;
let poisoned = false;
let outboundPublicationGate: OutboundPublicationGate | null = null;

workerScope.onrtctransform = event => {
  let options: TransformOptions;
  try {
    if (poisoned) throw new Error('media_e2ee_worker_poisoned');
    options = parseTransformOptions(event.transformer.options);
    if (transforms.has(options.transformId)) throw new Error('media_e2ee_transform_duplicate');
  } catch (error) {
    postFatal('', reason(error, 'media_e2ee_transform_options_invalid'));
    return;
  }
  const state: TransformState = { ...options, key: null, cipher: null, failed: false };
  transforms.set(options.transformId, state);
  const transform = new TransformStream<EncodedFrame, EncodedFrame>({
    transform: async (frame, controller) => {
      // DROP-first: not one encoded byte leaves the transform before a
      // direction/slot/connection key is installed and acknowledged.
      if (!state.key || !state.cipher || state.failed) return;
      // Codec DTX/empty callbacks contain no media byte and cannot carry the
      // authenticated clear codec prefix required by frame-format v2.
      if (frame.data.byteLength === 0) return;
      const publicationGate = outboundPublicationGate;
      const publicationSlot = state.cipher.context.slot;
      if (
        state.operation === 'encrypt'
        && !publicationAllowed(publicationGate, publicationSlot, Date.now())
      ) return;
      try {
        const key = state.key;
        const cipher = state.cipher;
        const transformed = state.operation === 'encrypt'
          ? await cipher.seal(key, copyBuffer(frame.data))
          : await cipher.open(key, copyBuffer(frame.data));
        // Another slot can poison the worker while WebCrypto is in flight.
        // Recheck the global and exact local generation before publication.
        if (poisoned || state.failed || state.key !== key || state.cipher !== cipher) return;
        // Revocation, expiry or a higher publication revision may occur while
        // WebCrypto is in flight. Never publish a frame admitted by a previous
        // gate, even if a later gate enables the same slot again.
        if (
          state.operation === 'encrypt'
          && (
            outboundPublicationGate !== publicationGate
            || !publicationAllowed(publicationGate, publicationSlot, Date.now())
          )
        ) return;
        frame.data = transformed;
        controller.enqueue(frame);
      } catch (error) {
        // A gate transition can race either a successful or rejected
        // WebCrypto operation. The old frame is no longer publishable, and a
        // crypto result from that obsolete admission must not poison a later
        // regrant. Persistent crypto failures surface on its next live frame.
        if (
          state.operation === 'encrypt'
          && (
            outboundPublicationGate !== publicationGate
            || !publicationAllowed(publicationGate, publicationSlot, Date.now())
          )
        ) return;
        state.failed = true;
        postFatal(state.transformId, reason(error, 'media_e2ee_transform_failed'));
        throw error;
      }
    },
  });
  event.transformer.readable
    .pipeThrough(transform)
    .pipeTo(event.transformer.writable)
    .catch(error => {
      if (state.failed) return;
      state.failed = true;
      postFatal(state.transformId, reason(error, 'media_e2ee_pipeline_failed'));
    });
  workerScope.postMessage({ version: 1, type: 'transform-ready', transformId: state.transformId });
};

workerScope.onmessage = event => {
  try {
    const message = parseWorkerMessage(event.data);
    if (message.type === 'install-keys') {
      // Validate and construct the complete six-transform key set before
      // mutating any live transform. A bad final entry cannot partially open
      // earlier DROP-first slots.
      if (installedOnce) throw new Error('media_e2ee_key_reinstall_forbidden');
      const ids = new Set(message.entries.map(entry => entry.transformId));
      if (
        ids.size !== message.entries.length
        || transforms.size !== message.entries.length
        || [...transforms.keys()].some(transformId => !ids.has(transformId))
      ) throw new Error('media_e2ee_key_set_invalid');
      const prepared = message.entries.map(entry => prepareInstall(entry));
      const contractExpiresAtMs = commonContractExpiry(prepared, message.sessionId);
      // One-shot before the first mutation. Even an unexpected commit error
      // cannot make the worker reusable with reset counters under this key.
      installedOnce = true;
      for (const install of prepared) commitInstall(install);
      installedSessionId = message.sessionId;
      installedContractExpiresAtMs = contractExpiresAtMs;
      workerScope.postMessage({
        version: 1,
        type: 'keys-installed',
        sessionId: message.sessionId,
        transformIds: message.entries.map(entry => entry.transformId),
      });
      return;
    }
    if (message.type === 'set-publication-gate') {
      if (!installedOnce || !installedSessionId || installedSessionId !== message.sessionId) {
        throw new Error('media_e2ee_publication_gate_not_keyed');
      }
      const gate = parsePublicationGate(message.gate, installedContractExpiresAtMs);
      const current = outboundPublicationGate;
      if (current && gate.revision < current.revision) {
        throw new Error('media_e2ee_publication_gate_stale');
      }
      if (current && gate.revision === current.revision && !samePublicationGate(current, gate)) {
        throw new Error('media_e2ee_publication_gate_conflict');
      }
      if (!current || gate.revision > current.revision) outboundPublicationGate = gate;
      workerScope.postMessage({
        version: 1,
        type: 'publication-gate-set',
        sessionId: message.sessionId,
        gate,
      });
      return;
    }
    if (message.type === 'clear-keys') {
      for (const state of transforms.values()) {
        state.key = null;
        state.cipher?.clear();
        state.cipher = null;
      }
      workerScope.postMessage({ version: 1, type: 'keys-cleared', sessionId: message.sessionId });
    }
  } catch (error) {
    postFatal('', reason(error, 'media_e2ee_worker_message_invalid'));
  }
};

function prepareInstall(entry: InstallEntry): PreparedInstall {
  const state = transforms.get(entry.transformId);
  if (!state || state.failed) throw new Error('media_e2ee_transform_missing');
  validateKey(entry.key);
  const cipher = new PairMediaE2eeFrameCipher(entry.context);
  if (cipher.context.senderId === cipher.context.recipientId) {
    throw new Error('media_e2ee_transform_direction_invalid');
  }
  return { state, key: entry.key, cipher };
}

function commitInstall(value: PreparedInstall): void {
  value.state.cipher?.clear();
  value.state.key = value.key;
  value.state.cipher = value.cipher;
}

function parseTransformOptions(raw: unknown): TransformOptions {
  const value = closedObject(raw, ['version', 'transformId', 'operation']);
  if (
    value['version'] !== 1
    || (value['operation'] !== 'encrypt' && value['operation'] !== 'decrypt')
    || typeof value['transformId'] !== 'string'
    || !/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(value['transformId'])
  ) throw new Error('media_e2ee_transform_options_invalid');
  return value as unknown as TransformOptions;
}

function parseWorkerMessage(raw: unknown):
  | { type: 'install-keys'; sessionId: string; entries: InstallEntry[] }
  | { type: 'set-publication-gate'; sessionId: string; gate: unknown }
  | { type: 'clear-keys'; sessionId: string } {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('media_e2ee_worker_message_invalid');
  }
  const value = raw as Record<string, unknown>;
  if (value['version'] !== 1 || typeof value['sessionId'] !== 'string') {
    throw new Error('media_e2ee_worker_message_invalid');
  }
  if (value['type'] === 'clear-keys') {
    closedObject(value, ['version', 'type', 'sessionId']);
    return { type: 'clear-keys', sessionId: value['sessionId'] };
  }
  if (value['type'] === 'set-publication-gate') {
    closedObject(value, ['version', 'type', 'sessionId', 'gate']);
    return {
      type: 'set-publication-gate',
      sessionId: value['sessionId'],
      gate: value['gate'],
    };
  }
  if (value['type'] !== 'install-keys') throw new Error('media_e2ee_worker_message_invalid');
  closedObject(value, ['version', 'type', 'sessionId', 'entries']);
  if (!Array.isArray(value['entries']) || value['entries'].length !== 6) {
    throw new Error('media_e2ee_worker_message_invalid');
  }
  const entries = value['entries'].map(item => {
    const entry = closedObject(item, ['transformId', 'context', 'key']);
    if (typeof entry['transformId'] !== 'string') throw new Error('media_e2ee_worker_message_invalid');
    return entry as unknown as InstallEntry;
  });
  return { type: 'install-keys', sessionId: value['sessionId'], entries };
}

function parsePublicationGate(raw: unknown, contractExpiresAtMs: number): OutboundPublicationGate {
  const value = closedObject(raw, ['revision', 'enabled', 'slots', 'expiresAtMs']);
  if (
    !Number.isSafeInteger(value['revision'])
    || (value['revision'] as number) < 1
    || typeof value['enabled'] !== 'boolean'
    || !Number.isSafeInteger(value['expiresAtMs'])
    || (value['expiresAtMs'] as number) < 0
    || (value['expiresAtMs'] as number) > contractExpiresAtMs
    || !validPublicationSlots(value['slots'])
  ) throw new Error('media_e2ee_publication_gate_invalid');
  return Object.freeze({
    revision: value['revision'] as number,
    enabled: value['enabled'] as boolean,
    slots: Object.freeze([...(value['slots'] as PublicPairMediaSlot[])]),
    expiresAtMs: value['expiresAtMs'] as number,
  });
}

function validPublicationSlots(raw: unknown): raw is PublicPairMediaSlot[] {
  if (!Array.isArray(raw) || raw.length > PUBLIC_PAIR_MEDIA_SLOTS.length) return false;
  const allowed = new Set<string>(PUBLIC_PAIR_MEDIA_SLOTS.map(value => value.slot));
  return new Set(raw).size === raw.length
    && raw.every(value => typeof value === 'string' && allowed.has(value));
}

function samePublicationGate(
  left: Readonly<OutboundPublicationGate>,
  right: Readonly<OutboundPublicationGate>,
): boolean {
  return left.revision === right.revision
    && left.enabled === right.enabled
    && left.expiresAtMs === right.expiresAtMs
    && left.slots.length === right.slots.length
    && left.slots.every((slot, index) => slot === right.slots[index]);
}

function publicationAllowed(
  gate: Readonly<OutboundPublicationGate> | null,
  slot: string,
  nowMs: number,
): boolean {
  return gate?.enabled === true
    && nowMs < gate.expiresAtMs
    && gate.slots.includes(slot as PublicPairMediaSlot);
}

function commonContractExpiry(prepared: readonly PreparedInstall[], sessionId: string): number {
  const expiries = new Set(prepared.map(value => value.cipher.context.contractExpiresAtMs));
  if (
    prepared.length === 0
    || expiries.size !== 1
    || prepared.some(value => value.cipher.context.sessionId !== sessionId)
  ) throw new Error('media_e2ee_key_set_binding_mismatch');
  return prepared[0].cipher.context.contractExpiresAtMs;
}

function closedObject(raw: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('media_e2ee_worker_message_invalid');
  }
  const value = raw as Record<string, unknown>;
  const keys = Object.keys(value);
  if (keys.length !== fields.length || fields.some(field => !(field in value))) {
    throw new Error('media_e2ee_worker_message_invalid');
  }
  return value;
}

function validateKey(value: CryptoKey): void {
  if (
    !value
    || value.type !== 'secret'
    || value.algorithm.name !== 'AES-GCM'
    || value.extractable
    || !value.usages.includes('encrypt')
    || !value.usages.includes('decrypt')
  ) throw new Error('media_e2ee_key_invalid');
}

function copyBuffer(value: ArrayBuffer): ArrayBuffer {
  if (Object.prototype.toString.call(value) !== '[object ArrayBuffer]') {
    throw new Error('media_e2ee_frame_size_invalid');
  }
  return value.slice(0);
}

function postFatal(transformId: string, reasonCode: string): void {
  if (poisoned) return;
  poisoned = true;
  // Worker-global fail boundary: one authenticated-frame failure closes all
  // slots synchronously, before the main thread receives the fatal event.
  for (const state of transforms.values()) {
    state.failed = true;
    state.key = null;
    state.cipher?.clear();
    state.cipher = null;
  }
  workerScope.postMessage({ version: 1, type: 'fatal', transformId, reasonCode });
}

function reason(error: unknown, fallback: string): string {
  return error instanceof Error && /^media_e2ee_[a-z0-9_]{2,96}$/.test(error.message)
    ? error.message : fallback;
}

export {};
