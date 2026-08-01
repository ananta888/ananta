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

export interface SourceControlWorkspaceFolder {
  readonly folder_handle: string;
  readonly display_name: string;
  readonly capabilities: {
    readonly selection_only: true;
    readonly read_only: true;
    readonly path_exposed: false;
    readonly file_names_exposed: false;
    readonly folder_label_exposed: true;
  };
}

export interface SourceControlWorkspaceFolderPage {
  readonly items: readonly SourceControlWorkspaceFolder[];
  readonly capabilities: {
    readonly project_scoped: true;
    readonly raw_paths_exposed: false;
  };
}

export interface SourceControlWorkspaceFolderValidation {
  readonly validation_handle: string;
  readonly expires_at_epoch: number;
  readonly capabilities: {
    readonly read_only: true;
    readonly one_time: true;
    readonly path_exposed: false;
    readonly filename_exposed: false;
  };
}

export interface SourceControlWorkspaceRegistration {
  readonly workspace_id: string;
  readonly state: 'active';
  readonly read_only: true;
  readonly etag: string;
  readonly capabilities: {
    readonly selection_only: true;
    readonly path_exposed: false;
    readonly filename_exposed: false;
  };
}

export type SourceControlGitAuthorizationKind =
  | 'github_app'
  | 'github_oauth'
  | 'generic_git';

export type SourceControlGitAuthorizationState =
  | 'active'
  | 'revoked'
  | 'scope_loss';

export type SourceControlGitAuthorizationNextAction =
  | 'revoke'
  | 'record_scope_loss';

export interface SourceControlGitAuthorizationView {
  readonly authorization_ref: string;
  readonly authorization_kind: SourceControlGitAuthorizationKind;
  readonly repository: string | null;
  readonly authorization_state: SourceControlGitAuthorizationState;
  readonly granted_scopes: readonly string[];
  readonly credential_configured: boolean;
  readonly persisted: boolean;
  readonly current_revision: number;
  readonly etag: string | null;
  readonly next_actions: readonly SourceControlGitAuthorizationNextAction[];
}

export interface SourceControlGitAuthorizationPage {
  readonly items: readonly SourceControlGitAuthorizationView[];
  readonly next_cursor: string | null;
}

export type SourceControlGitAuthorizationHealthStatus =
  | 'healthy'
  | 'degraded'
  | 'unavailable';

export interface SourceControlGitAuthorizationHealth {
  readonly status: SourceControlGitAuthorizationHealthStatus;
  readonly reason_code: string | null;
  readonly provider_status: SourceControlGitAuthorizationHealthStatus;
  readonly connector_ready: {
    readonly github_repository: boolean;
    readonly generic_git: boolean;
  };
  readonly registration_count: number;
  readonly active_registration_count: number;
}

export type SourceControlPublicRemoteProvider =
  | 'github_public'
  | 'https_git';

export type SourceControlPublicRemoteIntent =
  | {
      readonly provider: 'github_public';
      readonly owner: string;
      readonly repository: string;
      readonly requested_ref: string;
    }
  | {
      readonly provider: 'https_git';
      readonly host: string;
      readonly repository: string;
      readonly requested_ref: string;
    };

export interface SourceControlPublicRemoteValidation {
  readonly validation_handle: string;
  readonly provider: SourceControlPublicRemoteProvider;
  readonly requested_ref: string;
  readonly commit_sha: string;
  readonly expires_at_epoch: number;
  readonly capabilities: Readonly<Record<string, boolean>>;
}

export interface SourceControlPublicRemoteCreation {
  readonly remote_id: string;
  readonly provider: SourceControlPublicRemoteProvider;
  readonly commit_sha: string;
  readonly state: string;
  readonly capabilities: Readonly<Record<string, boolean>>;
}

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
  'clone_url',
  'credential',
  'credential_ref',
  'credentials',
  'file_content',
  'private_remote_url',
  'prompt',
  'raw_content',
  'remote_url',
  'secret',
  'token',
]);

const PUBLIC_REMOTE_FORBIDDEN_CAPABILITIES = new Set([
  ...SENSITIVE_KEYS,
  'ip',
  'port',
]);

