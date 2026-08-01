import { SourceControlV1ContractError } from './source-control-v1-api.model';
import {
  assertSourceControlGitAuthorizationEtag,
  assertSourceControlWorkspaceEtag,
  parseGitAuthorizationHealth,
  parseGitAuthorizationPage,
  parseGitAuthorizationView,
  parsePublicRemoteCreation,
  parsePublicRemoteIntent,
  parsePublicRemoteValidation,
  parseWorkspaceFolderPage,
  parseWorkspaceFolderValidation,
  parseWorkspaceRegistration,
} from './source-control-v1-governance.model';

const persistedAuthorization = (overrides: Record<string, unknown> = {}) => ({
  authorization_ref: 'github-installation:42',
  authorization_kind: 'github_app',
  repository: 'owner/repository',
  authorization_state: 'active',
  granted_scopes: ['contents:read', 'metadata:read'],
  credential_configured: true,
  persisted: true,
  current_revision: 1,
  etag: '"git-auth-v1:1"',
  next_actions: ['revoke', 'record_scope_loss'],
  ...overrides,
});

describe('source-control v1 Git authorization governance contracts', () => {
  it('parses the transient validation normal form', () => {
    const result = parseGitAuthorizationView(
      persistedAuthorization({
        persisted: false,
        current_revision: 0,
        etag: null,
      }),
    );

    expect(result.persisted).toBe(false);
    expect(result.repository).toBe('owner/repository');
    expect(result.etag).toBeNull();
  });

  it('parses persisted pages and the exact health shape', () => {
    const page = parseGitAuthorizationPage({
      items: [persistedAuthorization()],
      next_cursor: 'next_page_1',
    });
    const health = parseGitAuthorizationHealth({
      status: 'healthy',
      reason_code: null,
      provider_status: 'healthy',
      connector_ready: {
        github_repository: true,
        generic_git: true,
      },
      registration_count: 2,
      active_registration_count: 1,
    });

    expect(page.items[0].authorization_state).toBe('active');
    expect(health.connector_ready.github_repository).toBe(true);
  });

  it('rejects unexpected credential and remote material', () => {
    for (const forbiddenKey of [
      'token',
      'clone_url',
      'remote_url',
      'credential_ref',
    ]) {
      expect(() =>
        parseGitAuthorizationView(
          persistedAuthorization({ [forbiddenKey]: 'must-not-cross-boundary' }),
        ),
      ).toThrowError(SourceControlV1ContractError);
    }
  });

  it('enforces provider-to-repository normalization', () => {
    expect(() =>
      parseGitAuthorizationView(
        persistedAuthorization({ repository: null }),
      ),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      parseGitAuthorizationView(
        persistedAuthorization({
          authorization_kind: 'generic_git',
          repository: 'owner/repository',
          granted_scopes: ['repository:read'],
        }),
      ),
    ).toThrowError(SourceControlV1ContractError);
  });

  it('enforces quoted revision ETags and lifecycle invariants', () => {
    expect(() =>
      assertSourceControlGitAuthorizationEtag('git-auth-v1:1'),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      parseGitAuthorizationView(
        persistedAuthorization({ etag: '"git-auth-v1:2"' }),
      ),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      parseGitAuthorizationView(
        persistedAuthorization({
          authorization_state: 'revoked',
          next_actions: ['revoke'],
        }),
      ),
    ).toThrowError(SourceControlV1ContractError);
  });

  it('fails closed on malformed health relationships', () => {
    expect(() =>
      parseGitAuthorizationHealth({
        status: 'unavailable',
        reason_code: null,
        provider_status: 'unavailable',
        connector_ready: {
          github_repository: false,
          generic_git: false,
        },
        registration_count: 0,
        active_registration_count: 1,
      }),
    ).toThrowError(SourceControlV1ContractError);
  });
});

