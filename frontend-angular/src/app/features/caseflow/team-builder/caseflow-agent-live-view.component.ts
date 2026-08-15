import { CommonModule } from '@angular/common';
import { Component, EventEmitter, Input, OnChanges, Output, SimpleChanges, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { VisualProcessApiService, type VpGraph } from '../../visual-process/visual-process-api.service';
import { CaseFlowAgentCanvasComponent } from '../agent-canvas/caseflow-agent-canvas.component';
import { projectCaseFlowAgentNodeRuntimeTrace } from '../agent-canvas/caseflow-agent-node-runtime.mapper';
import { CaseFlowAgentRuntimeSessionFacade } from '../agent-canvas/caseflow-agent-runtime-session.facade';
import { type AgentConversation, projectAgentConversation } from './caseflow-agent-conversation';
import { agentGlyph, statusGlyph } from '../agent-canvas/caseflow-agent-glyphs';

type DetailTab = 'thoughts' | string;

/**
 * A team on a map, live, with one agent open at a time.
 *
 * The runtime session, the canvas and the per-node trace projection all
 * existed already but only ever met inside CaseFlow Studio, which is built
 * for someone configuring a process. This puts the same evidence in front of
 * someone who just wants to watch their agents work: who is running, what
 * each one produced, and what it said to whom.
 *
 * Nothing here derives runtime truth of its own — every status and every
 * message comes from the Hub projections, and an unproven one is shown as
 * unproven rather than quietly rendered as fact.
 */
@Component({
  selector: 'app-caseflow-agent-live-view',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, CaseFlowAgentCanvasComponent],
  providers: [CaseFlowAgentRuntimeSessionFacade],
  styleUrl: './caseflow-agent-live-view.component.scss',
  template: `
    <section class="live">
      <header class="live-head">
        <div class="live-title">
          <h2>{{ graph.name }}</h2>
          <span class="live-state" [class]="'live-state--' + session.state()">{{ stateLabel() }}</span>
        </div>
        <div class="live-actions">
          @if (session.canRefresh()) {
            <button type="button" class="live-ghost" (click)="session.refresh()">Aktualisieren</button>
          }
          <ng-content select="[liveActions]" />
        </div>
      </header>

      @if (session.errorCode(); as code) {
        <p class="live-error" role="alert">Laufzeit nicht lesbar ({{ code }}).</p>
      }

      <app-caseflow-agent-canvas
        [graph]="graph"
        [runtimeOverlay]="session.runtimeOverlay()"
        [edgeTraceReadModel]="session.edgeTraceReadModel()"
        [selectedNodeId]="selectedStepId()"
        (nodeSelected)="selectAgent($event)"
      />

      @if (selectedStep(); as step) {
        <article class="agent-box">
          <header class="agent-box-head">
            <span class="agent-glyph" aria-hidden="true">{{ glyphFor(step) }}</span>
            <label class="agent-name">
              <span class="agent-name-hint">Name dieses Agenten</span>
              <input
                type="text"
                [ngModel]="draftName()"
                (ngModelChange)="draftName.set($event)"
                aria-label="Name dieses Agenten"
              />
            </label>
            <span class="agent-status">
              {{ statusGlyphFor() }} {{ statusLabel() }}
            </span>
            <button type="button" class="live-ghost" (click)="closeAgent()" aria-label="Agent schließen">✕</button>
          </header>

          <p class="agent-desc">
            {{ describe(step) }}
          </p>

          @if (renameError(); as message) {
            <p class="live-error" role="alert">{{ message }}</p>
          }

          <div class="agent-buttons">
            <button
              type="button"
              class="live-primary"
              [disabled]="!renameable() || renaming()"
              (click)="applyName()"
            >
              {{ renaming() ? 'Wird gespeichert …' : 'Namen übernehmen' }}
            </button>
            <a class="live-ghost" routerLink="/caseflow/studio">Einstellungen im Studio</a>
            <a class="live-ghost" routerLink="/process-designer">Schritt im Designer</a>
          </div>

          <nav class="agent-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              [attr.aria-selected]="tab() === 'thoughts'"
              [class.agent-tab--active]="tab() === 'thoughts'"
              (click)="tab.set('thoughts')"
            >
              Gedanken ({{ conversation().thoughts.length }})
            </button>
            @for (exchange of conversation().exchanges; track exchange.peer_step_id) {
              <button
                type="button"
                role="tab"
                [attr.aria-selected]="tab() === exchange.peer_step_id"
                [class.agent-tab--active]="tab() === exchange.peer_step_id"
                (click)="tab.set(exchange.peer_step_id)"
              >
                ↔ {{ exchange.peer_label }} ({{ exchange.entries.length }})
              </button>
            }
          </nav>

          @if (!conversation().available) {
            <p class="live-muted">
              Noch kein Lauf zu diesem Team lesbar ({{ conversation().reason_code }}). Sobald das Team
              läuft, erscheinen hier seine Gedanken und der Austausch.
            </p>
          } @else if (!visibleEntries().length) {
            <p class="live-muted">Hier ist noch nichts gesagt worden.</p>
          } @else {
            <ol class="agent-chat">
              @for (entry of visibleEntries(); track entry.key) {
                <li class="chat-line" [class.chat-line--incoming]="entry.direction === 'incoming'">
                  <span class="chat-who">
                    {{ entry.direction === 'outgoing' ? draftName() : entry.peer_label }}
                    @if (entry.role) {
                      <span class="chat-role">{{ entry.role }}</span>
                    }
                  </span>
                  <span class="chat-content">{{ entry.content }}</span>
                  <span class="chat-marks">
                    @if (!entry.verified) {
                      <span class="chat-mark" title="Herkunft nicht belegt">ungeprüft</span>
                    }
                    @if (entry.truncated) {
                      <span class="chat-mark" title="Gekürzt übertragen">gekürzt</span>
                    }
                  </span>
                </li>
              }
            </ol>
          }
        </article>
      } @else {
        <p class="live-muted">Einen Agenten auf der Karte anklicken, um ihn zu öffnen.</p>
      }
    </section>
  `,
})
export class CaseFlowAgentLiveViewComponent implements OnChanges {
  private readonly api = inject(VisualProcessApiService);

