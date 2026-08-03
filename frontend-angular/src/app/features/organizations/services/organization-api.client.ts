import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { ApiBaseService } from '../../../services/api-base.service';
import {
  OrganizationAssignmentCandidate,
  OrganizationAdmissionExceptionRequest,
  OrganizationAdmissionExceptionResult,
  OrganizationBlueprintSummary,
  OrganizationBundlePreview,
  OrganizationBundleGrant,
  OrganizationDefinitionGraphBundle,
  OrganizationCompilePlan,
  OrganizationCompileRequest,
  OrganizationInstantiationGrant,
  OrganizationInstantiateRequest,
  OrganizationInstantiateResult,
  OrganizationLayoutPreference,
  OrganizationPatchOperation,
  OrganizationPatchPreview,
  OrganizationPlanningReadModel,
  OrganizationRoleSlot,
  OrganizationSummary,
  OrganizationTopologyPage,
  OrganizationTopologyPatchGrant,
  OrganizationTopologyQuery,
} from '../models/organization-topology.models';

export interface OrganizationPage<T> {
  items: readonly T[];
  next_cursor: string | null;
}

@Injectable({ providedIn: 'root' })
export class OrganizationApiClient extends ApiBaseService {
  listBlueprints(
    hubUrl: string,
    projectId: string,
  ): Observable<OrganizationPage<OrganizationBlueprintSummary>> {
    const query = new URLSearchParams({
      project_id: projectId,
      page_size: '100',
    });
    return this.core.get<OrganizationPage<OrganizationBlueprintSummary>>(
      this.endpoint(hubUrl, `/api/organization-blueprints?${query.toString()}`),
      hubUrl,
      undefined,
      false,
    );
  }

  compileBlueprint(
    hubUrl: string,
    projectId: string,
    request: OrganizationCompileRequest,
  ): Observable<OrganizationCompilePlan> {
    return this.core.request<OrganizationCompilePlan>(
      'POST',
      this.endpoint(hubUrl, `/api/organization-blueprints/${encodeURIComponent(request.blueprint_key)}/compile`),
      hubUrl,
      { body: { ...request, project_id: projectId }, timeoutMs: 30_000 },
    );
  }

  issueAdmissionException(
    hubUrl: string,
    projectId: string,
    blueprintKey: string,
    request: OrganizationAdmissionExceptionRequest,
    idempotencyKey: string,
  ): Observable<OrganizationAdmissionExceptionResult> {
    return this.core.request<OrganizationAdmissionExceptionResult>(
      'POST',
      this.endpoint(
        hubUrl,
        `/api/organization-blueprints/${encodeURIComponent(blueprintKey)}/admission-exceptions`,
      ),
      hubUrl,
      {
        body: { ...request, project_id: projectId },
        headers: { 'Idempotency-Key': idempotencyKey },
        timeoutMs: 30_000,
      },
    );
  }

  issueInstantiationGrant(
    hubUrl: string,
    projectId: string,
    plan: OrganizationCompilePlan,
    idempotencyKey: string,
    ttlSeconds = 900,
  ): Observable<OrganizationInstantiationGrant> {
    return this.core.request<OrganizationInstantiationGrant>(
      'POST',
      this.endpoint(
        hubUrl,
        `/api/organization-blueprints/${encodeURIComponent(plan.blueprint_key)}/precreation-admin-grants`,
      ),
      hubUrl,
      {
        body: {
          compile_plan: plan,
          project_id: projectId,
          ttl_seconds: clamp(ttlSeconds, 60, 3600),
        },
        headers: {
          'Idempotency-Key': idempotencyKey,
          'If-Match': quoteEtag(plan.definition_revision),
        },
        timeoutMs: 30_000,
      },
    );
  }

  listOrganizations(
    hubUrl: string,
    projectId: string,
    cursor = '',
    pageSize = 50,
  ): Observable<OrganizationPage<OrganizationSummary>> {
    const query = new URLSearchParams({
      project_id: projectId,
      page_size: String(clamp(pageSize, 1, 100)),
    });
    if (cursor) query.set('cursor', cursor);
    return this.core.get<OrganizationPage<OrganizationSummary>>(
      this.endpoint(hubUrl, `/api/organizations?${query.toString()}`),
      hubUrl,
      undefined,
      false,
    );
  }

  instantiate(
    hubUrl: string,
    projectId: string,
    request: OrganizationInstantiateRequest,
    idempotencyKey: string,
  ): Observable<OrganizationInstantiateResult> {
    const { admin_grant: adminGrant, compile_plan: compilePlan, title } = request;
    return this.core.request<OrganizationInstantiateResult>(
      'POST',
      this.endpoint(hubUrl, '/api/organizations'),
      hubUrl,
      {
        body: { compile_plan: compilePlan, title, project_id: projectId },
        headers: {
          'Idempotency-Key': idempotencyKey,
          'If-Match': quoteEtag(compilePlan.definition_revision),
          'X-Organization-Admin-Grant': adminGrant,
          'X-Plan-Digest': compilePlan.plan_digest,
        },
        timeoutMs: 30_000,
      },
    );
  }

