export const SEMANTIC_SCENE_SCHEMA = 'ananta.semantic-scene.v1' as const;
export const SEMANTIC_SCENE_MAX_BYTES = 256 * 1024;
export const SEMANTIC_SCENE_MAX_NODES = 256;
export const SEMANTIC_SCENE_MAX_DEPTH = 8;
export const SEMANTIC_SCENE_MAX_POINTS = 32;

export type SceneSource = 'heuristic' | 'user' | 'model_proposal';

export interface SceneProvenance {
  readonly source: SceneSource;
  readonly algorithm: string;
  readonly version: string;
  readonly authoritative: boolean;
}

export interface SceneObservation<T> {
  readonly value: T;
  readonly provenance: SceneProvenance;
}

export interface ScenePoint { readonly x: number; readonly y: number }
export interface SceneGeometry {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly outline: readonly ScenePoint[];
}
export interface SceneMotion { readonly dx_per_second: number; readonly dy_per_second: number }
export type SceneNodeKind = 'region' | 'text_hint' | 'object_hint' | 'cursor' | 'unknown';

export interface SemanticSceneNode {
  readonly id: string;
  readonly parent_id: string | null;
  readonly kind: SceneObservation<SceneNodeKind>;
  readonly geometry: SceneObservation<SceneGeometry>;
  readonly motion: SceneObservation<SceneMotion>;
  readonly label?: SceneObservation<string>;
  readonly confidence: SceneObservation<number>;
}

export interface SemanticScene {
  readonly schema: typeof SEMANTIC_SCENE_SCHEMA;
  readonly scene_id: string;
  readonly session_id: string;
  readonly contract_id: string;
  readonly contract_digest: string;
  readonly epoch: number;
  readonly sequence: number;
  readonly source_frame_digest: string;
  readonly coordinate_space: Readonly<{ unit: 'normalized'; origin: 'top_left'; width: 1; height: 1 }>;
  readonly timebase: Readonly<{ unit: 'milliseconds'; captured_at_ms: number; duration_ms: number }>;
  readonly provenance: SceneProvenance;
  readonly nodes: readonly SemanticSceneNode[];
  readonly security: Readonly<{
    classification: 'derived_semantic_metadata';
    raw_media_included: false;
  }>;
}

export class SemanticSceneValidationError extends Error {
  constructor(readonly reasonCode: string, message: string) {
    super(message);
    this.name = 'SemanticSceneValidationError';
  }
}

