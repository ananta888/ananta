import {
  HttpClient,
  HttpErrorResponse,
  HttpHeaders,
  HttpParams,
  HttpResponse,
} from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, catchError, map, of, throwError } from 'rxjs';

import {
  SourceControlV1ContractError,
  assertSourceControlEtag,
  assertSourceControlIdempotencyKey,
  assertSourceControlOpaqueId,
  assertSourceControlSha256,
  parseSourceControlEnvelope,
  parseSourceControlErrorEnvelope,
} from '../models/source-control-v1-api.model';
import {
  SourceControlContentAdmissionCreation,
  SourceControlContentAdmissionValidation,
  SourceControlGrantMutationResult,
  SourceControlGrantPage,
  SourceControlGrantPresetPage,
  SourceControlGitAuthorizationHealth,
  SourceControlGitAuthorizationKind,
  SourceControlGitAuthorizationPage,
  SourceControlGitAuthorizationView,
  SourceControlIndexProfileCatalogPage,
  SourceControlPublicRemoteCreation,
  SourceControlPublicRemoteIntent,
  SourceControlPublicRemoteValidation,
  SourceControlRegisteredRemoteCatalogPage,
  SourceControlWorkspaceCatalogPage,
  SourceControlWorkspaceFolderPage,
  SourceControlWorkspaceFolderValidation,
  SourceControlWorkspaceRegistration,
  assertSourceControlGitAuthorizationEtag,
  assertSourceControlWorkspaceEtag,
  parseContentAdmissionCreation,
  parseContentAdmissionValidation,
  parseGitAuthorizationHealth,
  parseGitAuthorizationPage,
  parseGitAuthorizationView,
  parseGrantMutationResult,
  parseGrantPage,
  parseGrantPresetPage,
  parseIndexProfileCatalogPage,
  parsePublicRemoteCreation,
  parsePublicRemoteIntent,
  parsePublicRemoteValidation,
  parseRegisteredRemoteCatalogPage,
  parseWorkspaceCatalogPage,
  parseWorkspaceFolderPage,
  parseWorkspaceFolderValidation,
  parseWorkspaceRegistration,
} from '../models/source-control-v1-governance.model';
import {
  SourceControlMutationGuard,
  SourceControlV1HttpError,
} from './source-control-v1-api.client';

const BASE_PATH = '/api/source-control/v1';

export interface SourceControlNotebookOutput {
  readonly output_type: 'stream' | 'text' | 'error';
  readonly text: string;
}

export interface SourceControlNotebookCell {
  readonly cell_type: 'markdown' | 'code';
  readonly source: string;
  readonly outputs: readonly SourceControlNotebookOutput[];
}

export interface SourceControlNotebookDocument {
  readonly cells: readonly SourceControlNotebookCell[];
}

interface SourceControlContentAdmissionBase {
  readonly project_id: string;
  readonly display_name: string;
  readonly sensitivity: string;
}

export type SourceControlContentAdmissionRequest =
  | (SourceControlContentAdmissionBase & {
      readonly source_type: 'direct_text';
      readonly content: string;
      readonly media_type: 'text/plain' | 'text/markdown';
    })
  | (SourceControlContentAdmissionBase & {
      readonly source_type: 'notebook';
      readonly notebook: SourceControlNotebookDocument;
    });

export interface SourceControlCatalogQuery {
  readonly cursor?: string;
  readonly limit?: number;
  readonly q?: string;
}

export interface SourceControlRegisteredRemoteQuery
  extends SourceControlCatalogQuery {
  readonly kind?: 'git' | 'github';
  readonly state?: string;
}

export interface SourceControlIndexProfileQuery
  extends SourceControlCatalogQuery {
  readonly source?: string;
}

export interface SourceControlGrantPresetQuery
  extends SourceControlCatalogQuery {
  readonly operation?: string;
  readonly transformation?: string;
}

export interface SourceControlGrantQuery extends SourceControlCatalogQuery {
  readonly state?: string;
  readonly source_revision_id?: string;
  readonly destination_id?: string;
}

export interface SourceControlGrantCreateRequest {
  readonly source_revision_id: string;
  readonly destination_id: string;
  readonly policy_id: string;
  readonly preset_id: string;
  readonly duration_seconds: number;
}