describe('source-control v1 public remote contracts', () => {
  const validation = (overrides: Record<string, unknown> = {}) => ({
    validation_handle: 'public-validation-1',
    provider: 'github_public',
    requested_ref: 'refs/heads/main',
    commit_sha: 'a'.repeat(40),
    expires_at_epoch: 1_800_000_000,
    capabilities: {
      connector_type: 'github',
      credential_mode: 'none',
      remote_url_exposed: false,
      immutable_validation: true,
    },
    ...overrides,
  });

  it('parses the two closed intent variants', () => {
    expect(
      parsePublicRemoteIntent({
        provider: 'github_public',
        owner: 'openai',
        repository: 'codex',
        requested_ref: 'refs/heads/main',
      }),
    ).toEqual({
      provider: 'github_public',
      owner: 'openai',
      repository: 'codex',
      requested_ref: 'refs/heads/main',
    });
    expect(
      parsePublicRemoteIntent({
        provider: 'https_git',
        host: 'git.example.org',
        repository: 'platform/ananta.git',
        requested_ref: 'v1.2.3',
      }),
    ).toEqual({
      provider: 'https_git',
      host: 'git.example.org',
      repository: 'platform/ananta.git',
      requested_ref: 'v1.2.3',
    });
  });

  it('parses validation and creation without URL or credential material', () => {
    expect(parsePublicRemoteValidation(validation()).commit_sha).toHaveLength(40);
    expect(
      parsePublicRemoteCreation({
        remote_id: 'public-remote-1',
        provider: 'https_git',
        commit_sha: 'b'.repeat(64),
        state: 'active',
        capabilities: {
          connector_type: 'git',
          credential_mode: 'none',
          remote_url_exposed: false,
        },
      }),
    ).toEqual({
      remote_id: 'public-remote-1',
      provider: 'https_git',
      commit_sha: 'b'.repeat(64),
      state: 'active',
      capabilities: {
        connector_type: 'git',
        credential_mode: 'none',
        remote_url_exposed: false,
      },
    });
  });

  it('rejects extra secret, URL, IP, and port fields', () => {
    for (const forbiddenKey of [
      'clone_url',
      'token',
      'credential_ref',
      'ip',
      'port',
    ]) {
      expect(() =>
        parsePublicRemoteValidation(
          validation({ [forbiddenKey]: 'must-not-cross-boundary' }),
        ),
      ).toThrowError(SourceControlV1ContractError);
    }
  });

  it('rejects unsafe host, repository, and ref values', () => {
    for (const host of [
      'localhost',
      '127.0.0.1',
      '[::1]',
      'git.example.org:8443',
      'https://git.example.org',
    ]) {
      expect(() =>
        parsePublicRemoteIntent({
          provider: 'https_git',
          host,
          repository: 'platform/ananta',
          requested_ref: 'main',
        }),
      ).toThrowError(SourceControlV1ContractError);
    }
    expect(() =>
      parsePublicRemoteIntent({
        provider: 'github_public',
        owner: 'openai',
        repository: '../secret',
        requested_ref: 'main',
      }),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      parsePublicRemoteIntent({
        provider: 'github_public',
        owner: 'openai',
        repository: 'codex',
        requested_ref: 'refs/heads/main..backup',
      }),
    ).toThrowError(SourceControlV1ContractError);
  });

  it('requires lowercase Git object IDs, positive epochs, and closed capabilities', () => {
    expect(() =>
      parsePublicRemoteValidation(
        validation({ commit_sha: 'A'.repeat(40) }),
      ),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      parsePublicRemoteValidation(validation({ expires_at_epoch: 0 })),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      parsePublicRemoteValidation(
        validation({ capabilities: { browse: 'yes' } }),
      ),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      parsePublicRemoteValidation(validation({
        capabilities: {
          connector_type: 'github',
          credential_mode: 'token',
          remote_url_exposed: false,
          immutable_validation: true,
        },
      })),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      parsePublicRemoteValidation(validation({
        capabilities: {
          connector_type: 'github',
          credential_mode: 'none',
          remote_url_exposed: true,
          immutable_validation: true,
        },
      })),
    ).toThrowError(SourceControlV1ContractError);
  });
});

