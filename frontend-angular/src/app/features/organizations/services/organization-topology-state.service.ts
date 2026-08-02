import { computed, inject, Injectable, signal } from '@angular/core';
import { finalize, forkJoin, switchMap, throwError } from 'rxjs';

import { AgentDirectoryService, normalizeHubOrigin } from '../../../services/agent-directory.service';
import {
  OrganizationBlueprintSummary,
  OrganizationCompilePlan,
  OrganizationCompileRequest,
  OrganizationEdgeNamespace,
  OrganizationInstantiateRequest,
  OrganizationLayoutPreference,
  OrganizationNodeKind,
  OrganizationPatchOperation,
  OrganizationPatchPreview,
  OrganizationPlanningReadModel,
  OrganizationRuntimeNodeOverlay,
  OrganizationSummary,
  OrganizationTopologyEdge,
  OrganizationTopologyNode,
  OrganizationTopologyPage,
  OrganizationTopologyPatchGrant,
  OrganizationViewMode,
} from '../models/organization-topology.models';
import { OrganizationApiClient, OrganizationPage } from './organization-api.client';

const DEFAULT_PAGE_SIZE = 100;
const DEFAULT_DEPTH = 3;

@Injectable()
export class OrganizationTopologyStateService {
  private readonly api = inject(OrganizationApiClient);
  private readonly directory = inject(AgentDirectoryService);

  readonly hubUrl = signal('');
  readonly blueprints = signal<readonly OrganizationBlueprintSummary[]>([]);
  readonly organizations = signal<readonly OrganizationSummary[]>([]);
  readonly selectedOrganizationId = signal<string | null>(null);
  readonly topology = signal<OrganizationTopologyPage | null>(null);
  readonly mode = signal<OrganizationViewMode>('hierarchy');
  readonly selectedNodeId = signal<string | null>(null);
  readonly selectedEdgeId = signal<string | null>(null);
  readonly focusedSubgraphId = signal<string | null>(null);
  readonly search = signal('');
  readonly nodeKinds = signal<readonly OrganizationNodeKind[]>([]);
  readonly edgeNamespaces = signal<readonly OrganizationEdgeNamespace[]>([
    'hierarchy',
    'organization',
    'runtime',
  ]);
  readonly inspectorOpen = signal(true);
  readonly loading = signal(false);
  readonly mutating = signal(false);
  readonly loaded = signal(false);
  readonly error = signal('');
  readonly errorReasonCode = signal('');
  readonly compilePlan = signal<OrganizationCompilePlan | null>(null);
  readonly patchPreview = signal<OrganizationPatchPreview | null>(null);
  readonly topologyPatchGrant = signal<OrganizationTopologyPatchGrant | null>(null);
  readonly planning = signal<OrganizationPlanningReadModel | null>(null);
  readonly layoutPreferences = signal<ReadonlyMap<string, OrganizationLayoutPreference>>(new Map());
  readonly organizationAdminGrants = signal<ReadonlyMap<string, string>>(new Map());
  private readonly topologyPatchGrantIssueKey = signal('');
  private readonly topologyPatchApplyKey = signal('');