export function parsePublicRemoteIntent(
  value: unknown,
  path = 'public_remote_intent',
): SourceControlPublicRemoteIntent {
  const input = objectValue(value, path);
  const provider = publicRemoteProvider(input['provider'], `${path}.provider`);
  if (provider === 'github_public') {
    exactKeys(
      input,
      ['provider', 'owner', 'repository', 'requested_ref'],
      path,
    );
    return {
      provider,
      owner: githubOwner(input['owner'], `${path}.owner`),
      repository: publicRemoteRepository(
        input['repository'],
        provider,
        `${path}.repository`,
      ),
      requested_ref: publicRemoteRef(
        input['requested_ref'],
        `${path}.requested_ref`,
      ),
    };
  }

  exactKeys(
    input,
    ['provider', 'host', 'repository', 'requested_ref'],
    path,
  );
  return {
    provider,
    host: publicDnsHost(input['host'], `${path}.host`),
    repository: publicRemoteRepository(
      input['repository'],
      provider,
      `${path}.repository`,
    ),
    requested_ref: publicRemoteRef(
      input['requested_ref'],
      `${path}.requested_ref`,
    ),
  };
}

export function parsePublicRemoteValidation(
  value: unknown,
  path = 'public_remote_validation',
): SourceControlPublicRemoteValidation {
  const input = objectValue(value, path);
  exactKeys(
    input,
    [
      'validation_handle',
      'provider',
      'requested_ref',
      'commit_sha',
      'expires_at_epoch',
      'capabilities',
    ],
    path,
  );
  assertSourceControlOpaqueId(
    input['validation_handle'],
    `${path}.validation_handle`,
  );
  return {
    validation_handle: input['validation_handle'],
    provider: publicRemoteProvider(input['provider'], `${path}.provider`),
    requested_ref: publicRemoteRef(
      input['requested_ref'],
      `${path}.requested_ref`,
    ),
    commit_sha: publicRemoteCommitSha(
      input['commit_sha'],
      `${path}.commit_sha`,
    ),
    expires_at_epoch: positiveInteger(
      input['expires_at_epoch'],
      `${path}.expires_at_epoch`,
    ),
    capabilities: publicRemoteCapabilities(
      input['capabilities'],
      `${path}.capabilities`,
    ),
  };
}

export function parsePublicRemoteCreation(
  value: unknown,
  path = 'public_remote_creation',
): SourceControlPublicRemoteCreation {
  const input = objectValue(value, path);
  exactKeys(
    input,
    ['remote_id', 'provider', 'commit_sha', 'state', 'capabilities'],
    path,
  );
  assertSourceControlOpaqueId(input['remote_id'], `${path}.remote_id`);
  assertSourceControlOpaqueId(input['state'], `${path}.state`);
  return {
    remote_id: input['remote_id'],
    provider: publicRemoteProvider(input['provider'], `${path}.provider`),
    commit_sha: publicRemoteCommitSha(
      input['commit_sha'],
      `${path}.commit_sha`,
    ),
    state: input['state'],
    capabilities: publicRemoteCapabilities(
      input['capabilities'],
      `${path}.capabilities`,
    ),
  };
}

function publicRemoteProvider(
  value: unknown,
  path: string,
): SourceControlPublicRemoteProvider {
  if (value !== 'github_public' && value !== 'https_git') {
    fail(`${path}_invalid`);
  }
  return value;
}

function githubOwner(value: unknown, path: string): string {
  const owner = text(value, path, 39);
  if (
    !/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(owner)
    || owner.includes('--')
  ) {
    fail(`${path}_invalid`);
  }
  return owner;
}

