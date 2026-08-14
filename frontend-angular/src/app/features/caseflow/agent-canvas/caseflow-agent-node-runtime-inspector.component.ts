import { HttpErrorResponse } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  DestroyRef,
  EventEmitter,
  InjectionToken,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  inject,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Observable, Subject, catchError, map, of, switchMap } from 'rxjs';

import type {
  VpGraph,
  VpRuntimeOverlay,
} from '../../visual-process/visual-process-api.service';
import { CaseFlowEdgeTraceApiService } from './caseflow-edge-trace-api.service';
import { CaseFlowEdgeTraceListComponent } from './caseflow-edge-trace-list.component';
import type {
  CaseFlowEdgeTraceReadModel,
  CaseFlowEdgeTraceScope,
} from './caseflow-edge-trace.models';
import {
  type CaseFlowAgentNodeRelationKind,
  type CaseFlowAgentNodeRelationProjection,
  type CaseFlowAgentNodeRuntimeTraceProjection,
  projectCaseFlowAgentNodeRuntimeTrace,
} from './caseflow-agent-node-runtime.mapper';

export interface CaseFlowAgentNodeTraceReader {
  read(scope: Readonly<CaseFlowEdgeTraceScope>): Observable<CaseFlowEdgeTraceReadModel>;
}

export const CASEFLOW_AGENT_NODE_TRACE_READER = new InjectionToken<CaseFlowAgentNodeTraceReader>(
  'CASEFLOW_AGENT_NODE_TRACE_READER',
  {
    providedIn: 'root',
    factory: () => inject(CaseFlowEdgeTraceApiService),
  },
);

type NodeInspectorView = 'overview' | 'communication' | 'trace';

interface NodeLoadRequest {
  readonly generation: number;
  readonly graph: Readonly<VpGraph>;
  readonly selectedStepId: string;
  readonly workflowId: string;
  readonly runId: string;
  readonly runtimeOverlay: Readonly<VpRuntimeOverlay> | null;
}

interface NodeLoadResult {
  readonly generation: number;
  readonly projection: CaseFlowAgentNodeRuntimeTraceProjection | null;
  readonly errorCode: string | null;
  readonly accessRevoked: boolean;
}

let inspectorSequence = 0;