  readonly selectedOrganization = computed(() => this.organizations().find(
    organization => organization.id === this.selectedOrganizationId(),
  ) ?? null);
  readonly selectedOrganizationAdminGrant = computed(() => {
    const organizationId = this.selectedOrganizationId();
    return organizationId ? this.organizationAdminGrants().get(organizationId) ?? '' : '';
  });
  readonly selectedNode = computed(() => this.topology()?.nodes.find(
    node => node.id === this.selectedNodeId(),
  ) ?? null);
  readonly selectedEdge = computed(() => {
    const page = this.topology();
    const selectedId = this.selectedEdgeId();
    if (!page || !selectedId) return null;
    return [
      ...page.edges,
      ...(page.runtime_overlay?.edges ?? []),
    ].find(edge => edge.id === selectedId) ?? null;
  });
  readonly selectedRuntime = computed<OrganizationRuntimeNodeOverlay | null>(() => {
    const selectedId = this.selectedNodeId();
    return this.topology()?.runtime_overlay?.nodes.find(item => item.node_id === selectedId) ?? null;
  });
  readonly visibleNodes = computed(() => {
    const page = this.topology();
    if (!page) return [];
    const search = this.search().trim().toLocaleLowerCase();
    const kinds = new Set(this.nodeKinds());
    return page.nodes.filter(node => (
      (!search || node.label.toLocaleLowerCase().includes(search) || node.stable_key.toLocaleLowerCase().includes(search))
      && (kinds.size === 0 || kinds.has(node.kind))
    ));
  });
  readonly visibleEdges = computed(() => {
    const page = this.topology();
    if (!page) return [];
    const nodeIds = new Set(this.visibleNodes().map(node => node.id));
    const namespaces = new Set(this.edgeNamespaces());
    return [
      ...page.edges,
      ...(page.runtime_overlay?.edges ?? []),
    ].filter(edge => (
      namespaces.has(edge.namespace)
      && nodeIds.has(edge.source_id)
      && nodeIds.has(edge.target_id)
    ));
  });
  readonly revisionMismatch = computed(() => {
    const page = this.topology();
    const overlay = page?.runtime_overlay;
    return Boolean(overlay && overlay.definition_revision !== page?.definition_revision);
  });
  readonly canLoadMore = computed(() => Boolean(this.topology()?.next_cursor));
  readonly statusText = computed(() => {
    if (this.loading()) return 'Organisation wird geladen';
    if (this.error()) return this.error();
    const page = this.topology();
    if (!page) return 'Keine Organisation ausgewählt';
    const truncation = page.truncated ? ', serverseitig begrenzt' : '';
    return `${page.nodes.length} Knoten und ${page.edges.length} Definitionskanten${truncation}`;
  });

  constructor() {
    this.resolveHub();
  }

  initialize(): void {
    const hubUrl = this.resolveHub();
    if (!hubUrl || this.loading()) {
      if (!hubUrl) this.captureError(new Error('organization_hub_unconfigured'), 'Kein Hub konfiguriert.');
      return;
    }
    this.resetError();
    this.loading.set(true);
    forkJoin({
      blueprints: this.api.listBlueprints(hubUrl),
      organizations: this.api.listOrganizations(hubUrl, '', 100),
    }).pipe(finalize(() => this.loading.set(false))).subscribe({
      next: ({ blueprints, organizations }) => {
        this.blueprints.set(normalizePage(blueprints).items.filter(item => !item.test_only));
        const instances = normalizePage(organizations).items;
        this.organizations.set(instances);
        this.loaded.set(true);
        const selected = this.selectedOrganizationId();
        if (selected && instances.some(item => item.id === selected)) {
          this.loadTopology(selected);
        } else if (instances.length) {
          this.selectOrganization(instances[0].id);
        }
      },
      error: error => this.captureError(error, 'Organisationskatalog konnte nicht geladen werden.'),
    });
  }

  selectOrganization(organizationId: string): void {
    const normalized = normalizeId(organizationId);
    if (!normalized) return;
    const changed = normalized !== this.selectedOrganizationId();
    this.selectedOrganizationId.set(normalized);
    if (changed) {
      this.selectedNodeId.set(null);
      this.selectedEdgeId.set(null);
      this.focusedSubgraphId.set(null);
      this.patchPreview.set(null);
      this.topologyPatchGrant.set(null);
      this.resetTopologyPatchKeys();
      this.planning.set(null);
    }
    this.loadTopology(normalized);
  }

  setMode(mode: OrganizationViewMode): void {
    this.mode.set(mode);
  }

  selectNode(nodeId: string | null): void {
    this.selectedNodeId.set(nodeId ? normalizeId(nodeId) : null);
    this.selectedEdgeId.set(null);
    if (nodeId) this.inspectorOpen.set(true);
  }

  selectEdge(edgeId: string | null): void {
    this.selectedEdgeId.set(edgeId ? normalizeId(edgeId) : null);
    this.selectedNodeId.set(null);
    if (edgeId) this.inspectorOpen.set(true);
  }

  setFocus(nodeId: string | null): void {
    const normalized = nodeId ? normalizeId(nodeId) : null;
    this.focusedSubgraphId.set(normalized);
    const organizationId = this.selectedOrganizationId();
    if (organizationId) this.loadTopology(organizationId, '', normalized);
  }

  setSearch(value: string): void {
    this.search.set(String(value || '').slice(0, 128));
  }

  toggleNodeKind(kind: OrganizationNodeKind): void {
    this.nodeKinds.update(current => current.includes(kind)
      ? current.filter(item => item !== kind)
      : [...current, kind]);
  }

  toggleEdgeNamespace(namespace: OrganizationEdgeNamespace): void {
    this.edgeNamespaces.update(current => current.includes(namespace)
      ? current.filter(item => item !== namespace)
      : [...current, namespace]);
  }

