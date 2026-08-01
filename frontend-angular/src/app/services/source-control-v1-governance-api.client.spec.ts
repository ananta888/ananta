import { HttpClientTestingModule, HttpTestingController } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { SourceControlV1ContractError } from '../models/source-control-v1-api.model';
import { SourceControlV1GovernanceApiClient } from './source-control-v1-governance-api.client';

const envelope = (data: unknown) => ({
  schema: 'ananta.source-control.api-response.v1',
  data,
});

const gitAuthorization = (overrides: Record<string, unknown> = {}) => ({
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

describe('SourceControlV1GovernanceApiClient', () => {
  let api: SourceControlV1GovernanceApiClient;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
      providers: [SourceControlV1GovernanceApiClient],
    });
    api = TestBed.inject(SourceControlV1GovernanceApiClient);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('sends the exact dry-run content admission contract to the v1 validate endpoint', () => {
    const intent = {
      project_id: 'project-alpha',
      source_type: 'direct_text' as const,
      display_name: 'Architecture',
      sensitivity: 'internal',
      content: '# Hub',
      media_type: 'text/markdown' as const,
    };

    api.validateContentAdmission(intent).subscribe({ error: () => undefined });

    const request = http.expectOne('/api/source-control/v1/content-admissions/validate');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ ...intent, dry_run: true });
    expect(request.request.headers.has('Authorization')).toBeFalsy();
    request.flush({ error: { code: 'test-stop' } }, { status: 400, statusText: 'Bad Request' });
  });

  it('persists admitted notebook content only through the production admission endpoint', () => {
    const intent = {
      project_id: 'project-alpha',
      source_type: 'notebook' as const,
      display_name: 'Runbook',
      sensitivity: 'confidential',
      notebook: {
        cells: [
          {
            cell_type: 'code' as const,
            source: 'print("ok")',
            outputs: [{ output_type: 'stream' as const, text: 'ok' }],
          },
        ],
      },
    };

    api
      .createContentAdmission(intent, 'content-create-example')
      .subscribe({ error: () => undefined });

    const request = http.expectOne('/api/source-control/v1/content-admissions');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ ...intent, dry_run: false });
    expect(request.request.headers.get('Idempotency-Key')).toBe('content-create-example');
    request.flush({ error: { code: 'test-stop' } }, { status: 400, statusText: 'Bad Request' });
  });

  it('uses project-scoped v1 catalogs without client-owned authentication tokens', () => {
    api.listWorkspaces('project-alpha').subscribe({ error: () => undefined });

    const request = http.expectOne(
      (candidate) =>
        candidate.url === '/api/source-control/v1/workspaces' &&
        candidate.params.get('project_id') === 'project-alpha',
    );
    expect(request.request.method).toBe('GET');
    expect(request.request.headers.has('Authorization')).toBeFalsy();
    request.flush({ error: { code: 'test-stop' } }, { status: 400, statusText: 'Bad Request' });
  });

  it('requires policy CAS and idempotency headers for grant creation', () => {
    const body = {
      source_revision_id: `srev_${'a'.repeat(64)}`,
      destination_id: 'hub-destination-primary',
      policy_id: 'policy-primary',
      preset_id: 'preset-read',
      duration_seconds: 900,
    };
    const etag = 'b'.repeat(64);

    api
      .createGrant('project-alpha', body, {
        etag,
        idempotencyKey: 'grant-create-example',
      })
      .subscribe({ error: () => undefined });

    const request = http.expectOne(
      (candidate) =>
        candidate.url === '/api/source-control/v1/grants' &&
        candidate.params.get('project_id') === 'project-alpha',
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual(body);
    expect(request.request.headers.get('If-Match')).toBe(`"${etag}"`);
    expect(request.request.headers.get('Idempotency-Key')).toBe('grant-create-example');
    request.flush({ error: { code: 'test-stop' } }, { status: 400, statusText: 'Bad Request' });
  });

  it('validates only the narrow Git authorization selection DTO', () => {
    const selection = {
      authorization_handle: 'github-installation:42',
      authorization_kind: 'github_app' as const,
      repository: 'owner/repository',
    };
    let persisted: boolean | undefined;

    api.validateGitAuthorization(selection).subscribe((result) => {
      persisted = result.persisted;
    });

    const request = http.expectOne(
      '/api/source-control/v1/git-authorizations/validate',
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual(selection);
    expect(JSON.stringify(request.request.body)).not.toContain('token');
    expect(JSON.stringify(request.request.body)).not.toContain('clone_url');
    expect(JSON.stringify(request.request.body)).not.toContain('credential_ref');
    request.flush(
      envelope(
        gitAuthorization({
          persisted: false,
          current_revision: 0,
          etag: null,
        }),
      ),
    );
    expect(persisted).toBe(false);
  });

  it('provisions with idempotency and requires matching quoted ETags', () => {
    const selection = {
      authorization_handle: 'github-installation:42',
      authorization_kind: 'github_app' as const,
      repository: 'owner/repository',
      token: 'compile-time-extra-property-is-dropped',
    };
    let resultEtag: string | null | undefined;

    api
      .provisionGitAuthorization(selection, 'git-auth-provision-42')
      .subscribe((result) => {
        resultEtag = result.etag;
      });

    const request = http.expectOne('/api/source-control/v1/git-authorizations');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({
      authorization_handle: 'github-installation:42',
      authorization_kind: 'github_app',
      repository: 'owner/repository',
    });
    expect(request.request.headers.get('Idempotency-Key')).toBe(
      'git-auth-provision-42',
    );
    request.flush(envelope(gitAuthorization()), {
      headers: { ETag: '"git-auth-v1:1"' },
    });
    expect(resultEtag).toBe('"git-auth-v1:1"');
  });

  it('parses an unavailable health success envelope returned with HTTP 503', () => {
    let status: string | undefined;

    api.gitAuthorizationHealth().subscribe((result) => {
      status = result.status;
    });

    const request = http.expectOne(
      '/api/source-control/v1/git-authorizations/health',
    );
    request.flush(
      envelope({
        status: 'unavailable',
        reason_code: 'provider_not_configured',
        provider_status: 'unavailable',
        connector_ready: {
          github_repository: false,
          generic_git: false,
        },
        registration_count: 0,
        active_registration_count: 0,
      }),
      { status: 503, statusText: 'Service Unavailable' },
    );
    expect(status).toBe('unavailable');
  });

  it('lists and reads persisted authorizations without project or secret parameters', () => {
    let listed = 0;
    let detailRef: string | undefined;

    api.listGitAuthorizations({ limit: 25 }).subscribe((result) => {
      listed = result.items.length;
    });
    const list = http.expectOne(
      (candidate) =>
        candidate.url === '/api/source-control/v1/git-authorizations'
        && candidate.params.get('limit') === '25',
    );
    expect(list.request.params.has('project_id')).toBe(false);
    list.flush(envelope({ items: [gitAuthorization()], next_cursor: null }));

    api
      .gitAuthorizationDetail(
        'github-installation:42',
        'owner/repository',
      )
      .subscribe((result) => {
        detailRef = result.authorization_ref;
      });
    const detail = http.expectOne(
      (candidate) =>
        candidate.url
          === '/api/source-control/v1/git-authorizations/github-installation%3A42'
        && candidate.params.get('repository') === 'owner/repository',
    );
    detail.flush(envelope(gitAuthorization()), {
      headers: { ETag: '"git-auth-v1:1"' },
    });

    expect(listed).toBe(1);
    expect(detailRef).toBe('github-installation:42');
  });

  it('sends lifecycle transitions with exact repository and CAS contracts', () => {
    const guard = {
      etag: '"git-auth-v1:1"',
      idempotencyKey: 'git-auth-transition-42',
    };

    api
      .revokeGitAuthorization(
        'github-installation:42',
        'owner/repository',
        guard,
      )
      .subscribe();
    const revoke = http.expectOne(
      '/api/source-control/v1/git-authorizations/github-installation%3A42/actions/revoke',
    );
    expect(revoke.request.body).toEqual({ repository: 'owner/repository' });
    expect(revoke.request.headers.get('If-Match')).toBe('"git-auth-v1:1"');
    expect(revoke.request.headers.get('Idempotency-Key')).toBe(
      'git-auth-transition-42',
    );
    revoke.flush(
      envelope(
        gitAuthorization({
          authorization_state: 'revoked',
          current_revision: 2,
          etag: '"git-auth-v1:2"',
          next_actions: [],
        }),
      ),
      { headers: { ETag: '"git-auth-v1:2"' } },
    );

    api
      .recordGitAuthorizationScopeLoss(
        'generic-handle-1',
        null,
        guard,
      )
      .subscribe();
    const scopeLoss = http.expectOne(
      '/api/source-control/v1/git-authorizations/generic-handle-1/actions/scope-loss',
    );
    expect(scopeLoss.request.body).toEqual({ repository: null });
    expect(scopeLoss.request.headers.get('If-Match')).toBe(
      '"git-auth-v1:1"',
    );
    scopeLoss.flush(
      envelope(
        gitAuthorization({
          authorization_ref: 'generic-handle-1',
          authorization_kind: 'generic_git',
          repository: null,
          authorization_state: 'scope_loss',
          granted_scopes: ['repository:read'],
          current_revision: 2,
          etag: '"git-auth-v1:2"',
          next_actions: [],
        }),
      ),
      { headers: { ETag: '"git-auth-v1:2"' } },
    );
  });

  it('validates a public GitHub remote with an exact secret-free body', () => {
    const intent = {
      provider: 'github_public' as const,
      owner: 'openai',
      repository: 'codex',
      requested_ref: 'refs/heads/main',
    };
    let handle: string | undefined;

    api.validatePublicRemote('project-alpha', intent).subscribe((result) => {
      handle = result.validation_handle;
    });

    const request = http.expectOne(
      '/api/source-control/v1/public-remotes/validate?project_id=project-alpha',
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.urlWithParams).toBe(
      '/api/source-control/v1/public-remotes/validate?project_id=project-alpha',
    );
    expect(request.request.params.keys()).toEqual(['project_id']);
    expect(request.request.body).toEqual({
      provider: 'github_public',
      owner: 'openai',
      repository: 'codex',
      requested_ref: 'refs/heads/main',
    });
    expect(JSON.stringify(request.request.body)).not.toContain('token');
    expect(JSON.stringify(request.request.body)).not.toContain('clone_url');
    expect(JSON.stringify(request.request.body)).not.toContain('credential');
    request.flush(
      envelope({
        validation_handle: 'public-validation-1',
        provider: 'github_public',
        requested_ref: 'refs/heads/main',
        commit_sha: 'a'.repeat(40),
        expires_at_epoch: 1_800_000_000,
        capabilities: { browse: true },
      }),
    );
    expect(handle).toBe('public-validation-1');
  });

  it('rejects extra public-remote secret fields before sending a request', () => {
    const unsafeIntent = {
      provider: 'github_public' as const,
      owner: 'openai',
      repository: 'codex',
      requested_ref: 'main',
      token: 'must-not-cross-boundary',
    };

    expect(() =>
      api.validatePublicRemote('project-alpha', unsafeIntent),
    ).toThrowError(
      SourceControlV1ContractError,
    );
    http.expectNone('/api/source-control/v1/public-remotes/validate');
  });

  it('fails closed when public validation does not match the submitted intent', () => {
    let contractError: unknown;
    api
      .validatePublicRemote('project-alpha', {
        provider: 'https_git',
        host: 'git.example.org',
        repository: 'platform/ananta',
        requested_ref: 'main',
      })
      .subscribe({ error: (error) => (contractError = error) });

    const request = http.expectOne(
      '/api/source-control/v1/public-remotes/validate?project_id=project-alpha',
    );
    expect(request.request.urlWithParams).toBe(
      '/api/source-control/v1/public-remotes/validate?project_id=project-alpha',
    );
    expect(request.request.params.keys()).toEqual(['project_id']);
    expect(request.request.body).toEqual({
      provider: 'https_git',
      host: 'git.example.org',
      repository: 'platform/ananta',
      requested_ref: 'main',
    });
    request.flush(
      envelope({
        validation_handle: 'public-validation-2',
        provider: 'github_public',
        requested_ref: 'main',
        commit_sha: 'b'.repeat(64),
        expires_at_epoch: 1_800_000_000,
        capabilities: {},
      }),
    );
    expect(contractError).toEqual(expect.any(SourceControlV1ContractError));
  });

  it('creates a public remote using only its validation handle and idempotency', () => {
    let remoteId: string | undefined;
    api
      .createPublicRemote(
        'project-alpha',
        'public-validation-1',
        'public-create-1',
      )
      .subscribe((result) => {
        remoteId = result.remote_id;
      });

    const request = http.expectOne(
      '/api/source-control/v1/public-remotes?project_id=project-alpha',
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.urlWithParams).toBe(
      '/api/source-control/v1/public-remotes?project_id=project-alpha',
    );
    expect(request.request.params.keys()).toEqual(['project_id']);
    expect(request.request.body).toEqual({
      validation_handle: 'public-validation-1',
    });
    expect(request.request.headers.get('Idempotency-Key')).toBe(
      'public-create-1',
    );
    request.flush(
      envelope({
        remote_id: 'public-remote-1',
        provider: 'github_public',
        commit_sha: 'a'.repeat(40),
        state: 'active',
        capabilities: { browse: true },
      }),
    );
    expect(remoteId).toBe('public-remote-1');
  });

  it('lists only opaque project-scoped selectable workspace folders', () => {
    let folderHandle: string | undefined;
    api.listWorkspaceFolders('project-alpha').subscribe((result) => {
      folderHandle = result.items[0].folder_handle;
    });

    const request = http.expectOne(
      '/api/source-control/v1/workspace-folders?project_id=project-alpha',
    );
    expect(request.request.method).toBe('GET');
    expect(request.request.urlWithParams).toBe(
      '/api/source-control/v1/workspace-folders?project_id=project-alpha',
    );
    expect(request.request.params.keys()).toEqual(['project_id']);
    expect(request.request.headers.has('Authorization')).toBeFalsy();
    request.flush(
      envelope({
        items: [
          {
            folder_handle: 'fld_project_alpha',
            display_name: 'Project Alpha',
            capabilities: {
              selection_only: true,
              read_only: true,
              path_exposed: false,
              file_names_exposed: false,
              folder_label_exposed: true,
            },
          },
        ],
        capabilities: {
          project_scoped: true,
          raw_paths_exposed: false,
        },
      }),
    );
    expect(folderHandle).toBe('fld_project_alpha');
  });

  it('validates a workspace folder using only its opaque handle', () => {
    let validationHandle: string | undefined;
    api
      .validateWorkspaceFolder('project-alpha', 'fld_project_alpha')
      .subscribe((result) => {
        validationHandle = result.validation_handle;
      });

    const request = http.expectOne(
      '/api/source-control/v1/workspace-folders/validate?project_id=project-alpha',
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.urlWithParams).toBe(
      '/api/source-control/v1/workspace-folders/validate?project_id=project-alpha',
    );
    expect(request.request.params.keys()).toEqual(['project_id']);
    expect(request.request.body).toEqual({
      folder_handle: 'fld_project_alpha',
    });
    expect(JSON.stringify(request.request.body)).not.toContain('path');
    expect(JSON.stringify(request.request.body)).not.toContain('file');
    expect(JSON.stringify(request.request.body)).not.toContain('upload');
    request.flush(
      envelope({
        validation_handle: 'wsv1_validation_alpha',
        expires_at_epoch: 1_800_000_000,
        capabilities: {
          read_only: true,
          one_time: true,
          path_exposed: false,
          filename_exposed: false,
        },
      }),
    );
    expect(validationHandle).toBe('wsv1_validation_alpha');
  });

  it('creates a workspace registration with exact selection, idempotency, and ETag contracts', () => {
    let workspaceId: string | undefined;
    api
      .createWorkspaceRegistration(
        'project-alpha',
        'wsv1_validation_alpha',
        'workspace-create-alpha',
      )
      .subscribe((result) => {
        workspaceId = result.workspace_id;
      });

    const request = http.expectOne(
      '/api/source-control/v1/workspaces?project_id=project-alpha',
    );
    expect(request.request.method).toBe('POST');
    expect(request.request.urlWithParams).toBe(
      '/api/source-control/v1/workspaces?project_id=project-alpha',
    );
    expect(request.request.params.keys()).toEqual(['project_id']);
    expect(request.request.body).toEqual({
      validation_handle: 'wsv1_validation_alpha',
    });
    expect(request.request.headers.get('Idempotency-Key')).toBe(
      'workspace-create-alpha',
    );
    expect(JSON.stringify(request.request.body)).not.toContain('path');
    expect(JSON.stringify(request.request.body)).not.toContain('upload');
    request.flush(
      envelope({
        workspace_id: 'ws_project_alpha',
        state: 'active',
        read_only: true,
        etag: '"workspace-v1:1"',
        capabilities: {
          selection_only: true,
          path_exposed: false,
          filename_exposed: false,
        },
      }),
      { headers: { ETag: '"workspace-v1:1"' } },
    );
    expect(workspaceId).toBe('ws_project_alpha');
  });

  it('fails closed when workspace response ETag and privacy metadata disagree', () => {
    let contractError: unknown;
    api
      .createWorkspaceRegistration(
        'project-alpha',
        'wsv1_validation_alpha',
        'workspace-create-mismatch',
      )
      .subscribe({ error: (error) => (contractError = error) });

    const request = http.expectOne(
      '/api/source-control/v1/workspaces?project_id=project-alpha',
    );
    request.flush(
      envelope({
        workspace_id: 'ws_project_alpha',
        state: 'active',
        read_only: true,
        etag: '"workspace-v1:1"',
        capabilities: {
          selection_only: true,
          path_exposed: false,
          filename_exposed: false,
        },
      }),
      { headers: { ETag: '"workspace-v1:2"' } },
    );
    expect(contractError).toEqual(expect.any(SourceControlV1ContractError));
  });

  it('rejects an empty project id for every project-scoped onboarding call', () => {
    const intent = {
      provider: 'github_public' as const,
      owner: 'openai',
      repository: 'codex',
      requested_ref: 'refs/heads/main',
    };

    expect(() => api.validatePublicRemote('', intent)).toThrowError(
      SourceControlV1ContractError,
    );
    expect(() =>
      api.createPublicRemote('', 'public-validation-1', 'public-create-1'),
    ).toThrowError(SourceControlV1ContractError);
    expect(() => api.listWorkspaceFolders('')).toThrowError(
      SourceControlV1ContractError,
    );
    expect(() =>
      api.validateWorkspaceFolder('', 'fld_project_alpha'),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      api.createWorkspaceRegistration(
        '',
        'wsv1_validation_alpha',
        'workspace-create-alpha',
      ),
    ).toThrowError(SourceControlV1ContractError);

    http.expectNone(
      (request) =>
        request.url.startsWith('/api/source-control/v1/public-remotes')
        || request.url.startsWith('/api/source-control/v1/workspace-folders')
        || request.url === '/api/source-control/v1/workspaces',
    );
  });
});
