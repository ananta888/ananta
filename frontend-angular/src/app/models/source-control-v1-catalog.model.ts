/** Strict parsers for Source Control v1 content admission and catalog pages. */

import {
  assertSourceControlOpaqueId,
} from './source-control-v1-api.model';
import type {
  SourceControlCatalogPage,
  SourceControlContentAdmissionCreation,
  SourceControlContentAdmissionValidation,
  SourceControlIndexProfileCatalogPage,
  SourceControlRegisteredRemoteCatalogPage,
  SourceControlWorkspaceCatalogPage,
} from './source-control-v1-governance-types.model';
import {
  arrayValue,
  booleanValue,
  cursor,
  exactKeys,
  fail,
  objectValue,
  safeObject,
  text,
} from './source-control-v1-governance-validation.model';

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
