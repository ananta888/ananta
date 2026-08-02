import { DestroyRef, Injectable, computed, effect, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { finalize, forkJoin } from 'rxjs';
import type { Observable } from 'rxjs';
import type { SourceControlIndexAccessResult } from '../../models/source-control-index-access.model';
import {
  SourceControlJobEvent,
  SourceControlIndexRecord,
  SourceControlJsonObject,
  SourceControlNextAction,
  SourceControlProjection,
} from '../../models/source-control-v1-api.model';
import { SourceControlV1ApiClient } from '../../services/source-control-v1-api.client';
import { SourceControlV1GovernanceApiClient } from '../../services/source-control-v1-governance-api.client';
import { ProjectContextService } from '../../services/project-context.service';

export interface SourceDetailView {
  sourceId: string;
  displayName: string;
  sourceType: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  metadata: Record<string, unknown>;
}

export interface SourceRevisionView {
  snapshotId: string;
  status: string;
  contentHash: string;
  createdAt: string;
  metadata: Record<string, unknown>;
}

export interface SourceIndexView {
  indexId: string;
  etag: string | null;
  status: string;
  createdAt: string;
  updatedAt: string;
  coveragePercent: number | null;
  stale: boolean | null;
  metadata: Record<string, unknown>;
}

export interface SourceGrantView {
  grantId: string;
  grantFamilyId: string;
  version: number;
  sourceRevisionId: string;
  destinationId: string;
  presetId: string | null;
  operation: string;
  transformation: string;
  purpose: string;
  policyVersion: string;
  state: string;
  issuedAt: string;
  expiresAt: string;
  expired: boolean;
  etag: string;
}

export interface SourceGrantPresetView {
  presetId: string;
  label: string;
  description: string;
  operation: string;
  transformation: string;
  purpose: string;
  maxDurationSeconds: number;
}

export interface SourceIndexProfileView {
  profileId: string;
  label: string;
  description: string;
  isDefault: boolean;
}

export interface SourceGrantDraft {
  destinationId: string;
  policyId: string;
  presetId: string;
  durationSeconds: number;
  policyEtag: string;
}

export interface SourceGraphNodeView {
  id: string;
  label: string;
  kind: string;
}

export interface SourceGraphEdgeView {
  source: string;
  target: string;
  kind: string;
}

export interface SourceDetailError {
  state: 'offline' | 'unauthorized' | 'forbidden' | 'not-found' | 'conflict' | 'unprocessable' | 'rate-limited' | 'server-error' | 'error';
  message: string;
}

const MAX_REVISIONS = 100;
const MAX_GRAPH_NODES = 100;
const MAX_GRAPH_EDGES = 200;

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function stringField(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
  }
  return '';
}

export function toSourceDetailError(error: unknown): SourceDetailError {
  const outer = recordOf(error);
  const status = Number(outer['status'] || 0);
  const nested = recordOf(outer['error']);
  const message = stringField(nested, 'message', 'detail') || stringField(outer, 'message');
  if (status === 0) return { state: 'offline', message: 'Der Hub ist nicht erreichbar.' };
  if (status === 401) return { state: 'unauthorized', message: 'Der Hub verlangt eine gültige Anmeldung.' };
  if (status === 403) return { state: 'forbidden', message: 'Der Hub erlaubt den Zugriff auf diese Daten nicht.' };
  if (status === 404) return { state: 'not-found', message: 'Die Quelle oder Fähigkeit wurde nicht gefunden.' };
  if (status === 409) return { state: 'conflict', message: 'Der serverseitige Quellenstand hat sich geändert. Bitte neu laden.' };
  if (status === 422) return { state: 'unprocessable', message: 'Der Hub kann den Quellenvertrag nicht verarbeiten.' };
  if (status === 429) return { state: 'rate-limited', message: 'Der Hub begrenzt die Anfrage. Bitte später erneut versuchen.' };
  if (status >= 500) return { state: 'server-error', message: 'Der Hub konnte die Anfrage nicht verarbeiten.' };
  return { state: 'error', message: message || 'Die Hub-Anfrage ist fehlgeschlagen.' };
}

