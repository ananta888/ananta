/** Strict parsers for Source Control v1 public remote intents and results. */

import {
  assertSourceControlOpaqueId,
} from './source-control-v1-api.model';
import type {
  SourceControlPublicRemoteCapabilities,
  SourceControlPublicRemoteCreation,
  SourceControlPublicRemoteIntent,
  SourceControlPublicRemoteProvider,
  SourceControlPublicRemoteValidation,
} from './source-control-v1-governance-types.model';
import {
  SENSITIVE_KEYS,
  exactKeys,
  fail,
  objectValue,
  positiveInteger,
  text,
} from './source-control-v1-governance-validation.model';

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
      true,
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
      false,
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

function publicRemoteCapabilities(
  value: unknown,
  path: string,
  immutableValidation: boolean,
): SourceControlPublicRemoteCapabilities {
  const input = objectValue(value, path);
  exactKeys(
    input,
    immutableValidation
      ? ['connector_type', 'credential_mode', 'remote_url_exposed', 'immutable_validation']
      : ['connector_type', 'credential_mode', 'remote_url_exposed'],
    path,
  );
  const connectorType = input['connector_type'];
  if (connectorType !== 'github' && connectorType !== 'git') {
    fail(`${path}.connector_type_invalid`);
  }
  if (input['credential_mode'] !== 'none') {
    fail(`${path}.credential_mode_invalid`);
  }
  if (input['remote_url_exposed'] !== false) {
    fail(`${path}.remote_url_exposed_invalid`);
  }
  if (
    Object.keys(input).some((key) => PUBLIC_REMOTE_FORBIDDEN_CAPABILITIES.has(key))
    || (immutableValidation && input['immutable_validation'] !== true)
  ) {
    fail(`${path}.immutable_validation_invalid`);
  }
  return {
    connector_type: connectorType,
    credential_mode: 'none',
    remote_url_exposed: false,
    ...(immutableValidation ? { immutable_validation: true as const } : {}),
  };
}
