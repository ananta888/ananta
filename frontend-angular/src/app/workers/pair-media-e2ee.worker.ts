import {
  PairMediaE2eeFrameCipher,
  PairMediaE2eeFrameContext,
} from '../services/pair-media-e2ee-frame-codec';

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
let poisoned = false;

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
      try {
        const key = state.key;
        const cipher = state.cipher;
        const frameType = frame.type ?? (cipher.context.kind === 'audio' ? 'audio' : 'delta');
        const transformed = state.operation === 'encrypt'
          ? await cipher.seal(key, copyBuffer(frame.data), frameType)
          : await cipher.open(key, copyBuffer(frame.data), frameType);
        // Another slot can poison the worker while WebCrypto is in flight.
        // Recheck the global and exact local generation before publication.
        if (poisoned || state.failed || state.key !== key || state.cipher !== cipher) return;
        frame.data = transformed;
        controller.enqueue(frame);
      } catch (error) {
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
      // One-shot before the first mutation. Even an unexpected commit error
      // cannot make the worker reusable with reset counters under this key.
      installedOnce = true;
      for (const install of prepared) commitInstall(install);
      workerScope.postMessage({
        version: 1,
        type: 'keys-installed',
        sessionId: message.sessionId,
        transformIds: message.entries.map(entry => entry.transformId),
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