  loadMore(): void {
    const page = this.topology();
    const organizationId = this.selectedOrganizationId();
    if (!page?.next_cursor || !organizationId || this.loading()) return;
    this.loadTopology(organizationId, page.next_cursor, this.focusedSubgraphId(), true);
  }

  loadChildren(nodeId: string): void {
    const organizationId = this.selectedOrganizationId();
    const normalized = normalizeId(nodeId);
    if (!organizationId || !normalized || this.loading()) return;
    this.loadTopology(organizationId, '', normalized, true);
  }

  loadPlanning(): void {
    const organizationId = this.selectedOrganizationId();
    const hubUrl = this.hubUrl();
    if (!organizationId || !hubUrl) return;
    this.loading.set(true);
    this.api.planning(hubUrl, organizationId).pipe(
      finalize(() => this.loading.set(false)),
    ).subscribe({
      next: planning => this.planning.set(planning),
      error: error => this.captureError(error, 'Planungslineage konnte nicht geladen werden.'),
    });
  }

  transitionPlanningArtifact(
    nodeId: string,
    operation: 'promote' | 'adopt',
    revision: string,
    digest: string,
  ): void {
    const hubUrl = this.hubUrl();
    const organizationId = this.selectedOrganizationId();
    if (!hubUrl || !organizationId || this.mutating()) return;
    this.resetError();
    this.mutating.set(true);
    this.api.transitionPlanningArtifact(hubUrl, organizationId, nodeId, operation, revision, digest).pipe(
      finalize(() => this.mutating.set(false)),
    ).subscribe({
      next: planning => this.planning.set(planning),
      error: error => this.captureError(error, 'Planungsartefakt ist stale oder konnte nicht freigegeben werden.'),
    });
  }

  decideProposal(
    proposalId: string,
    operation: 'approve' | 'reject',
    revision: string,
    digest: string,
  ): void {
    const hubUrl = this.hubUrl();
    const organizationId = this.selectedOrganizationId();
    if (!hubUrl || !organizationId || this.mutating()) return;
    this.resetError();
    this.mutating.set(true);
    this.api.decideProposal(hubUrl, organizationId, proposalId, operation, revision, digest).pipe(
      finalize(() => this.mutating.set(false)),
    ).subscribe({
      next: planning => this.planning.set(planning),
      error: error => this.captureError(error, 'Proposal-Entscheidung ist stale oder nicht erlaubt.'),
    });
  }

  compile(request: OrganizationCompileRequest): void {
    const hubUrl = this.hubUrl() || this.resolveHub();
    if (!hubUrl || this.mutating()) return;
    this.resetError();
    this.compilePlan.set(null);
    this.mutating.set(true);
    this.api.compileBlueprint(hubUrl, request).pipe(
      finalize(() => this.mutating.set(false)),
    ).subscribe({
      next: plan => this.compilePlan.set(plan),
      error: error => this.captureError(error, 'Dry-run konnte nicht erstellt werden.'),
    });
  }

  compileCustom(
    blueprintKey: string,
    blueprintVersion: string,
    title: string,
    teamBlueprintCounts: Readonly<Record<string, number>>,
    reason: string,
  ): void {
    const hubUrl = this.hubUrl() || this.resolveHub();
    if (!hubUrl || this.mutating()) return;
    this.resetError();
    this.compilePlan.set(null);
    this.mutating.set(true);
    this.api.issueAdmissionException(
      hubUrl,
      blueprintKey,
      {
        blueprint_version: blueprintVersion,
        team_blueprint_counts: teamBlueprintCounts,
        reason,
        ttl_seconds: 900,
      },
      createIdempotencyKey('organization-custom-admission'),
    ).pipe(
      switchMap(admission => {
        if (admission.status !== 'issued') {
          return throwError(() => new Error('organization_admission_exception_not_issued'));
        }
        return this.api.compileBlueprint(hubUrl, {
          blueprint_key: blueprintKey,
          blueprint_version: blueprintVersion,
          title,
          admission_exception_ref: admission.admission_exception_ref,
          parameters: { team_blueprint_counts: teamBlueprintCounts },
        });
      }),
      finalize(() => this.mutating.set(false)),
    ).subscribe({
      next: plan => this.compilePlan.set(plan),
      error: error => this.captureError(
        error,
        'Custom-N-Ausnahme oder Dry-run konnte nicht erstellt werden.',
      ),
    });
  }

