/** Strict parsers for Source Control v1 Git authorization views and health. */

import {
  assertSourceControlOpaqueId,
} from './source-control-v1-api.model';
import type {
  SourceControlGitAuthorizationHealth,
  SourceControlGitAuthorizationHealthStatus,
  SourceControlGitAuthorizationKind,
  SourceControlGitAuthorizationNextAction,
  SourceControlGitAuthorizationPage,
  SourceControlGitAuthorizationState,
  SourceControlGitAuthorizationView,
} from './source-control-v1-governance-types.model';
import {
  arrayValue,
  booleanValue,
  cursor,
  exactKeys,
  fail,
  nonNegativeInteger,
  nullableOpaqueId,
  objectValue,
  text,
} from './source-control-v1-governance-validation.model';

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