  protected readonly session = inject(CaseFlowAgentRuntimeSessionFacade);

  @Input({ required: true }) graph!: VpGraph;
  @Output() readonly graphChange = new EventEmitter<VpGraph>();

  protected readonly selectedStepId = signal<string | null>(null);
  protected readonly tab = signal<DetailTab>('thoughts');
  protected readonly draftName = signal('');
  protected readonly renaming = signal(false);
  protected readonly renameError = signal<string | null>(null);

  ngOnChanges(changes: SimpleChanges): void {
    if (!changes['graph']) return;
    const graph = this.graph;
    this.selectedStepId.set(null);
    this.renameError.set(null);
    if (!graph?.id) {
      this.session.detach();
      return;
    }
    // The Hub scopes a run by the graph it ran, so the two identities coincide.
    this.session.attach({ graph_id: graph.id, workflow_id: graph.id });
  }

  protected selectAgent(stepId: string): void {
    const step = this.graph.steps.find(candidate => candidate.id === stepId);
    if (!step) return;
    this.selectedStepId.set(stepId);
    this.draftName.set(step.label);
    this.renameError.set(null);
    this.tab.set('thoughts');
  }

  protected closeAgent(): void {
    this.selectedStepId.set(null);
  }

  protected selectedStep() {
    const stepId = this.selectedStepId();
    return stepId ? this.graph.steps.find(step => step.id === stepId) ?? null : null;
  }

  /**
   * The trace for the open agent, or nothing.
   *
   * Recomputed rather than cached because both the graph and the poll result
   * move underneath it; the projection itself is pure and cheap.
   */
  protected conversation(): AgentConversation {
    const stepId = this.selectedStepId();
    const runId = this.session.runId();
    if (!stepId || !runId) {
      return { available: false, reason_code: 'caseflow_conversation_no_run', thoughts: [], exchanges: [] };
    }
    const trace = projectCaseFlowAgentNodeRuntimeTrace(
      this.graph,
      stepId,
      this.graph.id,
      runId,
      this.session.runtimeOverlay(),
      this.session.edgeTraceReadModel(),
    );
    return projectAgentConversation(trace, stepId);
  }