export interface SourceControlGitAuthorizationSelection {
  readonly authorization_handle: string;
  readonly authorization_kind: SourceControlGitAuthorizationKind;
  readonly repository: string | null;
}

export interface SourceControlGitAuthorizationQuery {
  readonly cursor?: string;
  readonly limit?: number;
}

@Injectable({ providedIn: 'root' })
export class SourceControlV1GovernanceApiClient {
  private readonly http = inject(HttpClient);

  validateContentAdmission(
    request: SourceControlContentAdmissionRequest,
  ): Observable<SourceControlContentAdmissionValidation> {
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/content-admissions/validate`,
          this.contentPayload(request, true),
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseContentAdmissionValidation),
          ),
        ),
    );
  }

  createContentAdmission(
    request: SourceControlContentAdmissionRequest,
    idempotencyKey: string,
  ): Observable<SourceControlContentAdmissionCreation> {
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/content-admissions`,
          this.contentPayload(request, false),
          { headers: this.idempotencyHeaders(idempotencyKey) },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseContentAdmissionCreation),
          ),
        ),
    );
  }

  validatePublicRemote(
    projectId: string,
    intent: SourceControlPublicRemoteIntent,
  ): Observable<SourceControlPublicRemoteValidation> {
    const params = this.projectParams(projectId);
    const payload = parsePublicRemoteIntent(intent);
    return this.handle(
      this.http
        .post<unknown>(`${BASE_PATH}/public-remotes/validate`, payload, {
          params,
        })
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parsePublicRemoteValidation),
          ),
          map((result) => {
            if (
              result.provider !== payload.provider
              || result.requested_ref !== payload.requested_ref
            ) {
              throw new SourceControlV1ContractError(
                'public_remote_validation_intent_mismatch',
              );
            }
            return result;
          }),
        ),
    );
  }

  createPublicRemote(
    projectId: string,
    validationHandle: string,
    idempotencyKey: string,
  ): Observable<SourceControlPublicRemoteCreation> {
    const params = this.projectParams(projectId);
    assertSourceControlOpaqueId(validationHandle, 'validation_handle');
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/public-remotes`,
          { validation_handle: validationHandle },
          {
            params,
            headers: this.idempotencyHeaders(idempotencyKey),
          },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parsePublicRemoteCreation),
          ),
        ),
    );
  }

  listWorkspaceFolders(
    projectId: string,
  ): Observable<SourceControlWorkspaceFolderPage> {
    return this.envelopedGet(
      `${BASE_PATH}/workspace-folders`,
      this.projectParams(projectId),
      parseWorkspaceFolderPage,
    );
  }

  validateWorkspaceFolder(
    projectId: string,
    folderHandle: string,
  ): Observable<SourceControlWorkspaceFolderValidation> {
    const params = this.projectParams(projectId);
    assertSourceControlOpaqueId(folderHandle, 'folder_handle');
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/workspace-folders/validate`,
          { folder_handle: folderHandle },
          { params },
        )
        .pipe(
          map((body) =>
            parseSourceControlEnvelope(body, parseWorkspaceFolderValidation),
          ),
        ),
    );
  }

  createWorkspaceRegistration(
    projectId: string,
    validationHandle: string,
    idempotencyKey: string,
  ): Observable<SourceControlWorkspaceRegistration> {
    const params = this.projectParams(projectId);
    assertSourceControlOpaqueId(validationHandle, 'validation_handle');
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/workspaces`,
          { validation_handle: validationHandle },
          {
            params,
            headers: this.idempotencyHeaders(idempotencyKey),
            observe: 'response',
          },
        )
        .pipe(map((response) => this.workspaceRegistration(response))),
    );
  }

  listWorkspaces(
    projectId: string,
    query: SourceControlCatalogQuery = {},
  ): Observable<SourceControlWorkspaceCatalogPage> {
    return this.envelopedGet(
      `${BASE_PATH}/workspaces`,
      this.catalogParams(projectId, query),
      parseWorkspaceCatalogPage,
    );
  }

  listRegisteredRemotes(
    projectId: string,
    query: SourceControlRegisteredRemoteQuery = {},
  ): Observable<SourceControlRegisteredRemoteCatalogPage> {
    let params = this.catalogParams(projectId, query);
    if (query.kind !== undefined) params = params.set('kind', query.kind);
    if (query.state !== undefined) {
      assertSourceControlOpaqueId(query.state, 'state');
      params = params.set('state', query.state);
    }
    return this.envelopedGet(
      `${BASE_PATH}/registered-remotes`,
      params,
      parseRegisteredRemoteCatalogPage,
    );
  }

  gitAuthorizationHealth(): Observable<SourceControlGitAuthorizationHealth> {
    return this.http
      .get<unknown>(`${BASE_PATH}/git-authorizations/health`)
      .pipe(
        map((body) =>
          parseSourceControlEnvelope(body, parseGitAuthorizationHealth),
        ),
        catchError((error: unknown) => {
          if (error instanceof HttpErrorResponse && error.status === 503) {
            try {
              return of(
                parseSourceControlEnvelope(
                  error.error,
                  parseGitAuthorizationHealth,
                ),
              );
            } catch {
              // A malformed 503 body remains a fail-closed contract error.
            }
          }
          return throwError(() => this.toClientError(error));
        }),
      );
  }

  validateGitAuthorization(
    selection: SourceControlGitAuthorizationSelection,
  ): Observable<SourceControlGitAuthorizationView> {
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/git-authorizations/validate`,
          this.gitAuthorizationSelection(selection),
        )
        .pipe(
          map((body) => {
            const result = parseSourceControlEnvelope(
              body,
              parseGitAuthorizationView,
            );
            if (result.persisted) {
              throw new SourceControlV1ContractError(
                'git_authorization_validation_persisted',
              );
            }
            return result;
          }),
        ),
    );
  }

  provisionGitAuthorization(
    selection: SourceControlGitAuthorizationSelection,
    idempotencyKey: string,
  ): Observable<SourceControlGitAuthorizationView> {
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/git-authorizations`,
          this.gitAuthorizationSelection(selection),
          {
            headers: this.idempotencyHeaders(idempotencyKey),
            observe: 'response',
          },
        )
        .pipe(
          map((response) =>
            this.persistedGitAuthorization(response, 'provision'),
          ),
        ),
    );
  }

  listGitAuthorizations(
    query: SourceControlGitAuthorizationQuery = {},
  ): Observable<SourceControlGitAuthorizationPage> {
    return this.envelopedGet(
      `${BASE_PATH}/git-authorizations`,
      this.gitAuthorizationQueryParams(query),
      parseGitAuthorizationPage,
    );
  }

  gitAuthorizationDetail(
    authorizationRef: string,
    repository?: string | null,
  ): Observable<SourceControlGitAuthorizationView> {
    return this.handle(
      this.http
        .get<unknown>(
          `${BASE_PATH}/git-authorizations/${this.pathId(
            authorizationRef,
            'authorization_ref',
          )}`,
          {
            params: this.gitRepositoryParams(repository),
            observe: 'response',
          },
        )
        .pipe(
          map((response) =>
            this.persistedGitAuthorization(response, 'detail'),
          ),
        ),
    );
  }

  revokeGitAuthorization(
    authorizationRef: string,
    repository: string | null,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlGitAuthorizationView> {
    return this.gitAuthorizationTransition(
      authorizationRef,
      'revoke',
      repository,
      guard,
    );
  }

  recordGitAuthorizationScopeLoss(
    authorizationRef: string,
    repository: string | null,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlGitAuthorizationView> {
    return this.gitAuthorizationTransition(
      authorizationRef,
      'scope-loss',
      repository,
      guard,
    );
  }

  listIndexProfiles(
    projectId: string,
    query: SourceControlIndexProfileQuery = {},
  ): Observable<SourceControlIndexProfileCatalogPage> {
    let params = this.catalogParams(projectId, query);
    if (query.source !== undefined) {
      assertSourceControlOpaqueId(query.source, 'source');
      params = params.set('source', query.source);
    }
    return this.envelopedGet(
      `${BASE_PATH}/index-profiles`,
      params,
      parseIndexProfileCatalogPage,
    );
  }

  listGrantPresets(
    projectId: string,
    query: SourceControlGrantPresetQuery = {},
  ): Observable<SourceControlGrantPresetPage> {
    let params = this.catalogParams(projectId, query);
    for (const key of ['operation', 'transformation'] as const) {
      const value = query[key];
      if (value !== undefined) {
        assertSourceControlOpaqueId(value, key);
        params = params.set(key, value);
      }
    }
    return this.envelopedGet(
      `${BASE_PATH}/grant-presets`,
      params,
      parseGrantPresetPage,
    );
  }

  listGrants(
    projectId: string,
    query: SourceControlGrantQuery = {},
  ): Observable<SourceControlGrantPage> {
    let params = this.catalogParams(projectId, query);
    for (const key of [
      'state',
      'source_revision_id',
      'destination_id',
    ] as const) {
      const value = query[key];
      if (value !== undefined) {
        assertSourceControlOpaqueId(value, key);
        params = params.set(key, value);
      }
    }
    return this.envelopedGet(
      `${BASE_PATH}/grants`,
      params,
      parseGrantPage,
    );
  }

  createGrant(
    projectId: string,
    request: SourceControlGrantCreateRequest,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlGrantMutationResult> {
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/grants`,
          this.grantCreatePayload(request),
          {
            params: this.projectParams(projectId),
            headers: this.mutationHeaders(guard),
            observe: 'response',
          },
        )
        .pipe(map((response) => this.grantMutation(response))),
    );
  }

  revokeGrant(
    projectId: string,
    grantId: string,
    reasonCode: string,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlGrantMutationResult> {
    const id = this.pathId(grantId, 'grant_id');
    assertSourceControlOpaqueId(reasonCode, 'reason_code');
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/grants/${id}/actions/revoke`,
          { reason_code: reasonCode },
          {
            params: this.projectParams(projectId),
            headers: this.mutationHeaders(guard),
            observe: 'response',
          },
        )
        .pipe(map((response) => this.grantMutation(response))),
    );
  }

  private contentPayload(
    request: SourceControlContentAdmissionRequest,
    dryRun: boolean,
  ): Record<string, unknown> {
    assertSourceControlOpaqueId(request.project_id, 'project_id');
    assertSourceControlOpaqueId(request.sensitivity, 'sensitivity');
    const displayName = this.text(
      request.display_name,
      'display_name',
      200,
    );
    if (request.source_type === 'direct_text') {
      const content = this.text(
        request.content,
        'content',
        16 * 1024 * 1024,
        false,
      );
      if (
        request.media_type !== 'text/plain'
        && request.media_type !== 'text/markdown'
      ) {
        throw new SourceControlV1ContractError('media_type_invalid');
      }
      return {
        project_id: request.project_id,
        source_type: 'direct_text',
        display_name: displayName,
        sensitivity: request.sensitivity,
        content,
        media_type: request.media_type,
        dry_run: dryRun,
      };
    }
    if (
      !request.notebook
      || !Array.isArray(request.notebook.cells)
      || request.notebook.cells.length === 0
    ) {
      throw new SourceControlV1ContractError('notebook_invalid');
    }
    return {
      project_id: request.project_id,
      source_type: 'notebook',
      display_name: displayName,
      sensitivity: request.sensitivity,
      notebook: request.notebook,
      dry_run: dryRun,
    };
  }

  private grantCreatePayload(
    request: SourceControlGrantCreateRequest,
  ): SourceControlGrantCreateRequest {
    for (const key of [
      'source_revision_id',
      'destination_id',
      'policy_id',
      'preset_id',
    ] as const) {
      assertSourceControlOpaqueId(request[key], key);
    }
    if (
      !Number.isInteger(request.duration_seconds)
      || request.duration_seconds < 60
    ) {
      throw new SourceControlV1ContractError('duration_seconds_invalid');
    }
    return { ...request };
  }

  private catalogParams(
    projectId: string,
    query: SourceControlCatalogQuery,
  ): HttpParams {
    let params = this.projectParams(projectId);
    if (query.cursor !== undefined) {
      if (!/^[A-Za-z0-9_-]{1,512}$/.test(query.cursor)) {
        throw new SourceControlV1ContractError('cursor_invalid');
      }
      params = params.set('cursor', query.cursor);
    }
    if (query.limit !== undefined) {
      if (
        !Number.isInteger(query.limit)
        || query.limit < 1
        || query.limit > 200
      ) throw new SourceControlV1ContractError('limit_invalid');
      params = params.set('limit', query.limit);
    }
    if (query.q !== undefined) {
      params = params.set('q', this.text(query.q, 'q', 256));
    }
    return params;
  }

  private projectParams(projectId: string): HttpParams {
    assertSourceControlOpaqueId(projectId, 'project_id');
    return new HttpParams().set('project_id', projectId);
  }

  private grantMutation(
    response: HttpResponse<unknown>,
  ): SourceControlGrantMutationResult {
    const result = parseSourceControlEnvelope(
      response.body,
      parseGrantMutationResult,
    );
    const rawEtag = response.headers.get('ETag');
    if (rawEtag === null) {
      throw new SourceControlV1ContractError('etag_header_required');
    }
    const etag = rawEtag.trim().replace(/^"|"$/g, '');
    assertSourceControlSha256(etag, 'etag_header');
    if (result.grant.etag !== etag) {
      throw new SourceControlV1ContractError('etag_header_mismatch');
    }
    return result;
  }

  private workspaceRegistration(
    response: HttpResponse<unknown>,
  ): SourceControlWorkspaceRegistration {
    const result = parseSourceControlEnvelope(
      response.body,
      parseWorkspaceRegistration,
    );
    const headerEtag = response.headers.get('ETag')?.trim();
    if (headerEtag === undefined) {
      throw new SourceControlV1ContractError('etag_header_required');
    }
    assertSourceControlWorkspaceEtag(headerEtag, 'etag_header');
    if (headerEtag !== result.etag) {
      throw new SourceControlV1ContractError('etag_header_mismatch');
    }
    return result;
  }

  private gitAuthorizationSelection(
    selection: SourceControlGitAuthorizationSelection,
  ): SourceControlGitAuthorizationSelection {
    assertSourceControlOpaqueId(
      selection.authorization_handle,
      'authorization_handle',
    );
    const repository = this.gitRepository(
      selection.repository,
      selection.authorization_kind,
    );
    return {
      authorization_handle: selection.authorization_handle,
      authorization_kind: selection.authorization_kind,
      repository,
    };
  }

  private gitRepository(
    repository: string | null,
    kind?: SourceControlGitAuthorizationKind,
  ): string | null {
    if (kind === 'generic_git') {
      if (repository !== null) {
        throw new SourceControlV1ContractError(
          'git_authorization_repository_forbidden',
        );
      }
      return null;
    }
    if (kind !== undefined && kind !== 'github_app' && kind !== 'github_oauth') {
      throw new SourceControlV1ContractError(
        'git_authorization_kind_invalid',
      );
    }
    if (repository === null) {
      if (kind !== undefined) {
        throw new SourceControlV1ContractError(
          'git_authorization_repository_required',
        );
      }
      return null;
    }
    const normalized = String(repository ?? '').trim();
    if (
      !/^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})\/[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$/.test(
        normalized,
      )
    ) {
      throw new SourceControlV1ContractError(
        'git_authorization_repository_invalid',
      );
    }
    return normalized;
  }

  private gitAuthorizationQueryParams(
    query: SourceControlGitAuthorizationQuery,
  ): HttpParams {
    let params = new HttpParams();
    if (query.cursor !== undefined) {
      if (!/^[A-Za-z0-9_-]{1,512}$/.test(query.cursor)) {
        throw new SourceControlV1ContractError('cursor_invalid');
      }
      params = params.set('cursor', query.cursor);
    }
    if (query.limit !== undefined) {
      if (
        !Number.isInteger(query.limit)
        || query.limit < 1
        || query.limit > 200
      ) throw new SourceControlV1ContractError('limit_invalid');
      params = params.set('limit', query.limit);
    }
    return params;
  }

  private gitRepositoryParams(repository?: string | null): HttpParams {
    if (repository === undefined || repository === null) {
      return new HttpParams();
    }
    return new HttpParams().set(
      'repository',
      this.gitRepository(repository) as string,
    );
  }

  private gitAuthorizationTransition(
    authorizationRef: string,
    action: 'revoke' | 'scope-loss',
    repository: string | null,
    guard: SourceControlMutationGuard,
  ): Observable<SourceControlGitAuthorizationView> {
    const id = this.pathId(authorizationRef, 'authorization_ref');
    return this.handle(
      this.http
        .post<unknown>(
          `${BASE_PATH}/git-authorizations/${id}/actions/${action}`,
          { repository: this.gitRepository(repository) },
          {
            headers: this.gitAuthorizationMutationHeaders(guard),
            observe: 'response',
          },
        )
        .pipe(
          map((response) =>
            this.persistedGitAuthorization(response, action),
          ),
        ),
    );
  }

  private persistedGitAuthorization(
    response: HttpResponse<unknown>,
    operation: string,
  ): SourceControlGitAuthorizationView {
    const result = parseSourceControlEnvelope(
      response.body,
      parseGitAuthorizationView,
    );
    if (!result.persisted || result.etag === null) {
      throw new SourceControlV1ContractError(
        `git_authorization_${operation}_not_persisted`,
      );
    }
    const headerEtag = response.headers.get('ETag')?.trim();
    if (headerEtag === undefined) {
      throw new SourceControlV1ContractError('etag_header_required');
    }
    assertSourceControlGitAuthorizationEtag(headerEtag, 'etag_header');
    if (headerEtag !== result.etag) {
      throw new SourceControlV1ContractError('etag_header_mismatch');
    }
    return result;
  }

  private mutationHeaders(guard: SourceControlMutationGuard): HttpHeaders {
    assertSourceControlEtag(guard.etag, 'if_match');
    assertSourceControlIdempotencyKey(
      guard.idempotencyKey,
      'idempotency_key',
    );
    return new HttpHeaders({
      'If-Match': `"${guard.etag}"`,
      'Idempotency-Key': guard.idempotencyKey,
    });
  }

  private gitAuthorizationMutationHeaders(
    guard: SourceControlMutationGuard,
  ): HttpHeaders {
    assertSourceControlGitAuthorizationEtag(guard.etag, 'if_match');
    assertSourceControlIdempotencyKey(
      guard.idempotencyKey,
      'idempotency_key',
    );
    return new HttpHeaders({
      'If-Match': guard.etag,
      'Idempotency-Key': guard.idempotencyKey,
    });
  }

  private idempotencyHeaders(value: string): HttpHeaders {
    assertSourceControlIdempotencyKey(value, 'idempotency_key');
    return new HttpHeaders({ 'Idempotency-Key': value });
  }

  private pathId(value: string, name: string): string {
    assertSourceControlOpaqueId(value, name);
    return encodeURIComponent(value);
  }

  private text(
    value: string,
    name: string,
    maximum: number,
    trim = true,
  ): string {
    const normalized = trim ? String(value ?? '').trim() : String(value ?? '');
    if (!normalized.trim() || normalized.length > maximum) {
      throw new SourceControlV1ContractError(`${name}_invalid`);
    }
    return normalized;
  }

  private envelopedGet<T>(
    path: string,
    params: HttpParams,
    parser: (value: unknown) => T,
  ): Observable<T> {
    return this.handle(
      this.http
        .get<unknown>(path, { params })
        .pipe(map((body) => parseSourceControlEnvelope(body, parser))),
    );
  }

  private handle<T>(request: Observable<T>): Observable<T> {
    return request.pipe(
      catchError((error: unknown) =>
        throwError(() => this.toClientError(error)),
      ),
    );
  }

  private toClientError(error: unknown): Error {
    if (error instanceof SourceControlV1ContractError) return error;
    if (!(error instanceof HttpErrorResponse)) {
      return new SourceControlV1HttpError(0, 'transport_error');
    }
    try {
      const envelope = parseSourceControlErrorEnvelope(error.error);
      return new SourceControlV1HttpError(
        error.status,
        envelope.error.code,
      );
    } catch {
      return new SourceControlV1HttpError(
        error.status,
        'invalid_error_contract',
      );
    }
  }
}