function publicRemoteRepository(
  value: unknown,
  provider: SourceControlPublicRemoteProvider,
  path: string,
): string {
  const repository = text(value, path, provider === 'github_public' ? 100 : 512);
  if (
    repository.includes('://')
    || /[\\?#:@]/.test(repository)
    || repository.startsWith('/')
    || repository.endsWith('/')
    || repository.includes('//')
  ) {
    fail(`${path}_invalid`);
  }
  const segments = repository.split('/');
  if (
    (provider === 'github_public' && segments.length !== 1)
    || segments.length > 10
    || segments.some(
      (segment) =>
        segment === '.'
        || segment === '..'
        || !/^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$/.test(
          segment,
        ),
    )
  ) {
    fail(`${path}_invalid`);
  }
  return repository;
}

function publicRemoteRef(value: unknown, path: string): string {
  const ref = text(value, path, 255);
  const invalidComponent = ref
    .split('/')
    .some(
      (component) =>
        component.length === 0
        || component.startsWith('.')
        || component.endsWith('.')
        || component.endsWith('.lock'),
    );
  if (
    ref === '@'
    || ref.includes('..')
    || ref.includes('@{')
    || /[\u0000-\u0020\u007f~^:?*\[\\]/.test(ref)
    || invalidComponent
  ) {
    fail(`${path}_invalid`);
  }
  return ref;
}

function publicDnsHost(value: unknown, path: string): string {
  const host = text(value, path, 253);
  if (
    host !== host.toLowerCase()
    || host === 'localhost'
    || host.endsWith('.localhost')
    || host.includes('://')
    || host.includes(':')
    || host.endsWith('.')
    || /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host)
  ) {
    fail(`${path}_invalid`);
  }
  const labels = host.split('.');
  if (
    labels.length < 2
    || /^\d+$/.test(labels[labels.length - 1])
    || labels.some(
      (label) =>
        label.length > 63
        || !/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label),
    )
  ) {
    fail(`${path}_invalid`);
  }
  return host;
}

function publicRemoteCommitSha(value: unknown, path: string): string {
  if (
    typeof value !== 'string'
    || (!/^[0-9a-f]{40}$/.test(value) && !/^[0-9a-f]{64}$/.test(value))
  ) {
    fail(`${path}_invalid`);
  }
  return value;
}

function positiveInteger(value: unknown, path: string): number {
  if (!Number.isSafeInteger(value) || (value as number) <= 0) {
    fail(`${path}_invalid`);
  }
  return value as number;
}

function workspaceDisplayName(value: unknown, path: string): string {
  const label = text(value, path, 80);
  if (/[\\/\u0000-\u001f\u007f]/.test(label)) {
    fail(`${path}_invalid`);
  }
  return label;
}

function workspaceFolderCapabilities(
  value: unknown,
  path: string,
): SourceControlWorkspaceFolder['capabilities'] {
  const capabilities = objectValue(value, path);
  exactKeys(
    capabilities,
    [
      'selection_only',
      'read_only',
      'path_exposed',
      'file_names_exposed',
      'folder_label_exposed',
    ],
    path,
  );
  requireCapability(capabilities, 'selection_only', true, path);
  requireCapability(capabilities, 'read_only', true, path);
  requireCapability(capabilities, 'path_exposed', false, path);
  requireCapability(capabilities, 'file_names_exposed', false, path);
  requireCapability(capabilities, 'folder_label_exposed', true, path);
  return {
    selection_only: true,
    read_only: true,
    path_exposed: false,
    file_names_exposed: false,
    folder_label_exposed: true,
  };
}

function workspaceFolderPageCapabilities(
  value: unknown,
  path: string,
): SourceControlWorkspaceFolderPage['capabilities'] {
  const capabilities = objectValue(value, path);
  exactKeys(
    capabilities,
    ['project_scoped', 'raw_paths_exposed'],
    path,
  );
  requireCapability(capabilities, 'project_scoped', true, path);
  requireCapability(capabilities, 'raw_paths_exposed', false, path);
  return {
    project_scoped: true,
    raw_paths_exposed: false,
  };
}

function workspaceValidationCapabilities(
  value: unknown,
  path: string,
): SourceControlWorkspaceFolderValidation['capabilities'] {
  const capabilities = objectValue(value, path);
  exactKeys(
    capabilities,
    ['read_only', 'one_time', 'path_exposed', 'filename_exposed'],
    path,
  );
  requireCapability(capabilities, 'read_only', true, path);
  requireCapability(capabilities, 'one_time', true, path);
  requireCapability(capabilities, 'path_exposed', false, path);
  requireCapability(capabilities, 'filename_exposed', false, path);
  return {
    read_only: true,
    one_time: true,
    path_exposed: false,
    filename_exposed: false,
  };
}

