import { HttpErrorResponse } from '@angular/common/http';
import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  DestroyRef,
  EventEmitter,
  Input,
  OnChanges,
  Output,
  SimpleChanges,
  inject,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Subject, catchError, map, of, switchMap } from 'rxjs';

import { CaseFlowEdgeTraceApiService } from './caseflow-edge-trace-api.service';
import { CaseFlowEdgeTraceListComponent } from './caseflow-edge-trace-list.component';
import {
  CaseFlowEdgeIdentity,
  CaseFlowEdgeTraceMessage,
  CaseFlowEdgeTraceProjection,
  CaseFlowMessageTelemetryResolution,
} from './caseflow-edge-trace.models';
import {
  resolveCaseFlowMessageTelemetry,
  selectExactCaseFlowEdge,
} from './caseflow-edge-trace.validator';

type CaseFlowEdgeInspectorTab = 'communication' | 'telemetry';

interface EdgeLoadRequest {
  readonly generation: number;
  readonly workflowId: string;
  readonly runId: string;
  readonly edge: CaseFlowEdgeIdentity;
}

interface EdgeLoadResult {
  readonly generation: number;
  readonly edge: CaseFlowEdgeTraceProjection | null;
  readonly errorCode: string | null;
}

@Component({
  selector: 'app-caseflow-agent-edge-inspector',
  standalone: true,
  imports: [CaseFlowEdgeTraceListComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <aside class="edge-inspector" aria-label="CaseFlow Verbindungsdetails">
      <header class="inspector-header">
        <div>
          <p class="eyebrow">Gerichtete Kommunikation</p>
          <h3>{{ selectedDirection ? directionLabel(selectedDirection) : 'Keine Verbindung ausgewählt' }}</h3>
        </div>
        @if (availableDirections.length > 1) {
          <nav class="direction-switch" aria-label="Kommunikationsrichtung">
            @for (direction of availableDirections; track direction.edge_id) {
              <button
                type="button"
                [class.active]="sameEdge(direction, selectedDirection)"
                [attr.aria-pressed]="sameEdge(direction, selectedDirection)"
                (click)="selectDirection(direction)"
              >
                {{ directionLabel(direction) }}
              </button>
            }
          </nav>
        }
      </header>

      @if (loading) {
        <p class="inspector-state" role="status">Hub-Projektion wird geladen …</p>
      } @else if (errorCode) {
        <p class="inspector-state error" role="alert">
          Keine autorisierte Edge-Projektion verfügbar.
          <small>{{ errorCode }}</small>
        </p>
      } @else if (projection; as edgeProjection) {
        <div class="edge-status">
          <span>{{ edgeProjection.activity_status }}</span>
          <span>{{ edgeProjection.verification_status }}</span>
        </div>

        <nav class="inspector-tabs" aria-label="Verbindungsansicht">
          <button
            type="button"
            [class.active]="activeTab === 'communication'"
            [attr.aria-selected]="activeTab === 'communication'"
            role="tab"
            (click)="openTab('communication')"
          >Kommunikation</button>
          <button
            type="button"
            [class.active]="activeTab === 'telemetry'"
            [attr.aria-selected]="activeTab === 'telemetry'"
            role="tab"
            (click)="openTab('telemetry')"
          >Telemetrie</button>
        </nav>

        @if (activeTab === 'communication') {
          <section role="tabpanel" aria-label="Kommunikation">
            @if (edgeProjection.messages.length === 0) {
              <p class="inspector-state">Keine verifizierten Nachrichten für diese Richtung.</p>
            } @else {
              <ol class="message-list">
                @for (message of edgeProjection.messages; track $index) {
                  <li>
                    <header>
                      <strong>{{ message.role ?? 'Rolle nicht verfügbar' }}</strong>
                      <span>{{ displayTime(message.occurred_at) }}</span>
                    </header>
                    <p>{{ message.content }}</p>
                    @if (messageTelemetry(message, edgeProjection); as resolution) {
                      @if (resolution.status === 'verified') {
                        <button
                          type="button"
                          class="correlation-link"
                          (click)="showTelemetry(resolution.telemetry_index)"
                        >
                          Telemetrie: {{ resolution.correlation_ref }}
                        </button>
                      } @else {
                        <span class="correlation-unverified">
                          Korrelation nicht verifiziert{{ resolution.correlation_ref ? ': ' + resolution.correlation_ref : '' }}
                        </span>
                      }
                    }
                  </li>
                }
              </ol>
            }
          </section>
        } @else {
          <section role="tabpanel" aria-label="Telemetrie">
            <p class="telemetry-scope" data-testid="caseflow-edge-run-scope">
              Run: <code>{{ runId }}</code>
            </p>
            <app-caseflow-edge-trace-list
              [entries]="edgeProjection.telemetry"
              [highlightedIndex]="highlightedTelemetryIndex"
            />
          </section>
        }
      } @else {
        <p class="inspector-state" role="status">Keine exakte Edge-Projektion verfügbar.</p>
      }
    </aside>
  `,
  styleUrl: './caseflow-agent-edge-inspector.component.scss',
})
export class CaseFlowAgentEdgeInspectorComponent implements OnChanges {
  private readonly api = inject(CaseFlowEdgeTraceApiService);
  private readonly changeDetector = inject(ChangeDetectorRef);
  private readonly destroyRef = inject(DestroyRef);
  private readonly loadRequests = new Subject<EdgeLoadRequest>();
  private generation = 0;

  @Input() workflowId = '';
  @Input() runId = '';
  @Input() edge: CaseFlowEdgeIdentity | null = null;
  @Input() reverseEdge: CaseFlowEdgeIdentity | null = null;
  @Output() readonly directionSelected = new EventEmitter<CaseFlowEdgeIdentity>();

  selectedDirection: CaseFlowEdgeIdentity | null = null;
  projection: CaseFlowEdgeTraceProjection | null = null;
  activeTab: CaseFlowEdgeInspectorTab = 'communication';
  highlightedTelemetryIndex: number | null = null;
  loading = false;
  errorCode: string | null = null;

  constructor() {
    this.loadRequests.pipe(
      switchMap(request => this.api.read({
        workflow_id: request.workflowId,
        run_id: request.runId,
      }).pipe(
        map(readModel => ({
          generation: request.generation,
          edge: selectExactCaseFlowEdge(readModel, request.edge),
          errorCode: null,
        } satisfies EdgeLoadResult)),
        catchError(error => of({
          generation: request.generation,
          edge: null,
          errorCode: edgeLoadErrorCode(error),
        } satisfies EdgeLoadResult)),
      )),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(result => {
      if (result.generation !== this.generation) return;
      this.loading = false;
      this.projection = result.edge;
      this.errorCode = result.errorCode
        ?? (result.edge === null ? 'caseflow_edge_not_found_or_ambiguous' : null);
      this.changeDetector.markForCheck();
    });
  }

  ngOnChanges(changes: SimpleChanges): void {
    const directions = this.availableDirections;
    if (changes['edge'] || !directions.some(direction => this.sameEdge(direction, this.selectedDirection))) {
      this.selectedDirection = this.edge;
    }
    if (this.selectedDirection && this.workflowId && this.runId) {
      this.load(this.selectedDirection);
    } else {
      this.clearProjection(false);
    }
  }

  get availableDirections(): readonly CaseFlowEdgeIdentity[] {
    if (!this.edge) return [];
    if (!this.reverseEdge || !isCanonicalReverse(this.edge, this.reverseEdge)) return [this.edge];
    return [this.edge, this.reverseEdge];
  }

  selectDirection(direction: Readonly<CaseFlowEdgeIdentity>): void {
    const canonical = this.availableDirections.find(candidate => this.sameEdge(candidate, direction));
    if (!canonical || this.sameEdge(canonical, this.selectedDirection)) return;
    this.selectedDirection = canonical;
    this.clearProjection(true);
    const generation = this.generation;
    const workflowId = this.workflowId;
    const runId = this.runId;
    this.directionSelected.emit(canonical);
    if (generation !== this.generation
      || workflowId !== this.workflowId
      || runId !== this.runId
      || !this.sameEdge(canonical, this.selectedDirection)) return;
    this.enqueueLoad(canonical, generation, workflowId, runId);
  }

  openTab(tab: CaseFlowEdgeInspectorTab): void {
    this.activeTab = tab;
    if (tab === 'communication') this.highlightedTelemetryIndex = null;
  }

  showTelemetry(index: number): void {
    this.highlightedTelemetryIndex = index;
    this.activeTab = 'telemetry';
  }

  messageTelemetry(
    message: Readonly<CaseFlowEdgeTraceMessage>,
    projection: Readonly<CaseFlowEdgeTraceProjection>,
  ): CaseFlowMessageTelemetryResolution {
    return resolveCaseFlowMessageTelemetry(message, projection.telemetry);
  }

  displayTime(value: number | null): string {
    return value === null ? 'Zeitpunkt nicht verfügbar' : String(value);
  }

  directionLabel(direction: Readonly<CaseFlowEdgeIdentity>): string {
    return `${direction.source_step_id} → ${direction.target_step_id}`;
  }

  sameEdge(
    left: Readonly<CaseFlowEdgeIdentity> | null,
    right: Readonly<CaseFlowEdgeIdentity> | null,
  ): boolean {
    return left !== null && right !== null
      && left.edge_id === right.edge_id
      && left.source_step_id === right.source_step_id
      && left.target_step_id === right.target_step_id;
  }

  private load(edge: Readonly<CaseFlowEdgeIdentity>): void {
    this.clearProjection(true);
    this.enqueueLoad(edge, this.generation, this.workflowId, this.runId);
  }

  private enqueueLoad(
    edge: Readonly<CaseFlowEdgeIdentity>,
    generation: number,
    workflowId: string,
    runId: string,
  ): void {
    this.loadRequests.next({
      generation,
      workflowId,
      runId,
      edge: Object.freeze({ ...edge }),
    });
  }

  private clearProjection(loading: boolean): void {
    this.generation += 1;
    this.projection = null;
    this.errorCode = null;
    this.highlightedTelemetryIndex = null;
    this.activeTab = 'communication';
    this.loading = loading;
    this.changeDetector.markForCheck();
  }
}

function edgeLoadErrorCode(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    if (error.status === 401) return 'caseflow_edge_trace_unauthorized';
    if (error.status === 403) return 'caseflow_edge_trace_forbidden';
    if (error.status === 404) return 'caseflow_edge_trace_not_found';
  }
  return 'caseflow_edge_trace_unavailable';
}

function isCanonicalReverse(
  edge: Readonly<CaseFlowEdgeIdentity>,
  candidate: Readonly<CaseFlowEdgeIdentity>,
): boolean {
  return edge.source_step_id !== edge.target_step_id
    && edge.edge_id !== candidate.edge_id
    && edge.source_step_id === candidate.target_step_id
    && edge.target_step_id === candidate.source_step_id;
}
