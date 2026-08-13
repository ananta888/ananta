import type { VpGraph } from './visual-process-api.service';
import { validateVpGraphDefinition } from './vp-graph-definition.policy';

export const VP_CATALOG_BOUND_PRESET_METADATA_KEY =
  'ananta.caseflow.agent-preset';
export const VP_CATALOG_BOUND_PRESET_SCHEMA_V1 =
  'ananta.caseflow.agent-preset/v1';

export type VpPresetDirectLoadIssueCode =
  | 'preset_response_invalid'
  | 'preset_identity_mismatch'
  | 'catalog_application_required';

export interface VpPresetDirectLoadIssue {
  readonly code: VpPresetDirectLoadIssueCode;
  readonly path: string;
  readonly message: string;
}

export type VpPresetDirectLoadResult =
  | {
    readonly ok: true;
    readonly value: VpGraph;
    readonly issues: readonly [];
  }
  | {
    readonly ok: false;
    readonly issues: readonly VpPresetDirectLoadIssue[];
  };

/**
 * Guards the generic editor's replace-the-whole-graph preset path.
 *
 * Presets that declare required catalog slots need a domain application flow
 * that can resolve and authorize those references. The generic editor has no
 * such authority, so it must leave the current graph untouched and direct the
 * user to the owning workspace instead.
 */
export function validateVpPresetDirectLoad(
  requestedPresetId: string,
  response: unknown,
): VpPresetDirectLoadResult {
  if (!requestedPresetId || requestedPresetId.trim() !== requestedPresetId) {
    return failure(issue(
      'preset_response_invalid',
      '/requested_preset_id',
      'The requested preset ID is invalid.',
    ));
  }
  const definition = validateVpGraphDefinition(response, { path: '/preset' });
  if (!definition.ok) {
    return failure(issue(
      'preset_response_invalid',
      definition.issues[0].path,
      definition.issues[0].message,
    ));
  }
  const preset = definition.value;
  if (preset.id !== requestedPresetId) {
    return failure(issue(
      'preset_identity_mismatch',
      '/preset/id',
      'The Hub returned a different preset identity than requested.',
    ));
  }

  const metadata = preset.metadata;
  const marker = isRecord(metadata)
    ? metadata[VP_CATALOG_BOUND_PRESET_METADATA_KEY]
    : undefined;
  if (marker === undefined) return success(preset);
  if (!isRecord(marker)) {
    return failure(issue(
      'preset_response_invalid',
      `/preset/metadata/${VP_CATALOG_BOUND_PRESET_METADATA_KEY}`,
      'The catalog-bound preset marker is malformed.',
    ));
  }
  if (marker['schema'] !== VP_CATALOG_BOUND_PRESET_SCHEMA_V1) {
    return failure(issue(
      'preset_response_invalid',
      `/preset/metadata/${VP_CATALOG_BOUND_PRESET_METADATA_KEY}/schema`,
      'The catalog-bound preset marker schema is unsupported.',
    ));
  }

  const slots = marker['binding_slots'];
  if (!Array.isArray(slots)) {
    return failure(issue(
      'preset_response_invalid',
      `/preset/metadata/${VP_CATALOG_BOUND_PRESET_METADATA_KEY}/binding_slots`,
      'Catalog-bound preset slots must be an array.',
    ));
  }
  for (const [index, slot] of slots.entries()) {
    if (!isBindingSlotShape(slot)) {
      return failure(issue(
        'preset_response_invalid',
        `/preset/metadata/${VP_CATALOG_BOUND_PRESET_METADATA_KEY}/binding_slots/${index}`,
        'Every catalog-bound preset slot must declare its identity, target, resource type, access, and requirement.',
      ));
    }
  }
  if (slots.some(slot => isRecord(slot) && slot['required'] === true)) {
    return failure(issue(
      'catalog_application_required',
      `/preset/metadata/${VP_CATALOG_BOUND_PRESET_METADATA_KEY}/binding_slots`,
      'This preset requires authorized catalog bindings and must be applied by its owning workspace.',
    ));
  }
  return success(preset);
}

function isBindingSlotShape(value: unknown): value is Readonly<Record<string, unknown>> {
  return isRecord(value)
    && isNonEmptyString(value['slot'])
    && isNonEmptyString(value['step_id'])
    && isNonEmptyString(value['resource_type'])
    && isNonEmptyString(value['access'])
    && typeof value['required'] === 'boolean';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.trim() === value;
}

function issue(
  code: VpPresetDirectLoadIssueCode,
  path: string,
  message: string,
): VpPresetDirectLoadIssue {
  return { code, path, message };
}

function success(value: VpGraph): VpPresetDirectLoadResult {
  return { ok: true, value, issues: [] };
}

function failure(
  ...issues: readonly VpPresetDirectLoadIssue[]
): VpPresetDirectLoadResult {
  return { ok: false, issues };
}
