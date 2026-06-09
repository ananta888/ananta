/**
 * T08: Type guards and payload validators for Pair-Dev View-Sync.
 *
 * Every remote payload is validated by exactly one of these guards
 * before it touches any Angular state. Guards return boolean; if
 * false the payload is dropped silently and an audit event is
 * emitted by the caller. No throw, no recovery — invalid means
 * invalid.
 *
 * Path-whitelisting prevents prototype pollution / RCE via the
 * `ops` field. Only the documented SharedViewState paths are
 * accepted; everything else is dropped.
 */
import {
  ALL_PERMISSIONS,
  Annotation,
  ControlMessage,
  CursorDelta,
  DeltaKind,
  DeltaOp,
  PAIR_VIEW_SYNC_VERSION,
  PermissionKey,
  ScrollDelta,
  SharedViewState,
  ViewStateDelta,
} from './pair-view-sync.types';

// ── Path whitelist ────────────────────────────────────────────────────

/**
 * Whitelisted top-level paths in SharedViewState. Anything else
 * is rejected. The list is intentionally short — the delta
 * protocol is meant to carry only what `default_sync_fields`
 * advertises.
 */
const ALLOWED_DELTA_PATHS: ReadonlySet<string> = new Set<string>([
  'route',
  'queryParams',
  'activeSurface',
  'activeTab',
  'activePanel',
  'activeArtifactId',
  'activeArtifactHash',
  'activeFilePath',
  'activeSymbolId',
  'scroll',
  'cursor',
  'selection',
  'zoom',
  'collapsedSections',
]);

const ALLOWED_OP_TYPES: ReadonlySet<DeltaOp['op']> = new Set<DeltaOp['op']>([
  'set',
  'unset',
  'append',
  'remove',
]);

const ALLOWED_DELTA_KINDS: ReadonlySet<DeltaKind> = new Set<DeltaKind>([
  'snapshot',
  'delta',
  'cursor',
  'selection',
  'scroll',
  'control',
]);

const ALLOWED_PERMISSION_KEYS: ReadonlySet<PermissionKey> = new Set<PermissionKey>(ALL_PERMISSIONS);

// ── Primitive guards ──────────────────────────────────────────────────

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === 'object' && x !== null && !Array.isArray(x);
}

function isString(x: unknown): x is string {
  return typeof x === 'string';
}

function isFiniteNumber(x: unknown): x is number {
  return typeof x === 'number' && Number.isFinite(x);
}

function isNonNegativeInt(x: unknown): x is number {
  return isFiniteNumber(x) && Number.isInteger(x) && x >= 0;
}

function isHash(x: unknown): x is string {
  // viewHash / baseHash / newHash are SHA-256 hex (64 chars) or
  // a short stable string. We accept any non-empty string of
  // length <= 128 to keep the validator light; the producing
  // side is responsible for the actual hash format.
  return isString(x) && x.length > 0 && x.length <= 128;
}

function isSessionId(x: unknown): x is string {
  return isString(x) && x.length > 0 && x.length <= 128;
}

function isUserId(x: unknown): x is string {
  return isString(x) && x.length > 0 && x.length <= 128;
}

function isEnumValue<T extends string>(
  x: unknown,
  allowed: ReadonlySet<T>,
): x is T {
  return isString(x) && allowed.has(x as T);
}

// ── Public guards ─────────────────────────────────────────────────────

export function isPermissionKey(x: unknown): x is PermissionKey {
  return isEnumValue(x, ALLOWED_PERMISSION_KEYS);
}

export function isPermissionSet(x: unknown): x is Readonly<Record<PermissionKey, boolean>> {
  if (!isObject(x)) return false;
  for (const k of Object.keys(x)) {
    if (!isPermissionKey(k)) return false;
    if (typeof (x as Record<string, unknown>)[k] !== 'boolean') return false;
  }
  return true;
}

export function isScrollPos(x: unknown): x is { x: number; y: number } {
  if (!isObject(x)) return false;
  return isFiniteNumber(x['x']) && isFiniteNumber(x['y']);
}

export function isCursorPos(x: unknown): x is { line: number | null; column: number | null } {
  if (!isObject(x)) return false;
  const line = x['line'];
  const col = x['column'];
  return (
    (line === null || isNonNegativeInt(line)) &&
    (col === null || isNonNegativeInt(col))
  );
}

export function isSelectionPos(x: unknown): x is { start: number | null; end: number | null } {
  if (!isObject(x)) return false;
  const start = x['start'];
  const end = x['end'];
  if (start !== null && !isNonNegativeInt(start)) return false;
  if (end !== null && !isNonNegativeInt(end)) return false;
  if (start !== null && end !== null && start > end) return false;
  return true;
}

export function isActiveSurface(x: unknown): x is SharedViewState['activeSurface'] {
  return (
    x === 'chat' ||
    x === 'codecompass' ||
    x === 'artifact' ||
    x === 'terminal' ||
    x === 'settings' ||
    x === 'dashboard' ||
    x === 'pair' ||
    x === 'unknown'
  );
}

export function isDeltaOp(x: unknown): x is DeltaOp {
  if (!isObject(x)) return false;
  if (!isEnumValue(x['op'], ALLOWED_OP_TYPES)) return false;
  const path = x['path'];
  if (!isString(path) || !ALLOWED_DELTA_PATHS.has(path)) return false;
  if (x['op'] === 'unset') return true;
  // 'set' / 'append' / 'remove' all carry a value
  return 'value' in x;
}

