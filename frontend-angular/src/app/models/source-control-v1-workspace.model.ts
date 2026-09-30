/** Strict parsers for Source Control v1 workspace folders and registrations. */

import {
  assertSourceControlOpaqueId,
} from './source-control-v1-api.model';
import type {
  SourceControlWorkspaceFolder,
  SourceControlWorkspaceFolderPage,
  SourceControlWorkspaceFolderValidation,
  SourceControlWorkspaceRegistration,
} from './source-control-v1-governance-types.model';
import {
  arrayValue,
  exactKeys,
  fail,
  objectValue,
  positiveInteger,
  requireCapability,
  text,
} from './source-control-v1-governance-validation.model';

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