/** Validates and freezes an untrusted scene without knowing a renderer or model. */
export function parseSemanticScene(
  raw: unknown,
  nowMs?: number,
  maximumAgeMs = 60_000,
): SemanticScene {
  assertFinite(raw);
  let json: string;
  try {
    json = JSON.stringify(raw);
  } catch {
    fail('invalid_json', 'scene must be JSON serializable');
  }
  if (new TextEncoder().encode(json!).byteLength > SEMANTIC_SCENE_MAX_BYTES) {
    fail('payload_too_large', 'scene exceeds byte budget');
  }
  const scene = exactObject(raw, [
    'schema', 'scene_id', 'session_id', 'contract_id', 'contract_digest', 'epoch',
    'sequence', 'source_frame_digest', 'coordinate_space', 'timebase', 'provenance',
    'nodes', 'security',
  ], [], 'invalid_scene');
  if (scene['schema'] !== SEMANTIC_SCENE_SCHEMA) fail('invalid_schema', 'unknown scene schema');
  for (const field of ['scene_id', 'session_id', 'contract_id']) identifier(scene[field], field);
  for (const field of ['contract_digest', 'source_frame_digest']) digest(scene[field], field);
  integer(scene['epoch'], 'epoch', 1, 2_147_483_647);
  integer(scene['sequence'], 'sequence', 0, Number.MAX_SAFE_INTEGER);

  const coordinates = exactObject(scene['coordinate_space'], ['unit', 'origin', 'width', 'height'], [], 'invalid_coordinates');
  if (coordinates['unit'] !== 'normalized' || coordinates['origin'] !== 'top_left'
      || coordinates['width'] !== 1 || coordinates['height'] !== 1) {
    fail('invalid_coordinates', 'scene uses an unsupported coordinate space');
  }
  const timebase = exactObject(scene['timebase'], ['unit', 'captured_at_ms', 'duration_ms'], [], 'invalid_timebase');
  if (timebase['unit'] !== 'milliseconds') fail('invalid_timebase', 'timebase must use milliseconds');
  const capturedAtMs = integer(timebase['captured_at_ms'], 'captured_at_ms', 0, Number.MAX_SAFE_INTEGER);
  if (nowMs !== undefined && (capturedAtMs < nowMs - maximumAgeMs || capturedAtMs > nowMs)) {
    fail('stale_scene', 'scene capture time is outside the admitted live window');
  }
  integer(timebase['duration_ms'], 'duration_ms', 0, 60_000);
  provenance(scene['provenance']);
  const security = exactObject(scene['security'], ['classification', 'raw_media_included'], [], 'unknown_security_field');
  if (security['classification'] !== 'derived_semantic_metadata' || security['raw_media_included'] !== false) {
    fail('invalid_security', 'only derived metadata is permitted');
  }
  if (!Array.isArray(scene['nodes']) || scene['nodes'].length > SEMANTIC_SCENE_MAX_NODES) {
    fail('node_limit_exceeded', 'nodes must be a bounded array');
  }
  const parents = new Map<string, string | null>();
  for (const rawNode of scene['nodes']) {
    const node = validateNode(rawNode);
    if (parents.has(node.id)) fail('duplicate_node', 'node ids must be unique');
    parents.set(node.id, node.parent_id);
  }
  for (const [nodeId, parentId] of parents) {
    if (parentId !== null && !parents.has(parentId)) fail('missing_parent', `${nodeId} has a missing parent`);
    const seen = new Set<string>();
    let cursor: string | null | undefined = nodeId;
    let depth = 0;
    while (cursor !== null && cursor !== undefined) {
      if (seen.has(cursor)) fail('scene_cycle', 'scene hierarchy contains a cycle');
      seen.add(cursor);
      if (++depth > SEMANTIC_SCENE_MAX_DEPTH) fail('scene_depth_exceeded', 'scene hierarchy exceeds maximum depth');
      cursor = parents.get(cursor);
    }
  }
  return deepFreeze(structuredClone(scene)) as unknown as SemanticScene;
}

function validateNode(raw: unknown): SemanticSceneNode {
  const node = exactObject(
    raw, ['id', 'parent_id', 'kind', 'geometry', 'motion', 'confidence'], ['label'], 'invalid_node',
  );
  const id = identifier(node['id'], 'node.id');
  const parentId = node['parent_id'] === null ? null : identifier(node['parent_id'], 'node.parent_id');
  const kind = observation(node['kind']);
  if (!['region', 'text_hint', 'object_hint', 'cursor', 'unknown'].includes(String(kind['value']))) {
    fail('invalid_node_kind', 'unknown node kind');
  }
  const geometryObservation = observation(node['geometry']);
  const geometry = exactObject(
    geometryObservation['value'], ['x', 'y', 'width', 'height', 'outline'], [], 'invalid_geometry',
  );
  const x = numberIn(geometry['x'], 'x', 0, 1);
  const y = numberIn(geometry['y'], 'y', 0, 1);
  const width = numberIn(geometry['width'], 'width', 0, 1, true);
  const height = numberIn(geometry['height'], 'height', 0, 1, true);
  if (x + width > 1 || y + height > 1) fail('geometry_out_of_bounds', 'region extends beyond normalized bounds');
  if (!Array.isArray(geometry['outline']) || geometry['outline'].length > SEMANTIC_SCENE_MAX_POINTS) {
    fail('point_limit_exceeded', 'outline exceeds point budget');
  }
  for (const rawPoint of geometry['outline']) {
    const point = exactObject(rawPoint, ['x', 'y'], [], 'invalid_point');
    numberIn(point['x'], 'point.x', 0, 1);
    numberIn(point['y'], 'point.y', 0, 1);
  }
  const motion = exactObject(
    observation(node['motion'])['value'], ['dx_per_second', 'dy_per_second'], [], 'invalid_motion',
  );
  numberIn(motion['dx_per_second'], 'dx_per_second', -4, 4);
  numberIn(motion['dy_per_second'], 'dy_per_second', -4, 4);
  numberIn(observation(node['confidence'])['value'], 'confidence', 0, 1);
  if (node['label'] !== undefined) {
    const label = observation(node['label'])['value'];
    if (typeof label !== 'string' || label.length > 256) fail('invalid_label', 'label exceeds string budget');
  }
  return { ...node, id, parent_id: parentId } as unknown as SemanticSceneNode;
}