export function isViewStateDelta(x: unknown): x is ViewStateDelta {
  if (!isObject(x)) return false;
  if (x['version'] !== PAIR_VIEW_SYNC_VERSION) return false;
  if (!isSessionId(x['sessionId'])) return false;
  if (!isUserId(x['senderUserId'])) return false;
  if (!isNonNegativeInt(x['seq'])) return false;
  if (!isHash(x['baseHash'])) return false;
  if (!isHash(x['newHash'])) return false;
  if (!isEnumValue(x['kind'], ALLOWED_DELTA_KINDS)) return false;
  if (!isNonNegativeInt(x['createdAt'])) return false;
  const ops = x['ops'];
  if (!Array.isArray(ops)) return false;
  if (ops.length > 64) return false; // cap delta size
  for (const op of ops) {
    if (!isDeltaOp(op)) return false;
  }
  return true;
}

export function isCursorDelta(x: unknown): x is CursorDelta {
  if (!isObject(x)) return false;
  if (!isSessionId(x['sessionId'])) return false;
  if (!isUserId(x['senderUserId'])) return false;
  if (!isNonNegativeInt(x['createdAt'])) return false;
  return isCursorPos(x['cursor']);
}

export function isScrollDelta(x: unknown): x is ScrollDelta {
  if (!isObject(x)) return false;
  if (!isSessionId(x['sessionId'])) return false;
  if (!isUserId(x['senderUserId'])) return false;
  if (!isNonNegativeInt(x['createdAt'])) return false;
  return isScrollPos(x['scroll']);
}

export function isControlMessage(x: unknown): x is ControlMessage {
  if (!isObject(x)) return false;
  if (!isSessionId(x['sessionId'])) return false;
  if (!isUserId(x['senderUserId'])) return false;
  if (!isNonNegativeInt(x['createdAt'])) return false;
  const k = x['kind'];
  if (
    k !== 'request' &&
    k !== 'grant' &&
    k !== 'revoke' &&
    k !== 'request_follow' &&
    k !== 'request_unfollow'
  ) return false;
  const grant = x['grantToken'];
  if (grant !== null && !(isString(grant) && grant.length > 0 && grant.length <= 256)) return false;
  return true;
}

export function isAnnotation(x: unknown): x is Annotation {
  if (!isObject(x)) return false;
  if (!isSessionId(x['sessionId'])) return false;
  if (!isUserId(x['senderUserId'])) return false;
  if (!isNonNegativeInt(x['createdAt'])) return false;
  if (!isString(x['targetPath'])) return false;
  const body = x['body'];
  if (!isString(body)) return false;
  if (body.length > 4096) return false; // 4 KB cap
  return true;
}

/**
 * Validate a SharedViewState snapshot. Used when receiving a
 * `kind: 'snapshot'` message. The state is large; we only check
 * shape, not equality.
 */
export function isSharedViewState(x: unknown): x is SharedViewState {
  if (!isObject(x)) return false;
  if (x['version'] !== PAIR_VIEW_SYNC_VERSION) return false;
  if (!isSessionId(x['sessionId'])) return false;
  if (!isUserId(x['ownerUserId'])) return false;
  if (!isNonNegativeInt(x['seq'])) return false;
  if (!isString(x['route']) || x['route'].length > 2048) return false;
  const qp = x['queryParams'];
  if (!isObject(qp)) return false;
  for (const v of Object.values(qp)) {
    if (!isString(v)) return false;
  }
  if (!isActiveSurface(x['activeSurface'])) return false;
  if (!isString(x['activeTab']) || x['activeTab'].length > 128) return false;
  if (!isString(x['activePanel']) || x['activePanel'].length > 128) return false;
  if (x['activeArtifactId'] !== null && !isString(x['activeArtifactId'])) return false;
  if (x['activeArtifactHash'] !== null && !isString(x['activeArtifactHash'])) return false;
  if (x['activeFilePath'] !== null && !isString(x['activeFilePath'])) return false;
  if (x['activeSymbolId'] !== null && !isString(x['activeSymbolId'])) return false;
  if (!isScrollPos(x['scroll'])) return false;
  if (!isCursorPos(x['cursor'])) return false;
  if (!isSelectionPos(x['selection'])) return false;
  if (x['zoom'] !== null && !isFiniteNumber(x['zoom'])) return false;
  if (!Array.isArray(x['collapsedSections'])) return false;
  if (x['collapsedSections'].length > 256) return false;
  for (const s of x['collapsedSections']) {
    if (!isString(s) || s.length > 128) return false;
  }
  if (!isHash(x['viewHash'])) return false;
  if (!isNonNegativeInt(x['createdAt'])) return false;
  return true;
}

/** Maximum body size for the encrypted_payload (matches backend). */
export const MAX_ENCRYPTED_PAYLOAD_BYTES = 256 * 1024;

/** Hard cap on any incoming payload (matches DataChannel limit). */
export const MAX_DATACHANNEL_BYTES = 64 * 1024;

/** Minimum-size warnings; payloads above these are flagged. */
export const SNAPSHOT_WARN_BYTES = 32 * 1024;