function workspaceRegistrationCapabilities(
  value: unknown,
  path: string,
): SourceControlWorkspaceRegistration['capabilities'] {
  const capabilities = objectValue(value, path);
  exactKeys(
    capabilities,
    ['selection_only', 'path_exposed', 'filename_exposed'],
    path,
  );
  requireCapability(capabilities, 'selection_only', true, path);
  requireCapability(capabilities, 'path_exposed', false, path);
  requireCapability(capabilities, 'filename_exposed', false, path);
  return {
    selection_only: true,
    path_exposed: false,
    filename_exposed: false,
  };
}

function requireCapability(
  capabilities: Record<string, unknown>,
  key: string,
  expected: boolean,
  path: string,
): void {
  if (
    typeof capabilities[key] !== 'boolean'
    || capabilities[key] !== expected
  ) {
    fail(`${path}.${key}_invalid`);
  }
}

function publicRemoteCapabilities(
  value: unknown,
  path: string,
): Readonly<Record<string, boolean>> {
  const input = objectValue(value, path);
  const result: Record<string, boolean> = {};
  for (const [key, capability] of Object.entries(input)) {
    if (
      !/^[a-z][a-z0-9_]{0,63}$/.test(key)
      || PUBLIC_REMOTE_FORBIDDEN_CAPABILITIES.has(key)
      || typeof capability !== 'boolean'
    ) {
      fail(`${path}.${key}_invalid`);
    }
    result[key] = capability;
  }
  return result;
}

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

export function assertSourceControlGitAuthorizationEtag(
  value: unknown,
  path = 'git_authorization_etag',
): asserts value is string {
  if (
    typeof value !== 'string'
    || !/^"git-auth-v1:[1-9][0-9]*"$/.test(value)
  ) {
    fail(`${path}_invalid`);
  }
}

export function parseGitAuthorizationView(
  value: unknown,
  path = 'git_authorization',
): SourceControlGitAuthorizationView {
  const item = objectValue(value, path);
  exactKeys(
    item,
    [
      'authorization_ref',
      'authorization_kind',
      'repository',
      'authorization_state',
      'granted_scopes',
      'credential_configured',
      'persisted',
      'current_revision',
      'etag',
      'next_actions',
    ],
    path,
  );

  assertSourceControlOpaqueId(
    item['authorization_ref'],
    `${path}.authorization_ref`,
  );
  const authorizationKind = gitAuthorizationKind(
    item['authorization_kind'],
    `${path}.authorization_kind`,
  );
  const repository = gitRepository(
    item['repository'],
    authorizationKind,
    `${path}.repository`,
  );
  const authorizationState = gitAuthorizationState(
    item['authorization_state'],
    `${path}.authorization_state`,
  );
  const grantedScopes = arrayValue(
    item['granted_scopes'],
    `${path}.granted_scopes`,
  ).map((scope, index) => {
    const normalized = text(
      scope,
      `${path}.granted_scopes[${index}]`,
      128,
    );
    if (!/^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/.test(normalized)) {
      fail(`${path}.granted_scopes[${index}]_invalid`);
    }
    return normalized;
  });
  if (
    grantedScopes.length === 0
    || new Set(grantedScopes).size !== grantedScopes.length
  ) {
    fail(`${path}.granted_scopes_invalid`);
  }

  const persisted = booleanValue(item['persisted'], `${path}.persisted`);
  const currentRevision = nonNegativeInteger(
    item['current_revision'],
    `${path}.current_revision`,
  );
  const rawEtag = item['etag'];
  let etag: string | null = null;
  if (persisted) {
    if (currentRevision < 1) fail(`${path}.current_revision_invalid`);
    assertSourceControlGitAuthorizationEtag(rawEtag, `${path}.etag`);
    const etagRevision = Number(
      /^"git-auth-v1:([1-9][0-9]*)"$/.exec(rawEtag)?.[1],
    );
    if (
      !Number.isSafeInteger(etagRevision)
      || etagRevision !== currentRevision
    ) {
      fail(`${path}.etag_revision_mismatch`);
    }
    etag = rawEtag;
  } else if (currentRevision !== 0 || rawEtag !== null) {
    fail(`${path}.transient_revision_invalid`);
  }

  const nextActions = gitAuthorizationNextActions(
    item['next_actions'],
    authorizationState,
    `${path}.next_actions`,
  );
  return {
    authorization_ref: item['authorization_ref'],
    authorization_kind: authorizationKind,
    repository,
    authorization_state: authorizationState,
    granted_scopes: grantedScopes,
    credential_configured: booleanValue(
      item['credential_configured'],
      `${path}.credential_configured`,
    ),
    persisted,
    current_revision: currentRevision,
    etag,
    next_actions: nextActions,
  };
}

