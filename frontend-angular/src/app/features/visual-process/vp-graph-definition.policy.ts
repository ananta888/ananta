import type {
  VpEdge,
  VpGraph,
  VpStep,
} from './visual-process-api.service';

export interface VpGraphDefinitionIssue {
  readonly path: string;
  readonly message: string;
}

export type VpGraphDefinitionResult =
  | {
    readonly ok: true;
    readonly value: VpGraph;
    readonly issues: readonly [];
  }
  | {
    readonly ok: false;
    readonly issues: readonly VpGraphDefinitionIssue[];
  };

export interface VpGraphDefinitionPolicy {
  readonly path?: string;
  readonly reject_runtime_payload?: boolean;
}

/**
 * Runtime-decodes the shared Visual Process definition boundary.
 *
 * API generic types do not validate JSON responses. This policy therefore
 * checks the complete graph shape before a preset can enter editor state and,
 * by default, rejects state owned by the Hub runtime/trace projections.
 */
export function validateVpGraphDefinition(
  value: unknown,
  policy: VpGraphDefinitionPolicy = {},
): VpGraphDefinitionResult {
  const path = policy.path ?? '/graph';
  const shapeIssue = validateGraphShape(value, path);
  if (shapeIssue) return failure(shapeIssue);

  if (policy.reject_runtime_payload !== false) {
    const runtimePath = findNonNullRuntimePayload(
      value,
      path,
      new WeakSet<object>(),
    );
    if (runtimePath) {
      return failure(issue(
        runtimePath,
        'Graph definitions must not contain runtime, trace, telemetry, execution, or token-usage payloads.',
      ));
    }
  }
  return success(value as VpGraph);
}

function validateGraphShape(
  value: unknown,
  path: string,
): VpGraphDefinitionIssue | undefined {
  if (!isRecord(value)) {
    return issue(path, 'The graph must be an object.');
  }
  if (!isStringField(value, 'id')
    || !isStringField(value, 'name')
    || !isStringField(value, 'description')
    || !isStringField(value, 'version')
    || !isStringArray(value['tags'])) {
    return issue(path, 'The graph identity and tag fields are malformed.');
  }
  if (!Array.isArray(value['steps'])) {
    return issue(`${path}/steps`, 'Graph steps must be an array.');
  }
  if (!Array.isArray(value['edges'])) {
    return issue(`${path}/edges`, 'Graph edges must be an array.');
  }
  if (!isOptionalRecord(value['metadata']) || !isOptionalRecord(value['extensions'])) {
    return issue(path, 'Graph metadata and extensions must be objects when present.');
  }
  if (!isOptionalString(value['graph_schema_version'])
    || !isOptionalString(value['node_registry_version'])
    || !isOptionalString(value['base_graph_hash'])
    || !isOptionalNonNegativeInteger(value['definition_revision'])) {
    return issue(path, 'The optional graph definition fields are malformed.');
  }

  for (const [index, step] of value['steps'].entries()) {
    if (!isVpStepShape(step)) {
      return issue(
        `${path}/steps/${index}`,
        'Every graph step must have the required VpStep definition shape.',
      );
    }
  }
  for (const [index, edge] of value['edges'].entries()) {
    if (!isVpEdgeShape(edge)) {
      const edgePath = isRecord(edge)
        && isRecord(edge['condition'])
        && edge['condition']['loop_policy'] !== undefined
        && edge['condition']['loop_policy'] !== null
        ? `${path}/edges/${index}/condition/loop_policy`
        : `${path}/edges/${index}`;
      return issue(
        edgePath,
        'Every graph edge must have the required VpEdge definition shape.',
      );
    }
  }
  return undefined;
}