  topology(
    hubUrl: string,
    organizationId: string,
    query: OrganizationTopologyQuery,
  ): Observable<OrganizationTopologyPage> {
    const params = new URLSearchParams();
    if (query.cursor) params.set('cursor', query.cursor);
    if (query.page_size) params.set('page_size', String(clamp(query.page_size, 1, 100)));
    if (query.depth !== undefined) params.set('depth', String(Math.max(1, Math.trunc(query.depth))));
    if (query.subgraph_root_id) params.set('subgraph_root_id', query.subgraph_root_id);
    if (query.kinds?.length) params.set('kinds', query.kinds.join(','));
    if (query.edge_namespaces?.length) params.set('edge_namespaces', query.edge_namespaces.join(','));
    if (query.search) params.set('search', query.search);
    params.set('include_runtime', query.include_runtime === false ? 'false' : 'true');
    return this.core.get<OrganizationTopologyPage>(
      this.endpoint(
        hubUrl,
        `/api/organizations/${encodeURIComponent(organizationId)}/topology?${params.toString()}`,
      ),
      hubUrl,
      undefined,
      false,
    );
  }

  previewPatch(
    hubUrl: string,
    organizationId: string,
    revision: string,
    operations: readonly OrganizationPatchOperation[],
  ): Observable<OrganizationPatchPreview> {
    return this.core.request<OrganizationPatchPreview>(
      'POST',
      this.endpoint(hubUrl, `/api/organizations/${encodeURIComponent(organizationId)}/patches/preview`),
      hubUrl,
      {
        body: { expected_revision: revision, operations },
        headers: { 'If-Match': quoteEtag(revision) },
      },
    );
  }

  applyPatch(
    hubUrl: string,
    organizationId: string,
    preview: OrganizationPatchPreview,
    topologyPatchGrant: string,
    idempotencyKey: string,
  ): Observable<OrganizationTopologyPage> {
    return this.core.request<OrganizationTopologyPage>(
      'POST',
      this.endpoint(hubUrl, `/api/organizations/${encodeURIComponent(organizationId)}/patches/apply`),
      hubUrl,
      {
        body: preview,
        headers: {
          'If-Match': quoteEtag(preview.expected_revision),
          'Idempotency-Key': idempotencyKey,
          'X-Topology-Patch-Grant': topologyPatchGrant,
          'X-Patch-Digest': preview.patch_digest,
          'X-Policy-Digest': preview.effective_policy_hash,
          'X-Limit-Digest': preview.effective_limit_profile_hash,
        },
      },
    );
  }

  issueTopologyPatchGrant(
    hubUrl: string,
    organizationId: string,
    preview: OrganizationPatchPreview,
    parentAdminGrant: string,
    idempotencyKey: string,
  ): Observable<OrganizationTopologyPatchGrant> {
    return this.core.request<OrganizationTopologyPatchGrant>(
      'POST',
      this.endpoint(hubUrl, `/api/organizations/${encodeURIComponent(organizationId)}/patches/grants`),
      hubUrl,
      {
        body: preview,
        headers: {
          'If-Match': quoteEtag(preview.expected_revision),
          'Idempotency-Key': idempotencyKey,
          'X-Organization-Admin-Grant': parentAdminGrant,
          'X-Patch-Digest': preview.patch_digest,
          'X-Policy-Digest': preview.effective_policy_hash,
          'X-Limit-Digest': preview.effective_limit_profile_hash,
        },
      },
    );
  }

  saveLayout(
    hubUrl: string,
    organizationId: string,
    preferences: readonly OrganizationLayoutPreference[],
  ): Observable<{ saved: number }> {
    return this.core.request<{ saved: number }>(
      'PUT',
      this.endpoint(hubUrl, `/api/organizations/${encodeURIComponent(organizationId)}/layout-preferences`),
      hubUrl,
      { body: { preferences } },
    );
  }

  roleSlots(hubUrl: string, organizationId: string): Observable<readonly OrganizationRoleSlot[]> {
    return this.core.get<readonly OrganizationRoleSlot[]>(
      this.endpoint(hubUrl, `/api/organizations/${encodeURIComponent(organizationId)}/role-slots`),
      hubUrl,
      undefined,
      false,
    );
  }

  assignmentCandidates(
    hubUrl: string,
    organizationId: string,
    roleSlotId: string,
  ): Observable<readonly OrganizationAssignmentCandidate[]> {
    return this.core.get<readonly OrganizationAssignmentCandidate[]>(
      this.endpoint(
        hubUrl,
        `/api/organizations/${encodeURIComponent(organizationId)}/role-slots/${encodeURIComponent(roleSlotId)}/assignment-candidates`,
      ),
      hubUrl,
      undefined,
      false,
    );
  }