export function parseGitAuthorizationPage(
  value: unknown,
  path = 'git_authorization_page',
): SourceControlGitAuthorizationPage {
  const page = objectValue(value, path);
  exactKeys(page, ['items', 'next_cursor'], path);
  const items = arrayValue(page['items'], `${path}.items`).map(
    (item, index) => {
      const parsed = parseGitAuthorizationView(
        item,
        `${path}.items[${index}]`,
      );
      if (!parsed.persisted) fail(`${path}.items[${index}].persisted_invalid`);
      return parsed;
    },
  );
  return {
    items,
    next_cursor: cursor(page['next_cursor'], `${path}.next_cursor`),
  };
}

export function parseGitAuthorizationHealth(
  value: unknown,
  path = 'git_authorization_health',
): SourceControlGitAuthorizationHealth {
  const health = objectValue(value, path);
  exactKeys(
    health,
    [
      'status',
      'reason_code',
      'provider_status',
      'connector_ready',
      'registration_count',
      'active_registration_count',
    ],
    path,
  );
  const status = gitHealthStatus(health['status'], `${path}.status`);
  const providerStatus = gitHealthStatus(
    health['provider_status'],
    `${path}.provider_status`,
  );
  const reasonCode = nullableOpaqueId(
    health['reason_code'],
    `${path}.reason_code`,
  );
  if ((status === 'healthy') !== (reasonCode === null)) {
    fail(`${path}.reason_code_invalid`);
  }
  const connectorReady = objectValue(
    health['connector_ready'],
    `${path}.connector_ready`,
  );
  exactKeys(
    connectorReady,
    ['github_repository', 'generic_git'],
    `${path}.connector_ready`,
  );
  const registrationCount = nonNegativeInteger(
    health['registration_count'],
    `${path}.registration_count`,
  );
  const activeRegistrationCount = nonNegativeInteger(
    health['active_registration_count'],
    `${path}.active_registration_count`,
  );
  if (activeRegistrationCount > registrationCount) {
    fail(`${path}.active_registration_count_invalid`);
  }
  return {
    status,
    reason_code: reasonCode,
    provider_status: providerStatus,
    connector_ready: {
      github_repository: booleanValue(
        connectorReady['github_repository'],
        `${path}.connector_ready.github_repository`,
      ),
      generic_git: booleanValue(
        connectorReady['generic_git'],
        `${path}.connector_ready.generic_git`,
      ),
    },
    registration_count: registrationCount,
    active_registration_count: activeRegistrationCount,
  };
}

export function assertSourceControlWorkspaceEtag(
  value: unknown,
  path = 'workspace_etag',
): asserts value is string {
  if (
    typeof value !== 'string'
    || !/^"workspace-v1:[1-9][0-9]*"$/.test(value)
  ) {
    fail(`${path}_invalid`);
  }
}

export function parseWorkspaceFolderPage(
  value: unknown,
  path = 'workspace_folder_page',
): SourceControlWorkspaceFolderPage {
  const page = objectValue(value, path);
  exactKeys(page, ['items', 'capabilities'], path);
  return {
    items: arrayValue(page['items'], `${path}.items`).map((value, index) => {
      const itemPath = `${path}.items[${index}]`;
      const item = objectValue(value, itemPath);
      exactKeys(
        item,
        ['folder_handle', 'display_name', 'capabilities'],
        itemPath,
      );
      assertSourceControlOpaqueId(
        item['folder_handle'],
        `${itemPath}.folder_handle`,
      );
      return {
        folder_handle: item['folder_handle'],
        display_name: workspaceDisplayName(
          item['display_name'],
          `${itemPath}.display_name`,
        ),
        capabilities: workspaceFolderCapabilities(
          item['capabilities'],
          `${itemPath}.capabilities`,
        ),
      };
    }),
    capabilities: workspaceFolderPageCapabilities(
      page['capabilities'],
      `${path}.capabilities`,
    ),
  };
}

