import {
  SourceControlJson,
  SourceControlJsonObject,
  SourceControlV1ContractError,
  assertSourceControlOpaqueId,
  assertSourceControlSha256,
} from './source-control-v1-api.model';

export interface SourceControlContentAdmissionValidation {
  readonly valid: boolean;
  readonly preview: SourceControlJsonObject;
}

export interface SourceControlContentAdmissionCreation {
  readonly connection: SourceControlJsonObject;
  readonly revision: SourceControlJsonObject;
  readonly content: SourceControlJsonObject;
}

export interface SourceControlWorkspaceCatalogItem {
  readonly workspace_id: string;
  readonly enabled: boolean;
  readonly read_only: boolean;
  readonly capabilities: SourceControlJsonObject;
}

export interface SourceControlRegisteredRemoteCatalogItem {
  readonly remote_id: string;
  readonly kind: 'git' | 'github';
  readonly repository: string | null;
  readonly state: string;
  readonly capabilities: SourceControlJsonObject;
}

export interface SourceControlIndexProfileCatalogItem {
  readonly profile_id: string;
  readonly label: string;
  readonly description: string;
  readonly is_default: boolean;
  readonly capabilities: SourceControlJsonObject;
}

export interface SourceControlCatalogPage<T> {
  readonly items: readonly T[];
  readonly next_cursor: string | null;
  readonly capabilities: SourceControlJsonObject;
}

export type SourceControlWorkspaceCatalogPage =
  SourceControlCatalogPage<SourceControlWorkspaceCatalogItem>;
export type SourceControlRegisteredRemoteCatalogPage =
  SourceControlCatalogPage<SourceControlRegisteredRemoteCatalogItem>;
export type SourceControlIndexProfileCatalogPage =
  SourceControlCatalogPage<SourceControlIndexProfileCatalogItem>;

export interface SourceControlGrantPreset {
  readonly schema: 'ananta.source-control.grant-preset.v1';
  readonly preset_id: string;
  readonly label: string;
  readonly description: string;
  readonly operation: string;
  readonly transformation: string;
  readonly purpose: string;
  readonly max_duration_seconds: number;
}

export interface SourceControlGrant {
  readonly schema: 'ananta.source-control.grant-admin-item.v1';
  readonly grant_id: string;
  readonly grant_family_id: string;
  readonly version: number;
  readonly source_revision_id: string;
  readonly destination_id: string;
  readonly preset_id: string | null;
  readonly operation: string;
  readonly transformation: string;
  readonly purpose: string;
  readonly policy_version: string;
  readonly state: string;
  readonly issued_at: string;
  readonly expires_at: string;
  readonly expired: boolean;
  readonly etag: string;
}

export interface SourceControlGrantPresetPage {
  readonly items: readonly SourceControlGrantPreset[];
  readonly next_cursor: string | null;
  readonly capabilities: SourceControlJsonObject;
}

export interface SourceControlGrantPage {
  readonly schema: 'ananta.source-control.grant-admin-list.v1';
  readonly items: readonly SourceControlGrant[];
  readonly next_cursor: string | null;
  readonly capabilities: SourceControlJsonObject;
}

export interface SourceControlGrantMutationResult {
  readonly grant: SourceControlGrant;
  readonly capabilities: SourceControlJsonObject;
}

const SENSITIVE_KEYS = new Set([
  'absolute_path',
  'credential',
  'credentials',
  'file_content',
  'private_remote_url',
  'prompt',
  'raw_content',
  'secret',
  'token',
]);

export function parseContentAdmissionValidation(
  value: unknown,
  path = 'content_admission_validation',
): SourceControlContentAdmissionValidation {
  const input = objectValue(value, path);
  exactKeys(input, ['valid', 'preview'], path);
  if (typeof input['valid'] !== 'boolean') fail(`${path}.valid_invalid`);
  return {
    valid: input['valid'],
    preview: safeObject(input['preview'], `${path}.preview`),
  };
}

export function parseContentAdmissionCreation(
  value: unknown,
  path = 'content_admission_creation',
): SourceControlContentAdmissionCreation {
  const input = objectValue(value, path);
  exactKeys(input, ['connection', 'revision', 'content'], path);
  return {
    connection: safeObject(input['connection'], `${path}.connection`),
    revision: safeObject(input['revision'], `${path}.revision`),
    content: safeObject(input['content'], `${path}.content`),
  };
}

export function parseWorkspaceCatalogPage(
  value: unknown,
  path = 'workspace_catalog',
): SourceControlWorkspaceCatalogPage {
  return catalogPage(value, path, (entry, entryPath) => {
    exactKeys(
      entry,
      ['workspace_id', 'enabled', 'read_only', 'capabilities'],
      entryPath,
    );
    assertSourceControlOpaqueId(
      entry['workspace_id'],
      `${entryPath}.workspace_id`,
    );
    return {
      workspace_id: entry['workspace_id'],
      enabled: booleanValue(entry['enabled'], `${entryPath}.enabled`),
      read_only: booleanValue(entry['read_only'], `${entryPath}.read_only`),
      capabilities: safeObject(
        entry['capabilities'],
        `${entryPath}.capabilities`,
      ),
    };
  });
}