function isVpStepShape(value: unknown): value is VpStep {
  if (!isRecord(value)
    || !isStringField(value, 'id')
    || !isStringField(value, 'label')
    || !isStringField(value, 'kind')
    || !isOptionalString(value['role'])
    || !isOptionalString(value['agent_skill_profile_id'])
    || !isStringArray(value['policy_hints'])
    || typeof value['gate'] !== 'boolean'
    || !isOptionalRecord(value['metadata'])) {
    return false;
  }
  const io = value['io'];
  const position = value['position'];
  return isRecord(io)
    && Array.isArray(io['inputs'])
    && io['inputs'].every(isArtifactRefShape)
    && Array.isArray(io['outputs'])
    && io['outputs'].every(isArtifactRefShape)
    && isRecord(position)
    && isFiniteNumber(position['x'])
    && isFiniteNumber(position['y']);
}

function isArtifactRefShape(value: unknown): boolean {
  return isRecord(value)
    && isStringField(value, 'name')
    && isStringField(value, 'kind')
    && typeof value['required'] === 'boolean'
    && isOptionalString(value['description']);
}

function isVpEdgeShape(value: unknown): value is VpEdge {
  if (!isRecord(value)
    || !isStringField(value, 'id')
    || !isStringField(value, 'source')
    || !isStringField(value, 'target')
    || !isOptionalString(value['label'])
    || !isOptionalRecord(value['metadata'])) {
    return false;
  }
  const condition = value['condition'];
  if (!isRecord(condition)
    || !isStringField(condition, 'kind')
    || !isOptionalString(condition['expression'])
    || !isOptionalString(condition['output_name'])) {
    return false;
  }
  const loopPolicy = condition['loop_policy'];
  return loopPolicy === undefined
    || loopPolicy === null
    || (isRecord(loopPolicy)
      && isStringField(loopPolicy, 'kind')
      && isNonNegativeInteger(loopPolicy['max_iterations'])
      && isOptionalString(loopPolicy['condition'])
      && isOptionalString(loopPolicy['break_on_output']));
}

const RUNTIME_PAYLOAD_FIELDS = new Set([
  'run_state',
  'run_status',
  'runtime',
  'runtime_overlay',
  'runtime_state',
  'runtime_status',
  'run_id',
  'workflow_run_id',
  'current_step_id',
  'current_step_ids',
  'execution',
  'execution_result',
  'execution_state',
  'execution_status',
  'trace',
  'trace_events',
  'trace_id',
  'trace_ref',
  'telemetry',
  'token_usage',
]);

function findNonNullRuntimePayload(
  value: unknown,
  path: string,
  seen: WeakSet<object>,
): string | undefined {
  if (value === null || typeof value !== 'object') return undefined;
  if (seen.has(value)) return undefined;
  seen.add(value);

  if (Array.isArray(value)) {
    for (const [index, item] of value.entries()) {
      const found = findNonNullRuntimePayload(item, `${path}/${index}`, seen);
      if (found) return found;
    }
    return undefined;
  }
  for (const [field, fieldValue] of Object.entries(value)) {
    const fieldPath = `${path}/${field}`;
    if (RUNTIME_PAYLOAD_FIELDS.has(field)
      && fieldValue !== null
      && fieldValue !== undefined) {
      return fieldPath;
    }
    const found = findNonNullRuntimePayload(fieldValue, fieldPath, seen);
    if (found) return found;
  }
  return undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isStringField(
  value: Readonly<Record<string, unknown>>,
  field: string,
): boolean {
  return typeof value[field] === 'string';
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string');
}

function isOptionalString(value: unknown): boolean {
  return value === undefined || value === null || typeof value === 'string';
}

function isOptionalRecord(value: unknown): boolean {
  return value === undefined || value === null || isRecord(value);
}

function isOptionalNonNegativeInteger(value: unknown): boolean {
  return value === undefined || isNonNegativeInteger(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function issue(path: string, message: string): VpGraphDefinitionIssue {
  return { path, message };
}

function success(value: VpGraph): VpGraphDefinitionResult {
  return { ok: true, value, issues: [] };
}

function failure(issueValue: VpGraphDefinitionIssue): VpGraphDefinitionResult {
  return { ok: false, issues: [issueValue] };
}