export function parseWorkspaceFolderValidation(
  value: unknown,
  path = 'workspace_folder_validation',
): SourceControlWorkspaceFolderValidation {
  const validation = objectValue(value, path);
  exactKeys(
    validation,
    ['validation_handle', 'expires_at_epoch', 'capabilities'],
    path,
  );
  assertSourceControlOpaqueId(
    validation['validation_handle'],
    `${path}.validation_handle`,
  );
  return {
    validation_handle: validation['validation_handle'],
    expires_at_epoch: positiveInteger(
      validation['expires_at_epoch'],
      `${path}.expires_at_epoch`,
    ),
    capabilities: workspaceValidationCapabilities(
      validation['capabilities'],
      `${path}.capabilities`,
    ),
  };
}

export function parseWorkspaceRegistration(
  value: unknown,
  path = 'workspace_registration',
): SourceControlWorkspaceRegistration {
  const registration = objectValue(value, path);
  exactKeys(
    registration,
    ['workspace_id', 'state', 'read_only', 'etag', 'capabilities'],
    path,
  );
  assertSourceControlOpaqueId(
    registration['workspace_id'],
    `${path}.workspace_id`,
  );
  if (registration['state'] !== 'active') {
    fail(`${path}.state_invalid`);
  }
  if (registration['read_only'] !== true) {
    fail(`${path}.read_only_invalid`);
  }
  assertSourceControlWorkspaceEtag(registration['etag'], `${path}.etag`);
  return {
    workspace_id: registration['workspace_id'],
    state: 'active',
    read_only: true,
    etag: registration['etag'],
    capabilities: workspaceRegistrationCapabilities(
      registration['capabilities'],
      `${path}.capabilities`,
    ),
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

function gitAuthorizationKind(
  value: unknown,
  path: string,
): SourceControlGitAuthorizationKind {
  if (
    value !== 'github_app'
    && value !== 'github_oauth'
    && value !== 'generic_git'
  ) fail(`${path}_invalid`);
  return value;
}

function gitAuthorizationState(
  value: unknown,
  path: string,
): SourceControlGitAuthorizationState {
  if (value !== 'active' && value !== 'revoked' && value !== 'scope_loss') {
    fail(`${path}_invalid`);
  }
  return value;
}

function gitRepository(
  value: unknown,
  kind: SourceControlGitAuthorizationKind,
  path: string,
): string | null {
  if (kind === 'generic_git') {
    if (value !== null) fail(`${path}_forbidden`);
    return null;
  }
  const repository = text(value, path, 201);
  if (
    !/^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})\/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$/.test(
      repository,
    )
  ) fail(`${path}_invalid`);
  return repository;
}

function gitAuthorizationNextActions(
  value: unknown,
  state: SourceControlGitAuthorizationState,
  path: string,
): readonly SourceControlGitAuthorizationNextAction[] {
  const actions = arrayValue(value, path).map((action, index) => {
    if (action !== 'revoke' && action !== 'record_scope_loss') {
      fail(`${path}[${index}]_invalid`);
    }
    return action;
  });
  if (state === 'active') {
    if (
      actions.length !== 2
      || actions[0] !== 'revoke'
      || actions[1] !== 'record_scope_loss'
    ) fail(`${path}_state_mismatch`);
  } else if (actions.length !== 0) {
    fail(`${path}_state_mismatch`);
  }
  return actions;
}

function gitHealthStatus(
  value: unknown,
  path: string,
): SourceControlGitAuthorizationHealthStatus {
  if (value !== 'healthy' && value !== 'degraded' && value !== 'unavailable') {
    fail(`${path}_invalid`);
  }
  return value;
}

function nullableOpaqueId(value: unknown, path: string): string | null {
  if (value === null) return null;
  assertSourceControlOpaqueId(value, path);
  return value;
}

function nonNegativeInteger(value: unknown, path: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    fail(`${path}_invalid`);
  }
  return Number(value);
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
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return value as null | boolean | string;
  }
  if (typeof value === 'number' && Number.isFinite(value)) return value;
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