/** Thin read-only UI over the pure node runtime/trace projection. */
@Component({
  selector: 'app-caseflow-agent-node-runtime-inspector',
  standalone: true,
  imports: [CaseFlowEdgeTraceListComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <aside class="node-runtime-inspector" [attr.aria-labelledby]="titleId">
      <header class="inspector-header">
        <p class="eyebrow">Agent Runtime und Trace</p>
        <h2 [id]="titleId">{{ selectedStepLabel }}</h2>
        @if (workflowId && runId) {
          <p class="scope">Workflow <code>{{ workflowId }}</code> · Run <code>{{ runId }}</code></p>
        }
      </header>

      @if (closed) {
        <p class="state error" role="alert">
          Der Zugriff wurde entzogen. Runtime- und Trace-Daten wurden geschlossen.
          <small>{{ errorCode }}</small>
        </p>
      } @else if (loading) {
        <p class="state" role="status">Autorisierte Agent-Projektion wird geladen …</p>
      } @else if (errorCode) {
        <p class="state error" role="alert">
          Keine gebundene Agent-Projektion verfügbar.
          <small>{{ errorCode }}</small>
        </p>
      } @else if (projection; as node) {
        <nav class="view-switch" aria-label="Agentenansicht">
          <button
            type="button"
            [attr.aria-pressed]="activeView === 'overview'"
            (click)="openView('overview')"
          >Übersicht</button>
          <button
            type="button"
            [attr.aria-pressed]="activeView === 'communication'"
            (click)="openView('communication')"
          >Kommunikation</button>
          <button
            type="button"
            [attr.aria-pressed]="activeView === 'trace'"
            (click)="openView('trace')"
          >Trace</button>
        </nav>

        @if (activeView === 'overview') {
          <section class="panel" [attr.aria-labelledby]="runtimeHeadingId">
            <h3 [id]="runtimeHeadingId">Runtime-Metriken</h3>
            @if (node.runtime; as runtime) {
              <dl>
                <div><dt>Status</dt><dd>{{ runtime.status }}</dd></div>
                <div><dt>Aktueller Schritt</dt><dd>{{ runtime.current ? 'Ja' : 'Nein' }}</dd></div>
                <div><dt>Gestartet</dt><dd>{{ displayNumber(runtime.started_at) }}</dd></div>
                <div><dt>Beendet</dt><dd>{{ displayNumber(runtime.finished_at) }}</dd></div>
                <div><dt>Dauer (ms)</dt><dd>{{ displayNumber(runtime.duration_ms) }}</dd></div>
                <div><dt>Modellprofil</dt><dd>{{ displayText(runtime.selected_model_profile_id) }}</dd></div>
                <div><dt>Provider</dt><dd>{{ displayText(runtime.selected_provider_id) }}</dd></div>
                <div><dt>Modell</dt><dd>{{ displayText(runtime.selected_model) }}</dd></div>
              </dl>
            } @else {
              <p class="empty-value">Runtime-Metriken: Nicht verfügbar.</p>
            }
          </section>

          <section class="panel" [attr.aria-labelledby]="activityHeadingId">
            <h3 [id]="activityHeadingId">Aktuelle Aktivität</h3>
            @if (node.current_activity; as activity) {
              <dl>
                <div><dt>Status</dt><dd>{{ displayText(activity.status) }}</dd></div>
                <div><dt>Zeitpunkt</dt><dd>{{ displayNumber(activity.occurred_at) }}</dd></div>
                <div><dt>Kante</dt><dd>{{ displayText(activity.edge_id) }}</dd></div>
                <div><dt>Trace-Referenz</dt><dd>{{ displayText(activity.trace_ref) }}</dd></div>
                <div><dt>Event-Referenz</dt><dd>{{ displayText(activity.event_ref) }}</dd></div>
              </dl>
            } @else {
              <p class="empty-value">Aktuelle Aktivität: Nicht verfügbar.</p>
            }
          </section>

          <section class="panel" [attr.aria-labelledby]="errorHeadingId">
            <h3 [id]="errorHeadingId">Letzter erlaubter Fehler</h3>
            @if (node.last_error; as lastError) {
              <p class="last-error">{{ displayText(lastError.error) }}</p>
              <dl>
                <div><dt>Status</dt><dd>{{ displayText(lastError.status) }}</dd></div>
                <div><dt>Zeitpunkt</dt><dd>{{ displayNumber(lastError.occurred_at) }}</dd></div>
                <div><dt>Kante</dt><dd>{{ displayText(lastError.edge_id) }}</dd></div>
                <div><dt>Trace-Referenz</dt><dd>{{ displayText(lastError.trace_ref) }}</dd></div>
              </dl>
            } @else {
              <p class="empty-value">Letzter erlaubter Fehler: Nicht verfügbar.</p>
            }
          </section>
        } @else if (activeView === 'communication') {
          <section class="panel" [attr.aria-labelledby]="relationshipsHeadingId">
            <h3 [id]="relationshipsHeadingId">Gerichtete Kommunikation</h3>
            <div class="relation-groups">
              @for (kind of relationKinds; track kind) {
                <section [attr.data-relation-kind]="kind" [attr.aria-label]="relationKindLabel(kind)">
                  <h4>{{ relationKindLabel(kind) }}</h4>
                  @if (relationsFor(kind).length) {
                    <ul>
                      @for (relation of relationsFor(kind); track relationKey(relation)) {
                        <li>
                          <button
                            type="button"
                            [attr.data-edge-id]="relation.edge_id"
                            [attr.data-direction]="relation.source_step_id + '->' + relation.target_step_id"
                            [attr.aria-pressed]="relationKey(relation) === selectedRelationKey"
                            (click)="selectRelation(relation)"
                          >
                            <strong>{{ relation.edge_id }}</strong>
                            <span>{{ relation.source_step_id }} → {{ relation.target_step_id }}</span>
                          </button>
                        </li>
                      }
                    </ul>
                  } @else {
                    <p class="empty-value">Keine.</p>
                  }
                </section>
              }
            </div>
          </section>

          @if (selectedRelation; as relation) {
            <section class="panel communication" [attr.aria-labelledby]="communicationHeadingId">
              <h3 [id]="communicationHeadingId">
                Kommunikation · {{ relation.edge_id }} ·
                {{ relation.source_step_id }} → {{ relation.target_step_id }}
              </h3>
              <p class="verification">
                {{ relation.activity_status }} · {{ relation.verification_status }}
              </p>
              @if (relation.messages.length) {
                <ol class="message-list">
                  @for (message of relation.messages; track $index) {
                    <li>
                      <header>
                        <strong>{{ displayText(message.role) }}</strong>
                        <span>{{ displayNumber(message.occurred_at) }}</span>
                      </header>
                      <p>{{ displayText(message.content) }}</p>
                      <small>
                        Trace: {{ displayText(message.trace_ref) }} ·
                        {{ message.verification_status }}
                      </small>
                    </li>
                  }
                </ol>
              } @else {
                <p class="empty-value">Nachrichten: Nicht verfügbar.</p>
              }
              @if (hasRedactedFields(relation)) {
                <p class="redaction-note" role="status">Ein oder mehrere erlaubte Felder sind redigiert.</p>
              }
            </section>
          } @else {
            <p class="state">Wähle eine kanonische Beziehung.</p>
          }
        } @else {
          <section class="panel" [attr.aria-labelledby]="traceHeadingId">
            <h3 [id]="traceHeadingId">Agent-Trace in Hub-Reihenfolge</h3>
            @if (node.hub_ordered_relations.length) {
              @for (relation of node.hub_ordered_relations; track relationKey(relation)) {
                <section class="trace-relation" [attr.data-trace-edge-id]="relation.edge_id">
                  <h4>{{ relation.edge_id }} · {{ relation.source_step_id }} → {{ relation.target_step_id }}</h4>
                  <app-caseflow-edge-trace-list [entries]="relation.telemetry" />
                  @if (hasRedactedFields(relation)) {
                    <p class="redaction-note" role="status">Ein oder mehrere erlaubte Felder sind redigiert.</p>
                  }
                </section>
              }
            } @else {
              <p class="empty-value">Trace-Ereignisse: Nicht verfügbar.</p>
            }
          </section>
        }
      } @else {
        <p class="state" role="status">Wähle einen Agenten und einen Run aus.</p>
      }
    </aside>
  `,
  styleUrl: './caseflow-agent-node-runtime-inspector.component.scss',
})
export class CaseFlowAgentNodeRuntimeInspectorComponent implements OnChanges {
  private readonly reader = inject(CASEFLOW_AGENT_NODE_TRACE_READER);
  private readonly changeDetector = inject(ChangeDetectorRef);
  private readonly destroyRef = inject(DestroyRef);
  private readonly loadRequests = new Subject<NodeLoadRequest>();
  private generation = 0;

  @Input({ required: true }) graph!: VpGraph;
  @Input({ required: true }) selectedStepId = '';
  @Input() workflowId = '';
  @Input() runId = '';
  @Input() runtimeOverlay: VpRuntimeOverlay | null = null;
  /** undefined keeps standalone API loading; null/model delegates loading to the host. */
  @Input() traceReadModel: CaseFlowEdgeTraceReadModel | null | undefined = undefined;
  @Input() traceReadModelReason: string | null = null;
  @Output() readonly accessRevoked = new EventEmitter<string>();

  readonly titleId = `caseflow-node-runtime-title-${++inspectorSequence}`;
  readonly runtimeHeadingId = `${this.titleId}-runtime`;
  readonly activityHeadingId = `${this.titleId}-activity`;
  readonly errorHeadingId = `${this.titleId}-error`;
  readonly relationshipsHeadingId = `${this.titleId}-relationships`;
  readonly communicationHeadingId = `${this.titleId}-communication`;
  readonly traceHeadingId = `${this.titleId}-trace`;
  readonly relationKinds: readonly CaseFlowAgentNodeRelationKind[] = [
    'parent', 'child', 'loop',
  ];

  projection: CaseFlowAgentNodeRuntimeTraceProjection | null = null;
  activeView: NodeInspectorView = 'overview';
  selectedRelationKey: string | null = null;
  loading = false;
  closed = false;
  errorCode: string | null = null;
  private lastAccessRevocation: string | null = null;

  constructor() {
    this.loadRequests.pipe(
      switchMap(request => this.reader.read({
        workflow_id: request.workflowId,
        run_id: request.runId,
      }).pipe(
        map(readModel => ({
          generation: request.generation,
          projection: projectCaseFlowAgentNodeRuntimeTrace(
            request.graph,
            request.selectedStepId,
            request.workflowId,
            request.runId,
            request.runtimeOverlay,
            readModel,
          ),
          errorCode: null,
          accessRevoked: false,
        } satisfies NodeLoadResult)),
        catchError(error => of({
          generation: request.generation,
          projection: null,
          errorCode: nodeLoadErrorCode(error),
          accessRevoked: isAccessRevoked(error),
        } satisfies NodeLoadResult)),
      )),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(result => this.acceptResult(result));
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (this.traceReadModel !== undefined && !nodeScopeChanged(changes)) {
      this.refreshHostProjectionPreservingNavigation();
      return;
    }
    this.reload();
  }

  get selectedStepLabel(): string {
    return this.graph?.steps.find(step => step.id === this.selectedStepId)?.label
      ?? 'Kein Agent gewählt';
  }

  get selectedRelation(): CaseFlowAgentNodeRelationProjection | null {
    if (!this.selectedRelationKey || !this.projection) return null;
    return this.allRelations().find(
      relation => this.relationKey(relation) === this.selectedRelationKey,
    ) ?? null;
  }

  /** Explicit refresh hook for polling/authorization reconciliation owners. */
  reload(): void {
    this.clearState(true, false);
    if (!this.graph || !this.selectedStepId || !this.workflowId || !this.runId) {
      this.loading = false;
      return;
    }
    if (this.traceReadModel !== undefined) {
      this.projectHostReadModel();
      return;
    }
    this.loadRequests.next({
      generation: this.generation,
      graph: this.graph,
      selectedStepId: this.selectedStepId,
      workflowId: this.workflowId,
      runId: this.runId,
      runtimeOverlay: this.runtimeOverlay,
    });
  }

  openView(view: NodeInspectorView): void {
    this.activeView = view;
    if (view === 'communication' && !this.selectedRelation) {
      this.selectedRelationKey = this.allRelations()[0]
        ? this.relationKey(this.allRelations()[0])
        : null;
    }
  }

  relationsFor(kind: CaseFlowAgentNodeRelationKind): readonly CaseFlowAgentNodeRelationProjection[] {
    if (!this.projection) return [];
    if (kind === 'parent') return this.projection.parents;
    if (kind === 'child') return this.projection.children;
    return this.projection.loops;
  }

  relationKindLabel(kind: CaseFlowAgentNodeRelationKind): string {
    if (kind === 'parent') return 'Parents';
    if (kind === 'child') return 'Children';
    return 'Loops';
  }

  relationKey(relation: Readonly<CaseFlowAgentNodeRelationProjection>): string {
    return `${relation.edge_id}\u0000${relation.source_step_id}\u0000${relation.target_step_id}`;
  }

  selectRelation(relation: Readonly<CaseFlowAgentNodeRelationProjection>): void {
    const exact = this.allRelations().find(candidate =>
      this.relationKey(candidate) === this.relationKey(relation));
    if (!exact) return;
    this.selectedRelationKey = this.relationKey(exact);
  }

  displayText(value: string | null): string {
    if (value === null || value === '') return 'Nicht verfügbar';
    return isRedacted(value) ? 'Redigiert' : value;
  }

  displayNumber(value: number | null): string {
    return value === null ? 'Nicht verfügbar' : String(value);
  }

  hasRedactedFields(relation: Readonly<CaseFlowAgentNodeRelationProjection>): boolean {
    return relation.messages.some(message => [
      message.content, message.role, message.event_ref, message.trace_ref,
    ].some(isRedacted)) || relation.telemetry.some(entry => [
      entry.event_ref,
      entry.trace_ref,
      entry.agent_run_ref,
      entry.correlation_ref,
      entry.causation_ref,
      entry.event_type,
      entry.status,
      entry.model,
      entry.provider,
      entry.tool,
      entry.error,
    ].some(isRedacted));
  }

  private acceptResult(
    result: Readonly<NodeLoadResult>,
    preserveNavigation = false,
  ): void {
    if (result.generation !== this.generation) return;
    if (result.accessRevoked) {
      this.closeForAccessRevocation(
        result.errorCode ?? 'caseflow_node_trace_access_revoked',
      );
      return;
    }
    this.loading = false;
    if (!result.projection?.available) {
      this.projection = null;
      this.errorCode = result.errorCode
        ?? result.projection?.reason_code
        ?? 'caseflow_node_projection_unavailable';
      this.changeDetector.markForCheck();
      return;
    }
    this.lastAccessRevocation = null;
    this.projection = result.projection;
    this.errorCode = null;
    if (preserveNavigation) {
      const selectedKey = this.selectedRelationKey;
      if (selectedKey && !this.allRelations().some(
        relation => this.relationKey(relation) === selectedKey,
      )) {
        this.selectedRelationKey = null;
      }
    } else {
      this.selectedRelationKey = this.allRelations()[0]
        ? this.relationKey(this.allRelations()[0])
        : null;
    }
    this.changeDetector.markForCheck();
  }

  private refreshHostProjectionPreservingNavigation(): void {
    this.generation += 1;
    this.projection = null;
    this.errorCode = null;
    this.loading = false;
    this.closed = false;
    if (this.selectedRelationKey && !this.graphContainsRelation(this.selectedRelationKey)) {
      this.selectedRelationKey = null;
    }
    this.projectHostReadModel(true);
  }

  private projectHostReadModel(preserveNavigation = false): void {
    const readModel = this.traceReadModel;
    const reason = this.traceReadModelReason;
    const generation = this.generation;
    this.loading = false;
    if (reason && isAccessRevokedReason(reason)) {
      this.closeForAccessRevocation(reason, true);
      return;
    }
    if (readModel === null) {
      this.lastAccessRevocation = null;
      this.errorCode = reason;
      this.changeDetector.markForCheck();
      return;
    }
    if (readModel === undefined) return;
    const projection = projectCaseFlowAgentNodeRuntimeTrace(
      this.graph,
      this.selectedStepId,
      this.workflowId,
      this.runId,
      this.runtimeOverlay,
      readModel,
    );
    this.acceptResult({
      generation,
      projection,
      errorCode: reason,
      accessRevoked: false,
    }, preserveNavigation);
  }

  private closeForAccessRevocation(reason: string, deduplicate = false): void {
    const shouldEmit = !deduplicate || this.lastAccessRevocation !== reason;
    this.lastAccessRevocation = reason;
    this.clearState(false, true);
    this.errorCode = reason;
    if (shouldEmit) this.accessRevoked.emit(reason);
    this.changeDetector.markForCheck();
  }

  private allRelations(): readonly CaseFlowAgentNodeRelationProjection[] {
    if (!this.projection) return [];
    return [
      ...this.projection.parents,
      ...this.projection.children,
      ...this.projection.loops,
    ];
  }

  private graphContainsRelation(key: string): boolean {
    const matches = this.graph.edges.filter(edge =>
      `${edge.id}\u0000${edge.source}\u0000${edge.target}` === key
      && (edge.source === this.selectedStepId || edge.target === this.selectedStepId));
    return matches.length === 1;
  }

  private clearState(loading: boolean, closed: boolean): void {
    this.generation += 1;
    this.projection = null;
    this.selectedRelationKey = null;
    this.activeView = 'overview';
    this.errorCode = null;
    this.loading = loading;
    this.closed = closed;
    this.changeDetector.markForCheck();
  }
}

function nodeLoadErrorCode(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    if (error.status === 401) return 'caseflow_node_trace_unauthorized';
    if (error.status === 403) return 'caseflow_node_trace_forbidden';
    if (error.status === 404) return 'caseflow_node_trace_not_found';
  }
  return 'caseflow_node_trace_unavailable';
}

function isAccessRevoked(error: unknown): boolean {
  return error instanceof HttpErrorResponse && [401, 403, 404].includes(error.status);
}

function isAccessRevokedReason(reason: string): boolean {
  return reason.includes('unauthorized')
    || reason.includes('forbidden')
    || reason.includes('not_found')
    || reason.includes('access_revoked')
    || reason.includes('runtime_disappeared')
    || /_http_(401|403|404)$/.test(reason);
}

function isRedacted(value: unknown): boolean {
  return typeof value === 'string'
    && (value.includes('***REDACTED_') || value.trim() === '***');
}

function nodeScopeChanged(changes: SimpleChanges): boolean {
  const graphChange = changes['graph'];
  const previousGraph = graphChange?.previousValue as VpGraph | null | undefined;
  const currentGraph = graphChange?.currentValue as VpGraph | null | undefined;
  return Boolean(
    (graphChange && previousGraph?.id !== currentGraph?.id)
    || stringInputChanged(changes['selectedStepId'])
    || stringInputChanged(changes['workflowId'])
    || stringInputChanged(changes['runId']),
  );
}

function stringInputChanged(change: SimpleChanges[string] | undefined): boolean {
  return Boolean(change && change.previousValue !== change.currentValue);
}
