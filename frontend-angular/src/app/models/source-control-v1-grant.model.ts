/** Strict parsers for Source Control v1 grant presets, grants and mutations. */

import {
  assertSourceControlOpaqueId,
  assertSourceControlSha256,
} from './source-control-v1-api.model';
import type {
  SourceControlGrant,
  SourceControlGrantMutationResult,
  SourceControlGrantPage,
  SourceControlGrantPreset,
  SourceControlGrantPresetPage,
} from './source-control-v1-governance-types.model';
import {
  arrayValue,
  booleanValue,
  cursor,
  exactKeys,
  fail,
  integer,
  objectValue,
  safeObject,
  text,
} from './source-control-v1-governance-validation.model';

export function parseGrantPresetPage(
  value: unknown,
  path = 'grant_preset_page',
): SourceControlGrantPresetPage {
  const page = objectValue(value, path);
  exactKeys(page, ['items', 'next_cursor', 'capabilities'], path);
  return {
    items: arrayValue(page['items'], `${path}.items`).map((item, index) =>
      grantPreset(item, `${path}.items[${index}]`),
    ),
    next_cursor: cursor(page['next_cursor'], `${path}.next_cursor`),
    capabilities: safeObject(page['capabilities'], `${path}.capabilities`),
  };
}

export function parseGrantPage(
  value: unknown,
  path = 'grant_page',
): SourceControlGrantPage {
  const page = objectValue(value, path);
  exactKeys(
    page,
    ['schema', 'items', 'next_cursor', 'capabilities'],
    path,
  );
  if (page['schema'] !== 'ananta.source-control.grant-admin-list.v1') {
    fail(`${path}.schema_invalid`);
  }
  return {
    schema: 'ananta.source-control.grant-admin-list.v1',
    items: arrayValue(page['items'], `${path}.items`).map((item, index) =>
      grant(item, `${path}.items[${index}]`),
    ),
    next_cursor: cursor(page['next_cursor'], `${path}.next_cursor`),
    capabilities: safeObject(page['capabilities'], `${path}.capabilities`),
  };
}

export function parseGrantMutationResult(
  value: unknown,
  path = 'grant_mutation',
): SourceControlGrantMutationResult {
  const result = objectValue(value, path);
  exactKeys(result, ['grant', 'capabilities'], path);
  return {
    grant: grant(result['grant'], `${path}.grant`),
    capabilities: safeObject(
      result['capabilities'],
      `${path}.capabilities`,
    ),
  };
}

function grantPreset(value: unknown, path: string): SourceControlGrantPreset {
  const item = objectValue(value, path);
  exactKeys(
    item,
    [
      'schema',
      'preset_id',
      'label',
      'description',
      'operation',
      'transformation',
      'purpose',
      'max_duration_seconds',
    ],
    path,
  );
  if (item['schema'] !== 'ananta.source-control.grant-preset.v1') {
    fail(`${path}.schema_invalid`);
  }
  for (const key of [
    'preset_id',
    'operation',
    'transformation',
    'purpose',
  ] as const) {
    assertSourceControlOpaqueId(item[key], `${path}.${key}`);
  }
  return {
    schema: 'ananta.source-control.grant-preset.v1',
    preset_id: item['preset_id'] as string,
    label: text(item['label'], `${path}.label`, 256),
    description: text(item['description'], `${path}.description`, 1024, true),
    operation: item['operation'] as string,
    transformation: item['transformation'] as string,
    purpose: item['purpose'] as string,
    max_duration_seconds: integer(
      item['max_duration_seconds'],
      `${path}.max_duration_seconds`,
      60,
    ),
  };
}

function grant(value: unknown, path: string): SourceControlGrant {
  const item = objectValue(value, path);
  exactKeys(
    item,
    [
      'schema',
      'grant_id',
      'grant_family_id',
      'version',
      'source_revision_id',
      'destination_id',
      'preset_id',
      'operation',
      'transformation',
      'purpose',
      'policy_version',
      'state',
      'issued_at',
      'expires_at',
      'expired',
      'etag',
    ],
    path,
  );
  if (item['schema'] !== 'ananta.source-control.grant-admin-item.v1') {
    fail(`${path}.schema_invalid`);
  }
  for (const key of [
    'grant_id',
    'grant_family_id',
    'source_revision_id',
    'destination_id',
    'operation',
    'transformation',
    'purpose',
    'policy_version',
    'state',
  ] as const) {
    assertSourceControlOpaqueId(item[key], `${path}.${key}`);
  }
  const presetId = item['preset_id'];
  if (presetId !== null) {
    assertSourceControlOpaqueId(presetId, `${path}.preset_id`);
  }
  assertSourceControlSha256(item['etag'], `${path}.etag`);
  return {
    schema: 'ananta.source-control.grant-admin-item.v1',
    grant_id: item['grant_id'] as string,
    grant_family_id: item['grant_family_id'] as string,
    version: integer(item['version'], `${path}.version`, 1),
    source_revision_id: item['source_revision_id'] as string,
    destination_id: item['destination_id'] as string,
    preset_id: presetId as string | null,
    operation: item['operation'] as string,
    transformation: item['transformation'] as string,
    purpose: item['purpose'] as string,
    policy_version: item['policy_version'] as string,
    state: item['state'] as string,
    issued_at: text(item['issued_at'], `${path}.issued_at`, 128),
    expires_at: text(item['expires_at'], `${path}.expires_at`, 128),
    expired: booleanValue(item['expired'], `${path}.expired`),
    etag: item['etag'],
  };
}