  instantiate(adminGrant: string): void {
    const plan = this.compilePlan();
    const hubUrl = this.hubUrl();
    if (!plan || !hubUrl || !adminGrant.trim() || this.mutating()) return;
    const request: OrganizationInstantiateRequest = {
      compile_plan: plan,
      title: plan.title,
      admin_grant: adminGrant.trim(),
    };
    this.resetError();
    this.mutating.set(true);
    this.api.instantiate(hubUrl, request, createIdempotencyKey('organization-instantiate')).pipe(
      finalize(() => this.mutating.set(false)),
    ).subscribe({
      next: result => {
        this.organizationAdminGrants.update(current => {
          const next = new Map(current);
          next.set(result.organization.id, result.organization_admin_grant_id);
          return next;
        });
        this.organizations.update(current => [
          result.organization,
          ...current.filter(item => item.id !== result.organization.id),
        ]);
        this.compilePlan.set(null);
        this.selectOrganization(result.organization.id);
      },
      error: error => this.captureError(error, 'Organisation konnte nicht instanziiert werden.'),
    });
  }

  previewOperations(operations: readonly OrganizationPatchOperation[]): void {
    const hubUrl = this.hubUrl();
    const organizationId = this.selectedOrganizationId();
    const revision = this.topology()?.definition_revision;
    if (!hubUrl || !organizationId || !revision || !operations.length || this.mutating()) return;
    this.resetError();
    this.patchPreview.set(null);
    this.topologyPatchGrant.set(null);
    this.resetTopologyPatchKeys();
    this.mutating.set(true);
    this.api.previewPatch(hubUrl, organizationId, revision, operations).pipe(
      finalize(() => this.mutating.set(false)),
    ).subscribe({
      next: preview => {
        this.patchPreview.set(preview);
        this.topologyPatchGrant.set(null);
      },
      error: error => this.captureError(error, 'Änderungsplan konnte nicht validiert werden.'),
    });
  }

  issuePreviewGrant(parentAdminGrant: string): void {
    const hubUrl = this.hubUrl();
    const organizationId = this.selectedOrganizationId();
    const preview = this.patchPreview();
    if (!hubUrl || !organizationId || !preview?.applicable || !parentAdminGrant.trim() || this.mutating()) return;
    this.resetError();
    this.mutating.set(true);
    const issueKey = this.topologyPatchGrantIssueKey()
      || createIdempotencyKey('organization-patch-grant');
    this.topologyPatchGrantIssueKey.set(issueKey);
    this.api.issueTopologyPatchGrant(
      hubUrl,
      organizationId,
      preview,
      parentAdminGrant.trim(),
      issueKey,
    ).pipe(finalize(() => this.mutating.set(false))).subscribe({
      next: grant => {
        this.topologyPatchGrant.set(grant);
        this.topologyPatchApplyKey.set('');
      },
      error: error => this.captureError(error, 'Der one-shot Topologie-Grant konnte nicht gebunden werden.'),
    });
  }

  applyPreview(): void {
    const hubUrl = this.hubUrl();
    const organizationId = this.selectedOrganizationId();
    const preview = this.patchPreview();
    const grant = this.topologyPatchGrant();
    if (
      !hubUrl
      || !organizationId
      || !preview?.applicable
      || !grant
      || grant.patch_digest !== preview.patch_digest
      || this.mutating()
    ) return;
    this.resetError();
    this.mutating.set(true);
    const applyKey = this.topologyPatchApplyKey()
      || createIdempotencyKey('organization-patch-apply');
    this.topologyPatchApplyKey.set(applyKey);
    this.api.applyPatch(
      hubUrl,
      organizationId,
      preview,
      grant.grant_id,
      applyKey,
    ).pipe(finalize(() => this.mutating.set(false))).subscribe({
      next: page => {
        this.topology.set(page);
        this.patchPreview.set(null);
        this.topologyPatchGrant.set(null);
        this.resetTopologyPatchKeys();
      },
      error: error => this.captureError(error, 'Änderung konnte nicht angewendet werden; lokaler Zustand blieb erhalten.'),
    });
  }

  updateLayout(preference: OrganizationLayoutPreference): void {
    this.layoutPreferences.update(current => {
      const next = new Map(current);
      next.set(preference.node_id, preference);
      return next;
    });
  }