export function parseRegisteredRemoteCatalogPage(
  value: unknown,
  path = 'registered_remote_catalog',
): SourceControlRegisteredRemoteCatalogPage {
  return catalogPage(value, path, (entry, entryPath) => {
    exactKeys(
      entry,
      ['remote_id', 'kind', 'repository', 'state', 'capabilities'],
      entryPath,
    );
    assertSourceControlOpaqueId(entry['remote_id'], `${entryPath}.remote_id`);
    if (entry['kind'] !== 'git' && entry['kind'] !== 'github') {
      fail(`${entryPath}.kind_invalid`);
    }
    const repository = entry['repository'];
    if (repository !== null && typeof repository !== 'string') {
      fail(`${entryPath}.repository_invalid`);
    }
    return {
      remote_id: entry['remote_id'],
      kind: entry['kind'],
      repository: repository === null
        ? null
        : text(repository, `${entryPath}.repository`, 512),
      state: text(entry['state'], `${entryPath}.state`, 64),
      capabilities: safeObject(
        entry['capabilities'],
        `${entryPath}.capabilities`,
      ),
    };
  });
}

export function parseIndexProfileCatalogPage(
  value: unknown,
  path = 'index_profile_catalog',
): SourceControlIndexProfileCatalogPage {
  return catalogPage(value, path, (entry, entryPath) => {
    exactKeys(
      entry,
      ['profile_id', 'label', 'description', 'is_default', 'capabilities'],
      entryPath,
    );
    assertSourceControlOpaqueId(entry['profile_id'], `${entryPath}.profile_id`);
    return {
      profile_id: entry['profile_id'],
      label: text(entry['label'], `${entryPath}.label`, 256),
      description: text(
        entry['description'],
        `${entryPath}.description`,
        1024,
        true,
      ),
      is_default: booleanValue(
        entry['is_default'],
        `${entryPath}.is_default`,
      ),
      capabilities: safeObject(
        entry['capabilities'],
        `${entryPath}.capabilities`,
      ),
    };
  });
}

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

function catalogPage<T>(
  value: unknown,
  path: string,
  parseItem: (item: Record<string, unknown>, path: string) => T,
): SourceControlCatalogPage<T> {
  const page = objectValue(value, path);
  exactKeys(page, ['items', 'next_cursor', 'capabilities'], path);
  return {
    items: arrayValue(page['items'], `${path}.items`).map((item, index) =>
      parseItem(
        objectValue(item, `${path}.items[${index}]`),
        `${path}.items[${index}]`,
      ),
    ),
    next_cursor: cursor(page['next_cursor'], `${path}.next_cursor`),
    capabilities: safeObject(page['capabilities'], `${path}.capabilities`),
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
    preset_id: item['preset_id'],
    label: text(item['label'], `${path}.label`, 256),
    description: text(item['description'], `${path}.description`, 1024, true),
    operation: item['operation'],
    transformation: item['transformation'],
    purpose: item['purpose'],
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
    grant_id: item['grant_id'],
    grant_family_id: item['grant_family_id'],
    version: integer(item['version'], `${path}.version`, 1),
    source_revision_id: item['source_revision_id'],
    destination_id: item['destination_id'],
    preset_id: presetId,
    operation: item['operation'],
    transformation: item['transformation'],
    purpose: item['purpose'],
    policy_version: item['policy_version'],
    state: item['state'],
    issued_at: text(item['issued_at'], `${path}.issued_at`, 128),
    expires_at: text(item['expires_at'], `${path}.expires_at`, 128),
    expired: booleanValue(item['expired'], `${path}.expired`),
    etag: item['etag'],
  };
}

function safeObject(value: unknown, path: string): SourceControlJsonObject {
  const input = objectValue(value, path);
  const output: Record<string, SourceControlJson> = {};
  for (const [key, item] of Object.entries(input)) {
    if (SENSITIVE_KEYS.has(key.toLowerCase())) {
      fail(`${path}.${key}_forbidden`);
    }
    output[key] = safeValue(item, `${path}.${key}`);
  }
  return output;
}

function safeValue(value: unknown, path: string): SourceControlJson {
  if (
    value === null
    || typeof value === 'boolean'
    || typeof value === 'string'
    || (typeof value === 'number' && Number.isFinite(value))
  ) return value;
  if (Array.isArray(value)) {
    return value.map((item, index) => safeValue(item, `${path}[${index}]`));
  }
  return safeObject(value, path);
}

function cursor(value: unknown, path: string): string | null {
  if (value === null) return null;
  if (
    typeof value !== 'string'
    || !/^[A-Za-z0-9_-]{1,512}$/.test(value)
  ) fail(`${path}_invalid`);
  return value;
}

function text(
  value: unknown,
  path: string,
  maximum: number,
  allowEmpty = false,
): string {
  if (typeof value !== 'string') fail(`${path}_invalid`);
  const normalized = value.trim();
  if ((!allowEmpty && !normalized) || normalized.length > maximum) {
    fail(`${path}_invalid`);
  }
  return normalized;
}

function integer(value: unknown, path: string, minimum: number): number {
  if (!Number.isInteger(value) || Number(value) < minimum) {
    fail(`${path}_invalid`);
  }
  return Number(value);
}

function booleanValue(value: unknown, path: string): boolean {
  if (typeof value !== 'boolean') fail(`${path}_invalid`);
  return value;
}

function objectValue(value: unknown, path: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail(`${path}_object_required`);
  }
  return value as Record<string, unknown>;
}

function arrayValue(value: unknown, path: string): readonly unknown[] {
  if (!Array.isArray(value)) fail(`${path}_array_required`);
  return value;
}

function exactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  path: string,
): void {
  const keys = new Set(expected);
  if (
    Object.keys(value).length !== keys.size
    || Object.keys(value).some((key) => !keys.has(key))
  ) fail(`${path}_properties_invalid`);
}

function fail(reasonCode: string): never {
  throw new SourceControlV1ContractError(reasonCode);
}