function operationReceiptError(
  operation: 'refresh' | 'scan' | 'disable',
  value: unknown,
): SourceDetailError | null {
  if (operation === 'disable') return null;
  const receipt = recordOf(recordOf(value)['receipt']);
  const status = stringField(receipt, 'status').toLowerCase();
  if (!['failed', 'rejected', 'error', 'cancelled'].includes(status)) return null;
  const reasonCode = stringField(receipt, 'reason_code', 'code') || 'operation_failed';
  const label = operation === 'refresh' ? 'Die Quellenaktualisierung' : 'Der sichere Scan';
  return {
    state: 'unprocessable',
    message: `${label} ist fehlgeschlagen (${reasonCode}).`,
  };
}

@Injectable({ providedIn: 'root' })
export class SourceDetailFacade {
  private readonly sourceControlApi = inject(SourceControlV1ApiClient);
  private readonly governanceApi = inject(SourceControlV1GovernanceApiClient);
  private readonly projectContext = inject(ProjectContextService);
  private readonly destroyRef = inject(DestroyRef);

  private readonly connectionId = signal('');
  private readonly requestProjectId = signal('');
  private readonly projectId = signal('');
  private readonly sourceRevisionId = signal('');
  readonly revisionId = this.sourceRevisionId.asReadonly();
  private readonly etag = signal('');
  private readonly nextActions = signal<readonly string[]>([]);
  private readonly activeIndexId = signal<string | null>(null);
  private readonly activeIndexGeneration = signal(0);
  private readonly projectionStale = signal<boolean | null>(null);
  readonly source = signal<SourceDetailView | null>(null);
  readonly revisions = signal<readonly SourceRevisionView[]>([]);
  readonly runs = signal<readonly SourceIndexView[]>([]);
  readonly grants = signal<readonly SourceGrantView[]>([]);
  readonly grantPresets = signal<readonly SourceGrantPresetView[]>([]);
  readonly indexProfiles = signal<readonly SourceIndexProfileView[]>([]);
  readonly auditEvents = signal<readonly SourceControlJobEvent[]>([]);
  readonly index = signal<SourceIndexView | null>(null);
  readonly graphNodes = signal<readonly SourceGraphNodeView[]>([]);
  readonly graphEdges = signal<readonly SourceGraphEdgeView[]>([]);
  readonly revisionsTruncated = signal(false);
  readonly runsTruncated = signal(false);
  readonly graphTruncated = signal(false);
  readonly graphTextAlternative = signal('');
  readonly artifactStatus = signal<string | SourceControlJsonObject | null>(null);
  readonly sourceError = signal<SourceDetailError | null>(null);
  readonly revisionsError = signal<SourceDetailError | null>(null);
  readonly indexError = signal<SourceDetailError | null>(null);
  readonly graphError = signal<SourceDetailError | null>(null);
  readonly auditError = signal<SourceDetailError | null>(null);
  readonly mutationError = signal<SourceDetailError | null>(null);
  readonly governanceError = signal<SourceDetailError | null>(null);
  readonly lifecycleMessage = signal('');
  readonly pending = signal(0);
  readonly graphLoading = signal(false);
  readonly auditLoading = signal(false);
  readonly mutationLoading = signal(false);
  readonly governanceLoading = signal(false);
  readonly loading = computed(() => this.pending() > 0);
  readonly active = computed<boolean | null>(() =>
    this.index() ? this.activeIndexId() === this.index()?.indexId : null,
  );
  readonly stale = computed(() => this.projectionStale());
  readonly coveragePercent = computed(() => this.index()?.coveragePercent ?? null);
  readonly projectScopeValid = computed(() => this.hasActiveProjectScope());
  readonly nextAction = computed(() => {
    const source = this.source();
    if (!source) return 'Quelle prüfen';
    const action = this.nextActions()[0];
    if (!action) return 'Keine serverseitig erlaubte Aktion verfügbar';
    if (action === 'refresh') return 'Quelle aktualisieren';
    if (action === 'scan') return 'Quelle sicher scannen';
    if (action === 'index') return 'Indexlauf starten';
    if (action === 'activate') return 'Index aktivieren';
    if (action === 'grant') return 'Zugriff prüfen';
    if (action === 'disable') return 'Quelle deaktivieren';
    if (action === 'rollback') return 'Auf früheren Index zurückrollen';
    if (this.graphNodes().length === 0) return 'CodeCompass-Graph lesen';
    return 'Keine serverseitig erlaubte Aktion verfügbar';
  });