  saveLayout(): void {
    const hubUrl = this.hubUrl();
    const organizationId = this.selectedOrganizationId();
    if (!hubUrl || !organizationId || this.layoutPreferences().size === 0) return;
    this.api.saveLayout(hubUrl, organizationId, [...this.layoutPreferences().values()]).subscribe({
      error: error => this.captureError(error, 'Layout-Einstellungen konnten nicht gespeichert werden.'),
    });
  }

  resetError(): void {
    this.error.set('');
    this.errorReasonCode.set('');
  }

  private loadTopology(
    organizationId: string,
    cursor = '',
    subgraphRootId: string | null = this.focusedSubgraphId(),
    append = false,
  ): void {
    const hubUrl = this.hubUrl() || this.resolveHub();
    if (!hubUrl || this.loading()) return;
    this.resetError();
    this.loading.set(true);
    this.api.topology(hubUrl, organizationId, {
      cursor,
      page_size: this.topology()?.limits.max_page_size ?? DEFAULT_PAGE_SIZE,
      depth: DEFAULT_DEPTH,
      subgraph_root_id: subgraphRootId ?? undefined,
      include_runtime: true,
    }).pipe(finalize(() => this.loading.set(false))).subscribe({
      next: page => this.topology.set(append ? mergeTopologyPages(this.topology(), page) : page),
      error: error => this.captureError(error, 'Organisationstopologie konnte nicht geladen werden.'),
    });
  }

  private resolveHub(): string {
    const hub = this.directory.list().find(agent => agent.role === 'hub')
      ?? this.directory.list().find(agent => agent.name === 'hub');
    const normalized = normalizeHubOrigin(hub?.url || '');
    if (this.hubUrl() && this.hubUrl() !== (normalized ?? '')) {
      this.organizationAdminGrants.set(new Map());
      this.topologyPatchGrant.set(null);
      this.resetTopologyPatchKeys();
    }
    this.hubUrl.set(normalized ?? '');
    return normalized ?? '';
  }

  private captureError(error: unknown, fallback: string): void {
    const record = error as { status?: number; error?: { reason_code?: string; message?: string }; message?: string };
    this.errorReasonCode.set(record?.error?.reason_code || (record?.status ? `http_${record.status}` : 'organization_request_failed'));
    this.error.set(record?.error?.message || fallback);
  }

  private resetTopologyPatchKeys(): void {
    this.topologyPatchGrantIssueKey.set('');
    this.topologyPatchApplyKey.set('');
  }
}

function normalizePage<T>(value: OrganizationPage<T> | readonly T[]): OrganizationPage<T> {
  if (Array.isArray(value)) return { items: value, next_cursor: null };
  const page = value as OrganizationPage<T>;
  return {
    items: Array.isArray(page?.items) ? page.items : [],
    next_cursor: page?.next_cursor ?? null,
  };
}

function normalizeId(value: string): string | null {
  const normalized = String(value || '').trim();
  return /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(normalized) ? normalized : null;
}

function mergeTopologyPages(
  current: OrganizationTopologyPage | null,
  incoming: OrganizationTopologyPage,
): OrganizationTopologyPage {
  if (!current || current.definition_revision !== incoming.definition_revision) return incoming;
  const nodes = mergeById(current.nodes, incoming.nodes);
  const edges = mergeById(current.edges, incoming.edges);
  const runtimeNodes = mergeByKey(
    current.runtime_overlay?.nodes ?? [],
    incoming.runtime_overlay?.nodes ?? [],
    item => item.node_id,
  );
  const runtimeEdges = mergeById(
    current.runtime_overlay?.edges ?? [],
    incoming.runtime_overlay?.edges ?? [],
  );
  return {
    ...incoming,
    nodes,
    edges,
    diagnostics: [...current.diagnostics, ...incoming.diagnostics],
    runtime_overlay: incoming.runtime_overlay ? {
      ...incoming.runtime_overlay,
      nodes: runtimeNodes,
      edges: runtimeEdges,
    } : current.runtime_overlay,
  };
}

function mergeById<T extends { id: string }>(left: readonly T[], right: readonly T[]): readonly T[] {
  return mergeByKey(left, right, item => item.id);
}

function mergeByKey<T>(left: readonly T[], right: readonly T[], key: (item: T) => string): readonly T[] {
  const merged = new Map(left.map(item => [key(item), item]));
  right.forEach(item => merged.set(key(item), item));
  return [...merged.values()];
}

function createIdempotencyKey(prefix: string): string {
  const suffix = typeof globalThis.crypto?.randomUUID === 'function'
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}:${suffix}`;
}