  previewBundle(
    hubUrl: string,
    projectId: string,
    bundle: unknown,
    conflictStrategy: string,
    assignmentRebindings: Readonly<Record<string, string>> = {},
    instanceAdmissionExceptionRefs: Readonly<Record<string, string>> = {},
  ): Observable<OrganizationBundlePreview> {
    return this.core.request<OrganizationBundlePreview>(
      'POST',
      this.endpoint(hubUrl, '/api/organization-bundles/import-preview'),
      hubUrl,
      {
        body: {
          bundle,
          project_id: projectId,
          conflict_strategy: conflictStrategy,
          assignment_rebindings: assignmentRebindings,
          instance_admission_exception_refs: instanceAdmissionExceptionRefs,
        },
        timeoutMs: 30_000,
      },
    );
  }

  issueBundleGrant(
    hubUrl: string,
    preview: OrganizationBundlePreview,
    idempotencyKey: string,
  ): Observable<OrganizationBundleGrant> {
    return this.core.request<OrganizationBundleGrant>(
      'POST',
      this.endpoint(hubUrl, '/api/organization-bundles/import-grants'),
      hubUrl,
      {
        body: preview,
        headers: {
          'If-Match': quoteEtag(preview.import_plan.expected_target_revision),
          'Idempotency-Key': idempotencyKey,
          'X-Import-Plan-Digest': preview.plan_digest,
        },
        timeoutMs: 30_000,
      },
    );
  }

  applyBundle(
    hubUrl: string,
    preview: OrganizationBundlePreview,
    adminGrant: string,
    idempotencyKey: string,
  ): Observable<{ imported: Readonly<Record<string, number>>; replayed: boolean }> {
    return this.core.request<{ imported: Readonly<Record<string, number>>; replayed: boolean }>(
      'POST',
      this.endpoint(hubUrl, '/api/organization-bundles/import-apply'),
      hubUrl,
      {
        body: preview,
        headers: {
          'If-Match': quoteEtag(preview.import_plan.expected_target_revision),
          'Idempotency-Key': idempotencyKey,
          'X-Organization-Admin-Grant': adminGrant,
          'X-Import-Plan-Digest': preview.plan_digest,
        },
        timeoutMs: 30_000,
      },
    );
  }

  exportBundle(
    hubUrl: string,
    organizationId: string,
    includeInstances = false,
    includeAssignments = false,
  ): Observable<OrganizationDefinitionGraphBundle> {
    const query = new URLSearchParams({
      organization_id: organizationId,
      include_instances: String(includeInstances),
      include_assignments: String(includeAssignments),
    });
    return this.core.get<OrganizationDefinitionGraphBundle>(
      this.endpoint(hubUrl, `/api/organization-bundles/export?${query.toString()}`),
      hubUrl,
      undefined,
      false,
    );
  }

  planning(
    hubUrl: string,
    organizationId: string,
    cursor = '',
  ): Observable<OrganizationPlanningReadModel> {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
    return this.core.get<OrganizationPlanningReadModel>(
      this.endpoint(hubUrl, `/api/organizations/${encodeURIComponent(organizationId)}/planning${query}`),
      hubUrl,
      undefined,
      false,
    );
  }

  decideProposal(
    hubUrl: string,
    organizationId: string,
    proposalId: string,
    operation: 'approve' | 'reject',
    revision: string,
    digest: string,
  ): Observable<OrganizationPlanningReadModel> {
    return this.core.request<OrganizationPlanningReadModel>(
      'POST',
      this.endpoint(
        hubUrl,
        `/api/organizations/${encodeURIComponent(organizationId)}/proposals/${encodeURIComponent(proposalId)}/${operation}`,
      ),
      hubUrl,
      {
        body: { expected_revision: revision, expected_digest: digest },
        headers: { 'If-Match': quoteEtag(revision) },
      },
    );
  }

  transitionPlanningArtifact(
    hubUrl: string,
    organizationId: string,
    nodeId: string,
    operation: 'promote' | 'adopt',
    revision: string,
    digest: string,
  ): Observable<OrganizationPlanningReadModel> {
    return this.core.request<OrganizationPlanningReadModel>(
      'POST',
      this.endpoint(
        hubUrl,
        `/api/organizations/${encodeURIComponent(organizationId)}/planning/${encodeURIComponent(nodeId)}/${operation}`,
      ),
      hubUrl,
      {
        body: { expected_revision: revision, expected_digest: digest },
        headers: { 'If-Match': quoteEtag(revision) },
      },
    );
  }

  private endpoint(hubUrl: string, path: string): string {
    return `${hubUrl.replace(/\/+$/, '')}${path}`;
  }
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, Math.trunc(value)));
}

function quoteEtag(value: string): string {
  const normalized = String(value || '').replace(/^W\//, '').replace(/^"|"$/g, '');
  return `"${normalized}"`;
}