function observation(raw: unknown): Record<string, unknown> {
  const value = exactObject(raw, ['value', 'provenance'], [], 'invalid_observation');
  provenance(value['provenance']);
  return value;
}

function provenance(raw: unknown): void {
  const value = exactObject(raw, ['source', 'algorithm', 'version', 'authoritative'], [], 'invalid_provenance');
  if (!['heuristic', 'user', 'model_proposal'].includes(String(value['source']))) {
    fail('invalid_provenance', 'unknown provenance source');
  }
  for (const field of ['algorithm', 'version']) {
    if (typeof value[field] !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$/.test(value[field] as string)) {
      fail('invalid_provenance', `invalid provenance ${field}`);
    }
  }
  if (typeof value['authoritative'] !== 'boolean') fail('invalid_provenance', 'authoritative must be boolean');
  if (value['source'] === 'model_proposal' && value['authoritative'] === true) {
    fail('model_not_authoritative', 'model proposals cannot be authoritative');
  }
}

function exactObject(
  raw: unknown,
  required: readonly string[],
  optional: readonly string[],
  reason: string,
): Record<string, unknown> {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) fail(reason, 'value must be an object');
  const value = raw as Record<string, unknown>;
  const allowed = new Set([...required, ...optional]);
  const unknown = Object.keys(value).filter(key => !allowed.has(key));
  const missing = required.filter(key => !(key in value));
  if (unknown.length || missing.length) fail(reason, `unknown=${unknown.join(',')} missing=${missing.join(',')}`);
  return value;
}

function identifier(value: unknown, field: string): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$/.test(value)) {
    fail('invalid_identifier', `invalid ${field}`);
  }
  return value as string;
}

function digest(value: unknown, field: string): string {
  if (typeof value !== 'string' || !/^[0-9a-f]{64}$/.test(value)) fail('invalid_digest', `invalid ${field}`);
  return value as string;
}

function integer(value: unknown, field: string, minimum: number, maximum: number): number {
  if (!Number.isSafeInteger(value) || (value as number) < minimum || (value as number) > maximum) {
    fail('invalid_integer', `invalid ${field}`);
  }
  return value as number;
}

function numberIn(value: unknown, field: string, minimum: number, maximum: number, exclusiveMin = false): number {
  if (typeof value !== 'number' || !Number.isFinite(value)
      || (exclusiveMin ? value <= minimum : value < minimum) || value > maximum) {
    fail('invalid_number', `invalid ${field}`);
  }
  return value;
}

function assertFinite(value: unknown): void {
  if (typeof value === 'number' && !Number.isFinite(value)) fail('non_finite', 'non-finite number');
  if (Array.isArray(value)) value.forEach(assertFinite);
  else if (value !== null && typeof value === 'object') Object.values(value).forEach(assertFinite);
}

function deepFreeze<T>(value: T): Readonly<T> {
  if (value !== null && typeof value === 'object') {
    Object.values(value as Record<string, unknown>).forEach(item => deepFreeze(item));
    Object.freeze(value);
  }
  return value;
}

function fail(reason: string, message: string): never {
  throw new SemanticSceneValidationError(reason, message);
}