describe('source-control v1 workspace registration contracts', () => {
  const folderCapabilities = {
    selection_only: true,
    read_only: true,
    path_exposed: false,
    file_names_exposed: false,
    folder_label_exposed: true,
  };
  const validationCapabilities = {
    read_only: true,
    one_time: true,
    path_exposed: false,
    filename_exposed: false,
  };
  const registrationCapabilities = {
    selection_only: true,
    path_exposed: false,
    filename_exposed: false,
  };

  it('parses the exact privacy-preserving folder page', () => {
    const result = parseWorkspaceFolderPage({
      items: [
        {
          folder_handle: 'fld_project_alpha',
          display_name: 'Project Alpha',
          capabilities: folderCapabilities,
        },
      ],
      capabilities: {
        project_scoped: true,
        raw_paths_exposed: false,
      },
    });

    expect(result.items[0].display_name).toBe('Project Alpha');
    expect(result.items[0].capabilities.path_exposed).toBeFalsy();
    expect(result.capabilities.project_scoped).toBeTruthy();
  });

  it('parses one-time validation and active registration responses', () => {
    const validation = parseWorkspaceFolderValidation({
      validation_handle: 'wsv1_validation_alpha',
      expires_at_epoch: 1_800_000_000,
      capabilities: validationCapabilities,
    });
    const registration = parseWorkspaceRegistration({
      workspace_id: 'ws_project_alpha',
      state: 'active',
      read_only: true,
      etag: '"workspace-v1:1"',
      capabilities: registrationCapabilities,
    });

    expect(validation.capabilities.one_time).toBeTruthy();
    expect(registration.etag).toBe('"workspace-v1:1"');
  });

  it('rejects path, upload, filename, and unknown response fields', () => {
    for (const forbidden of [
      { path: '/private/project' },
      { upload: 'archive' },
      { file_names: ['secret.txt'] },
      { next_cursor: null },
      { token: 'must-not-cross-boundary' },
    ]) {
      expect(() =>
        parseWorkspaceFolderPage({
          items: [],
          capabilities: {
            project_scoped: true,
            raw_paths_exposed: false,
          },
          ...forbidden,
        }),
      ).toThrowError(SourceControlV1ContractError);
    }
    expect(() =>
      parseWorkspaceFolderPage({
        items: [
          {
            folder_handle: 'fld_project_alpha',
            display_name: '../private/project',
            capabilities: folderCapabilities,
          },
        ],
        capabilities: {
          project_scoped: true,
          raw_paths_exposed: false,
        },
      }),
    ).toThrowError(SourceControlV1ContractError);
  });

  it('requires the exact top-level workspace privacy capabilities', () => {
    for (const capabilities of [
      { project_scoped: false, raw_paths_exposed: false },
      { project_scoped: true, raw_paths_exposed: true },
      {
        project_scoped: true,
        raw_paths_exposed: false,
        absolute_path: '/private/project',
      },
    ]) {
      expect(() =>
        parseWorkspaceFolderPage({ items: [], capabilities }),
      ).toThrowError(SourceControlV1ContractError);
    }
  });

  it('requires fixed privacy capabilities and positive expiry epochs', () => {
    expect(() =>
      parseWorkspaceFolderPage({
        items: [
          {
            folder_handle: 'fld_project_alpha',
            display_name: 'Project Alpha',
            capabilities: { ...folderCapabilities, path_exposed: true },
          },
        ],
        capabilities: {
          project_scoped: true,
          raw_paths_exposed: false,
        },
      }),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      parseWorkspaceFolderValidation({
        validation_handle: 'wsv1_validation_alpha',
        expires_at_epoch: 0,
        capabilities: validationCapabilities,
      }),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      parseWorkspaceRegistration({
        workspace_id: 'ws_project_alpha',
        state: 'active',
        read_only: false,
        etag: '"workspace-v1:1"',
        capabilities: registrationCapabilities,
      }),
    ).toThrowError(SourceControlV1ContractError);
  });

  it('enforces the quoted revision workspace ETag contract', () => {
    expect(() =>
      assertSourceControlWorkspaceEtag('workspace-v1:1'),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      assertSourceControlWorkspaceEtag('"workspace-v1:0"'),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      assertSourceControlWorkspaceEtag('"workspace-v1:2"'),
    ).not.toThrow();
  });
});