  constructor() {
    effect(() => {
      const selectedProjectId = this.selectedProjectId();
      const requestedProjectId = this.requestProjectId();
      const projectedProjectId = this.projectId();
      if (
        (requestedProjectId && selectedProjectId !== requestedProjectId) ||
        (projectedProjectId && selectedProjectId !== projectedProjectId)
      ) {
        this.invalidateProjectScope(
          'Der Projektkontext wurde gewechselt. Die Quelldetails wurden sicher verworfen.',
        );
      }
    });
  }

  load(sourceId: string): void {
    const normalizedId = String(sourceId || '').trim();
    const selectedProjectId = this.selectedProjectId();
    this.reset();
    if (!selectedProjectId) {
      this.sourceError.set({
        state: 'forbidden',
        message: 'Ohne aktiven Projektkontext bleiben Quelldetails gesperrt.',
      });
      return;
    }
    if (!normalizedId) {
      this.sourceError.set({ state: 'not-found', message: 'Es wurde keine Source-ID angegeben.' });
      return;
    }
    this.connectionId.set(normalizedId);
    this.requestProjectId.set(selectedProjectId);
    this.loadSource(normalizedId, selectedProjectId);
    this.loadIndex(normalizedId, selectedProjectId);
  }

  loadGraph(): void {
    const connectionId = this.connectionId();
    if (!connectionId || !this.hasActiveProjectScope() || this.graphLoading()) return;
    this.graphLoading.set(true);
    this.graphError.set(null);
    this.sourceControlApi.loadGraph(connectionId, {
      limit: MAX_GRAPH_NODES,
    }).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.graphLoading.set(false)),
    ).subscribe({
      next: (graph) => {
        const raw = recordOf(graph);
        this.graphTextAlternative.set(
          typeof graph.text_alternative === 'string'
            ? graph.text_alternative
            : '',
        );
        this.artifactStatus.set(graph.artifact_status);
        const rawNodes = Array.isArray(raw['nodes']) ? raw['nodes'] : [];
        const rawEdges = Array.isArray(raw['edges']) ? raw['edges'] : [];
        this.graphTruncated.set(rawNodes.length > MAX_GRAPH_NODES || rawEdges.length > MAX_GRAPH_EDGES);
        this.graphNodes.set(rawNodes.slice(0, MAX_GRAPH_NODES).flatMap((item) => {
          const node = recordOf(item);
          const id = stringField(node, 'id', 'node_id');
          if (!id) return [];
          return [{
            id,
            label: stringField(node, 'label', 'name', 'qualified_name', 'id') || id,
            kind: stringField(node, 'kind', 'type', 'node_type') || 'unbekannt',
          }];
        }));
        this.graphEdges.set(rawEdges.slice(0, MAX_GRAPH_EDGES).flatMap((item) => {
          const edge = recordOf(item);
          const source = stringField(edge, 'source', 'source_id', 'from');
          const target = stringField(edge, 'target', 'target_id', 'to');
          if (!source || !target) return [];
          return [{
            source,
            target,
            kind: stringField(edge, 'kind', 'type', 'edge_type') || 'unbekannt',
          }];
        }));
      },
      error: (error) => this.graphError.set(toSourceDetailError(error)),
    });
  }

  loadAudit(): void {
    const connectionId = this.connectionId();
    if (!connectionId || !this.hasActiveProjectScope() || this.auditLoading()) return;
    this.auditLoading.set(true);
    this.auditError.set(null);
    this.sourceControlApi.listEvents({ after_sequence: 0, limit: 500 }).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.auditLoading.set(false)),
    ).subscribe({
      next: (page) => this.auditEvents.set(
        page.events.filter((event) => event.resource_id === connectionId),
      ),
      error: (error) => this.auditError.set(toSourceDetailError(error)),
    });
  }

  can(action: SourceControlNextAction): boolean {
    return this.hasActiveProjectScope() && this.nextActions().includes(action);
  }

  refresh(): void {
    this.mutateConnection('refresh');
  }

  scan(): void {
    this.mutateConnection('scan');
  }

  disable(): void {
    this.mutateConnection('disable');
  }

  private loadSource(sourceId: string, expectedProjectId: string): void {
    this.begin();
    this.sourceControlApi.getConnection(sourceId).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.end()),
    ).subscribe({
      next: ({ projection, etag }) => {
        if (!this.matchesActiveRequest(sourceId, expectedProjectId)) return;
        const projectionProjectId = String(projection.connection.project_id || '').trim();
        if (!projectionProjectId || projectionProjectId !== expectedProjectId) {
          this.invalidateProjectScope(
            'Die Hub-Projektion gehört nicht zum aktiven Projekt. Die Antwort wurde verworfen.',
          );
          return;
        }
        this.etag.set(etag);
        this.nextActions.set(projection.next_actions);
        this.projectConnection(projection);
        this.loadGovernance();
      },
      error: (error) => this.sourceError.set(toSourceDetailError(error)),
    });
  }

  private loadIndex(sourceId: string, expectedProjectId: string): void {
    this.begin();
    this.sourceControlApi.listRuns(sourceId, { limit: MAX_REVISIONS }).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.end()),
    ).subscribe({
      next: (page) => {
        if (!this.matchesActiveRequest(sourceId, expectedProjectId)) return;
        this.runsTruncated.set(page.next_cursor !== null);
        this.activeIndexId.set(page.active?.knowledge_index_id ?? null);
        this.activeIndexGeneration.set(page.active?.generation ?? 0);
        this.runs.set(page.items.map((item) => this.indexView(item)));
        const match = page.items[page.items.length - 1];
        if (match === undefined) {
          this.index.set(null);
          return;
        }
        const metadata = recordOf(match);
        const coverage = recordOf(match['coverage']);
        const rawCoverage = Number(coverage['percent']);
        const coveragePercent = Number.isFinite(rawCoverage) && rawCoverage >= 0 && rawCoverage <= 100
          ? rawCoverage
          : null;
        this.index.set({
          indexId: match.knowledge_index_id,
          etag: stringField(metadata, 'etag') || null,
          status: match.status,
          createdAt: stringField(metadata, 'created_at', 'completed_at'),
          updatedAt: stringField(metadata, 'updated_at', 'completed_at'),
          coveragePercent,
          stale: this.projectionStale(),
          metadata,
        });
      },
      error: (error) => this.indexError.set(toSourceDetailError(error)),
    });
  }

  private projectConnection(projection: SourceControlProjection): void {
    const projectionRecord = recordOf(projection);
    const connection = recordOf(projection.connection);
    const revision = projection.revision;
    const revisionRecord = recordOf(revision);
    this.projectionStale.set(projection.stale);
    this.projectId.set(
      stringField(connection, 'project_id') ||
        stringField(projectionRecord, 'project_id'),
    );
    this.sourceRevisionId.set(stringField(revisionRecord, 'source_revision_id'));
    this.source.set({
      sourceId: projection.connection_id,
      displayName: stringField(connection, 'display_name') || projection.connection_id,
      sourceType: stringField(connection, 'connector_type') || 'unbekannt',
      status: stringField(connection, 'state') || 'unbekannt',
      createdAt: stringField(connection, 'created_at'),
      updatedAt: stringField(connection, 'updated_at'),
      metadata: connection,
    });
    if (this.revisions().length === 0) {
      this.revisions.set(revision === null ? [] : [{
        snapshotId: stringField(revisionRecord, 'source_revision_id'),
        status: stringField(recordOf(projection.admission), 'state') || 'unbekannt',
        contentHash: stringField(revisionRecord, 'revision_digest'),
        createdAt: stringField(revisionRecord, 'captured_at'),
        metadata: revisionRecord,
      }]);
    }
  }

  private indexView(record: SourceControlIndexRecord): SourceIndexView {
    const raw = recordOf(record);
    const coverage = recordOf(record['coverage']);
    const rawCoverage = Number(coverage['percent']);
    return {
      indexId: record.knowledge_index_id,
      etag: stringField(raw, 'etag') || null,
      status: record.status,
      createdAt: stringField(raw, 'created_at', 'completed_at'),
      updatedAt: stringField(raw, 'updated_at', 'completed_at'),
      coveragePercent:
        Number.isFinite(rawCoverage) && rawCoverage >= 0 && rawCoverage <= 100
          ? rawCoverage
          : null,
      stale: this.projectionStale(),
      metadata: raw,
    };
  }

  startIndex(
    profileId: string,
    accessAuthorization?: SourceControlIndexAccessResult,
  ): void {
    const connectionId = this.connectionId();
    const connectionEtag = this.etag();
    const profile = this.indexProfiles().find(
      (item) => item.profileId === String(profileId || '').trim(),
    );
    if (
      !connectionId ||
      !connectionEtag ||
      !profile ||
      (
        !this.can('index')
        && !this.isIndexAccessAuthorizationValid(
          connectionId,
          accessAuthorization,
        )
      ) ||
      this.mutationLoading()
    ) {
      this.mutationError.set({
        state: 'conflict',
        message:
          'Ein Indexlauf erfordert ein serverseitiges Profil, den aktuellen Connection-ETag und die Hub-Aktion index oder einen passenden einmaligen Indexzugriffs-Grant.',
      });
      return;
    }
    this.runIndexMutation(
      this.sourceControlApi.startIndexRun(
        connectionId,
        profile.profileId,
        this.guard(connectionEtag, 'index:start'),
      ),
      'Indexlauf wurde serverseitig gestartet.',
    );
  }

  private isIndexAccessAuthorizationValid(
    connectionId: string,
    authorization?: SourceControlIndexAccessResult,
  ): boolean {
    return Boolean(
      authorization?.access_ready === true
        && authorization.connection_id === connectionId
        && authorization.source_revision_id === this.sourceRevisionId()
        && authorization.effect.provider_location === 'local'
        && authorization.effect.transformation === 'redacted'
        && authorization.effect.one_time === true
        && authorization.grant.state === 'active'
        && authorization.next_actions.length === 1
        && authorization.next_actions[0] === 'start_index_run',
    );
  }

  activateIndex(indexId: string): void {
    const run = this.serverRun(indexId);
    if (!run || !this.can('activate') || this.mutationLoading()) {
      this.mutationError.set({
        state: 'conflict',
        message:
          'Nur ein servergelieferter Index mit ETag und erlaubter activate-Aktion kann aktiviert werden.',
      });
      return;
    }
    this.runIndexMutation(
      this.sourceControlApi.activateIndex(
        run.indexId,
        this.guard(this.activePointerEtag(), 'index:activate'),
      ),
      'Index wurde serverseitig aktiviert.',
    );
  }

  rollbackIndex(indexId: string): void {
    const run = this.serverRun(indexId);
    if (!run || !this.can('rollback') || this.mutationLoading()) {
      this.mutationError.set({
        state: 'conflict',
        message:
          'Nur ein servergelieferter Index mit ETag und erlaubter rollback-Aktion kann verwendet werden.',
      });
      return;
    }
    this.runIndexMutation(
      this.sourceControlApi.rollbackIndex(
        run.indexId,
        this.guard(this.activePointerEtag(), 'index:rollback'),
      ),
      'Rollback wurde serverseitig angefordert.',
    );
  }

  private serverRun(indexId: string): SourceIndexView | undefined {
    const normalizedId = String(indexId || '').trim();
    return this.runs().find((run) => run.indexId === normalizedId);
  }

  private activePointerEtag(): string {
    return `active:${this.activeIndexGeneration()}`;
  }

  private guard(etag: string, operation: string) {
    return {
      etag,
      idempotencyKey: `ui:${operation}:${crypto.randomUUID()}`,
    };
  }

  private runIndexMutation(request: Observable<unknown>, message: string): void {
    const connectionId = this.connectionId();
    const expectedProjectId = this.requestProjectId();
    if (!this.hasActiveProjectScope()) {
      this.mutationError.set({
        state: 'forbidden',
        message: 'Die Indexaktion wurde wegen eines ungültigen Projektkontexts blockiert.',
      });
      return;
    }
    this.mutationLoading.set(true);
    this.mutationError.set(null);
    this.lifecycleMessage.set('');
    request.pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.mutationLoading.set(false)),
    ).subscribe({
      next: () => {
        if (!this.matchesActiveRequest(connectionId, expectedProjectId)) return;
        this.lifecycleMessage.set(message);
        if (connectionId) {
          this.loadSource(connectionId, expectedProjectId);
          this.loadIndex(connectionId, expectedProjectId);
        }
      },
      error: (error) => this.mutationError.set(toSourceDetailError(error)),
    });
  }

  createGrant(draft: SourceGrantDraft): void {
    const projectId = this.projectId();
    const sourceRevisionId = this.sourceRevisionId();
    const destinationId = String(draft.destinationId || '').trim();
    const policyId = String(draft.policyId || '').trim();
    const policyEtag = String(draft.policyEtag || '').trim();
    const preset = this.grantPresets().find(
      (item) => item.presetId === String(draft.presetId || '').trim(),
    );
    const durationSeconds = Number(draft.durationSeconds);
    if (
      !projectId ||
      !sourceRevisionId ||
      !destinationId ||
      !policyId ||
      !policyEtag ||
      !preset ||
      !Number.isInteger(durationSeconds) ||
      durationSeconds <= 0 ||
      durationSeconds > preset.maxDurationSeconds ||
      this.governanceLoading()
    ) {
      this.governanceError.set({
        state: 'unprocessable',
        message:
          'Grant-Ziel, Policy, serverseitiges Preset, Laufzeit und Policy-ETag muessen gueltig sein.',
      });
      return;
    }

    this.runGovernanceMutation(
      this.governanceApi.createGrant(
        projectId,
        {
          source_revision_id: sourceRevisionId,
          destination_id: destinationId,
          policy_id: policyId,
          preset_id: preset.presetId,
          duration_seconds: durationSeconds,
        },
        {
          etag: policyEtag,
          idempotencyKey: `ui:grant:create:${crypto.randomUUID()}`,
        },
      ),
      'Grant wurde serverseitig ausgestellt.',
    );
  }

  revokeGrant(grantId: string, reasonCode = 'operator_revoked'): void {
    const projectId = this.projectId();
    const normalizedGrantId = String(grantId || '').trim();
    const grant = this.grants().find(
      (item) => item.grantId === normalizedGrantId && item.state === 'active',
    );
    if (!projectId || !grant || !grant.etag || this.governanceLoading()) {
      this.governanceError.set({
        state: 'conflict',
        message: 'Nur ein aktueller, aktiver Server-Grant kann widerrufen werden.',
      });
      return;
    }

    this.runGovernanceMutation(
      this.governanceApi.revokeGrant(
        projectId,
        grant.grantId,
        reasonCode,
        {
          etag: grant.etag,
          idempotencyKey: `ui:grant:revoke:${crypto.randomUUID()}`,
        },
      ),
      'Grant wurde serverseitig widerrufen.',
    );
  }

  private loadGovernance(): void {
    const projectId = this.projectId();
    if (!projectId || !this.hasActiveProjectScope() || this.governanceLoading()) return;
    this.governanceLoading.set(true);
    this.governanceError.set(null);
    forkJoin({
      profiles: this.governanceApi.listIndexProfiles(projectId),
      presets: this.governanceApi.listGrantPresets(projectId),
      grants: this.governanceApi.listGrants(projectId),
    }).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.governanceLoading.set(false)),
    ).subscribe({
      next: ({ profiles, presets, grants }) => {
        this.indexProfiles.set(profiles.items.map((profile) => ({
          profileId: profile.profile_id,
          label: profile.label,
          description: profile.description,
          isDefault: profile.is_default,
        })));
        this.grantPresets.set(presets.items.map((preset) => ({
          presetId: preset.preset_id,
          label: preset.label,
          description: preset.description,
          operation: preset.operation,
          transformation: preset.transformation,
          purpose: preset.purpose,
          maxDurationSeconds: preset.max_duration_seconds,
        })));
        this.grants.set(grants.items.map((grant) => ({
          grantId: grant.grant_id,
          grantFamilyId: grant.grant_family_id,
          version: grant.version,
          sourceRevisionId: grant.source_revision_id,
          destinationId: grant.destination_id,
          presetId: grant.preset_id,
          operation: grant.operation,
          transformation: grant.transformation,
          purpose: grant.purpose,
          policyVersion: grant.policy_version,
          state: grant.state,
          issuedAt: grant.issued_at,
          expiresAt: grant.expires_at,
          expired: grant.expired,
          etag: grant.etag,
        })));
      },
      error: (error) => this.governanceError.set(toSourceDetailError(error)),
    });
  }

  private runGovernanceMutation(request: Observable<unknown>, message: string): void {
    const expectedProjectId = this.requestProjectId();
    if (!this.hasActiveProjectScope()) {
      this.governanceError.set({
        state: 'forbidden',
        message: 'Die Governance-Aktion wurde wegen eines ungültigen Projektkontexts blockiert.',
      });
      return;
    }
    this.governanceLoading.set(true);
    this.governanceError.set(null);
    this.lifecycleMessage.set('');
    let succeeded = false;
    request.pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => {
        this.governanceLoading.set(false);
        if (succeeded) this.loadGovernance();
      }),
    ).subscribe({
      next: () => {
        if (!this.hasActiveProjectScope(expectedProjectId)) return;
        succeeded = true;
        this.lifecycleMessage.set(message);
      },
      error: (error) => this.governanceError.set(toSourceDetailError(error)),
    });
  }

  private mutateConnection(
    operation: 'refresh' | 'scan' | 'disable',
  ): void {
    const connectionId = this.connectionId();
    const etag = this.etag();
    if (
      !connectionId ||
      !etag ||
      !this.hasActiveProjectScope() ||
      !this.can(operation) ||
      this.mutationLoading()
    ) return;
    this.mutationLoading.set(true);
    this.mutationError.set(null);
    const guard = {
      etag,
      idempotencyKey: `ui:${crypto.randomUUID()}`,
    };
    const request: Observable<unknown> = operation === 'refresh'
      ? this.sourceControlApi.refreshConnection(connectionId, guard)
      : operation === 'scan'
        ? this.sourceControlApi.scanConnection(connectionId, guard)
        : this.sourceControlApi.disableConnection(connectionId, guard);
    request.pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => this.mutationLoading.set(false)),
    ).subscribe({
      next: (result) => {
        const receiptError = operationReceiptError(operation, result);
        if (receiptError) {
          this.mutationError.set(receiptError);
          return;
        }
        if (this.hasActiveProjectScope()) this.load(connectionId);
      },
      error: (error) => this.mutationError.set(toSourceDetailError(error)),
    });
  }

  private reset(): void {
    this.source.set(null);
    this.revisions.set([]);
    this.runs.set([]);
    this.grants.set([]);
    this.grantPresets.set([]);
    this.indexProfiles.set([]);
    this.auditEvents.set([]);
    this.index.set(null);
    this.graphNodes.set([]);
    this.graphEdges.set([]);
    this.revisionsTruncated.set(false);
    this.runsTruncated.set(false);
    this.graphTruncated.set(false);
    this.graphTextAlternative.set('');
    this.artifactStatus.set(null);
    this.sourceError.set(null);
    this.revisionsError.set(null);
    this.indexError.set(null);
    this.graphError.set(null);
    this.auditError.set(null);
    this.mutationError.set(null);
    this.governanceError.set(null);
    this.lifecycleMessage.set('');
    this.connectionId.set('');
    this.requestProjectId.set('');
    this.projectId.set('');
    this.sourceRevisionId.set('');
    this.etag.set('');
    this.nextActions.set([]);
    this.activeIndexId.set(null);
    this.activeIndexGeneration.set(0);
    this.projectionStale.set(null);
  }

  private begin(): void {
    this.pending.update((value) => value + 1);
  }

  private end(): void {
    this.pending.update((value) => Math.max(0, value - 1));
  }

  private selectedProjectId(): string {
    return String(this.projectContext.selectedProjectId() || '').trim();
  }

  private hasActiveProjectScope(expectedProjectId = this.requestProjectId()): boolean {
    const selectedProjectId = this.selectedProjectId();
    const projectedProjectId = this.projectId();
    return Boolean(
      selectedProjectId &&
        expectedProjectId &&
        selectedProjectId === expectedProjectId &&
        (!projectedProjectId || projectedProjectId === expectedProjectId),
    );
  }

  private matchesActiveRequest(sourceId: string, expectedProjectId: string): boolean {
    return this.connectionId() === sourceId && this.hasActiveProjectScope(expectedProjectId);
  }

  private invalidateProjectScope(message: string): void {
    this.reset();
    this.sourceError.set({ state: 'forbidden', message });
    this.indexError.set({ state: 'forbidden', message });
  }
}