  protected visibleEntries() {
    const conversation = this.conversation();
    const tab = this.tab();
    if (tab === 'thoughts') return conversation.thoughts;
    return conversation.exchanges.find(exchange => exchange.peer_step_id === tab)?.entries ?? [];
  }

  protected glyphFor(step: { readonly role?: string; readonly metadata?: Record<string, unknown> }): string {
    const presentation = this.graph.extensions?.['ananta.caseflow.agent-canvas'] as
      | { nodes?: Record<string, { icon?: string }> }
      | undefined;
    const stepId = this.selectedStepId() ?? '';
    return agentGlyph({ icon: presentation?.nodes?.[stepId]?.icon ?? null, role: step.role ?? null });
  }

  protected statusGlyphFor(): string {
    return statusGlyph(this.overlayStatus());
  }

  protected statusLabel(): string {
    const status = this.overlayStatus();
    return status ? STATUS_LABELS[status] ?? status : 'kein Lauf';
  }

  protected describe(step: { readonly role?: string; readonly kind?: string }): string {
    const parts = [step.role ? `Rolle: ${step.role}` : null, step.kind ? `Aufgabe: ${step.kind}` : null];
    const overlay = this.overlayStep();
    if (overlay?.selected_model) parts.push(`Modell: ${overlay.selected_model}`);
    if (overlay?.duration_ms != null) parts.push(`Dauer: ${Math.round(overlay.duration_ms / 100) / 10}s`);
    const described = parts.filter(Boolean).join(' · ');
    return described || 'Noch nichts über diesen Agenten bekannt.';
  }

  protected renameable(): boolean {
    const step = this.selectedStep();
    const name = this.draftName().trim();
    if (!step || !name) return false;
    if (name === step.label) return false;
    return !this.graph.steps.some(other => other.id !== step.id && other.label.trim() === name);
  }

  /**
   * Persist the name a person gave this agent.
   *
   * The label is what every other view shows, so a rename here is a rename
   * everywhere — which is the point. It goes through the same optimistic
   * concurrency as any other graph edit rather than around it.
   */
  protected applyName(): void {
    const step = this.selectedStep();
    if (!step || !this.renameable()) return;
    const name = this.draftName().trim();
    const next: VpGraph = {
      ...this.graph,
      steps: this.graph.steps.map(candidate =>
        candidate.id === step.id ? { ...candidate, label: name } : candidate,
      ),
    };
    this.renaming.set(true);
    this.renameError.set(null);
    this.api.saveGraph(next).subscribe({
      next: result => {
        this.renaming.set(false);
        this.graphChange.emit({
          ...next,
          definition_revision: result.definition_revision,
          base_graph_hash: result.base_graph_hash,
        });
      },
      error: () => {
        this.renaming.set(false);
        this.renameError.set('Der Name konnte nicht gespeichert werden. Bitte noch einmal versuchen.');
      },
    });
  }

  protected stateLabel(): string {
    return SESSION_LABELS[this.session.state()] ?? this.session.state();
  }

  private overlayStep() {
    const stepId = this.selectedStepId();
    return stepId ? this.session.runtimeOverlay()?.steps?.[stepId] ?? null : null;
  }

  private overlayStatus(): string | null {
    return this.overlayStep()?.status ?? null;
  }
}

const STATUS_LABELS: Readonly<Record<string, string>> = {
  pending: 'wartet',
  running: 'arbeitet',
  awaiting_approval: 'wartet auf Freigabe',
  succeeded: 'fertig',
  success: 'fertig',
  failed: 'fehlgeschlagen',
  error: 'fehlgeschlagen',
  skipped: 'übersprungen',
  cancelled: 'abgebrochen',
  unknown: 'unbekannt',
};

const SESSION_LABELS: Readonly<Record<string, string>> = {
  detached: 'nicht verbunden',
  loading: 'verbindet …',
  no_run: 'läuft gerade nicht',
  no_run_timeout: 'kein Lauf gefunden',
  active: 'live',
  terminal: 'Lauf beendet',
  access_revoked: 'Zugriff entzogen',
  error: 'nicht lesbar',
};
