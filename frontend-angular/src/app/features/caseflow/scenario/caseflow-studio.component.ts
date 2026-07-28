import { Component, DestroyRef, OnInit, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { catchError, of } from 'rxjs';
import {
  SavedGraphSummary,
  VisualProcessApiService,
  VpGraph,
} from '../../visual-process/visual-process-api.service';
import { VisualProcessEditorComponent } from '../../visual-process/visual-process-editor.component';
import { PageIntroComponent } from '../../../shared/ui/layout/page-intro.component';
import { SectionCardComponent } from '../../../shared/ui/layout/section-card.component';
import { SummaryPanelComponent } from '../../../shared/ui/display/summary-panel.component';
import { CaseFlowScenarioDefinition, CaseFlowScenarioDraft } from './caseflow-scenario.models';
import { CaseFlowScenarioRegistryService } from './caseflow-scenario-registry.service';

@Component({
  standalone: true,
  selector: 'app-caseflow-studio',
  imports: [
    FormsModule,
    PageIntroComponent,
    RouterLink,
    SectionCardComponent,
    SummaryPanelComponent,
    VisualProcessEditorComponent,
  ],
  template: `
    <main class="caseflow-page">
      <app-page-intro
        eyebrow="Workflow → Anwendung"
        title="CaseFlow Studio"
        subtitle="Entwickle einen Workflow im Visual Process Editor und erzeuge daraus eine übersichtliche Angular-Anwendung."
      >
        <a intro-actions class="secondary" routerLink="/process-designer">Vollständiger Prozesseditor</a>
      </app-page-intro>

      <app-section-card
        title="1. Workflow auswählen"
        subtitle="Der Hub bleibt Eigentümer des Prozesses; CaseFlow ergänzt nur eine deklarative UI-Beschreibung."
      >
        <label class="field">
          <span>Gespeicherter Visual Process</span>
          <select [(ngModel)]="selectedGraphId" (ngModelChange)="selectGraph($event)">
            <option value="">Bitte auswählen …</option>
            @for (graph of graphs(); track graph.id) {
              <option [value]="graph.id">{{ graph.name }}</option>
            }
          </select>
        </label>
        @if (!graphs().length && !loadingGraphs()) {
          <p class="muted">Noch keine gespeicherten Prozesse vorhanden. Lege zuerst einen Prozess an.</p>
        }
      </app-section-card>

      @if (selectedGraphId) {
        <app-section-card
          title="2. Workflow entwickeln"
          subtitle="Der eingebettete Editor nutzt dieselben generischen Werkzeuge und den vorhandenen AI-Assistenten."
          variant="technical"
        >
          <app-visual-process-editor [graphId]="selectedGraphId" editorMode="embedded-edit" />
        </app-section-card>

        <app-section-card
          title="3. Anwendung beschreiben"
          subtitle="Es wird kein Angular-Code generiert: Metadaten und Workflow-Struktur wählen Komponenten aus einer sicheren Allowlist."
        >
          <div class="form-grid">
            <label class="field"><span>Titel</span><input [(ngModel)]="draft.title" /></label>
            <label class="field"><span>Case-Typ</span><input [(ngModel)]="draft.caseType" /></label>
            <label class="field"><span>Technische ID</span><input [(ngModel)]="draft.id" /></label>
            <label class="field"><span>Icon-Name</span><input [(ngModel)]="draft.icon" /></label>
            <label class="field full"><span>Beschreibung</span><textarea rows="3" [(ngModel)]="draft.description"></textarea></label>
          </div>
          <div class="actions">
            <button type="button" class="secondary" (click)="generatePreview()" [disabled]="busy()">
              UI aus aktuellem Workflow erzeugen
            </button>
            <button type="button" class="primary" (click)="publish()" [disabled]="busy() || !preview()">
              CaseFlow veröffentlichen
            </button>
          </div>
          @if (message()) {
            <p role="status" [class.error]="hasError()">{{ message() }}</p>
          }
        </app-section-card>
      }

      @if (preview(); as current) {
        <app-section-card title="Vorschau der generierten Struktur">
          <app-summary-panel
            [title]="current.title"
            [summary]="current.description"
            eyebrow="CaseFlow UI v1"
            [metrics]="[
              { label: 'Seiten', value: current.pages.length },
              { label: 'Komponenten', value: current.pages[0].blocks.length },
              { label: 'Case-Typ', value: current.caseType }
            ]"
          />
          <div class="component-list">
            @for (block of current.pages[0].blocks; track block.id) {
              <span class="badge">{{ block.title }} · {{ block.kind }}</span>
            }
          </div>
          <a [routerLink]="['/caseflow/scenario', current.id]">Veröffentlichte Anwendung öffnen</a>
        </app-section-card>
      }
    </main>
  `,
  styles: [`
    .caseflow-page { display: grid; gap: 1rem; padding: 1rem; }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
    .field { display: grid; gap: .35rem; }
    .field.full { grid-column: 1 / -1; }
    .field input, .field select, .field textarea, select { width: 100%; }
    .actions, .component-list { display: flex; flex-wrap: wrap; gap: .75rem; margin-top: 1rem; }
    .error { color: var(--danger-color, #d33); }
    @media (max-width: 48rem) { .form-grid { grid-template-columns: 1fr; } }
  `],
})
export class CaseFlowStudioComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  private readonly visualProcessApi = inject(VisualProcessApiService);
  private readonly registry = inject(CaseFlowScenarioRegistryService);

  readonly graphs = signal<SavedGraphSummary[]>([]);
  readonly loadingGraphs = signal(true);
  readonly preview = signal<CaseFlowScenarioDefinition | null>(null);
  readonly busy = signal(false);
  readonly message = signal('');
  readonly hasError = signal(false);
  selectedGraphId = '';
  draft: CaseFlowScenarioDraft = {
    id: '',
    title: '',
    description: '',
    icon: 'account_tree',
    caseType: '',
  };

  ngOnInit(): void {
    const requestedGraph = this.route.snapshot.queryParamMap.get('graph') || '';
    this.visualProcessApi.listSavedGraphs().pipe(
      catchError(() => of([])),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(graphs => {
      this.graphs.set(graphs);
      this.loadingGraphs.set(false);
      if (requestedGraph && graphs.some(graph => graph.id === requestedGraph)) {
        this.selectedGraphId = requestedGraph;
        this.selectGraph(requestedGraph);
      }
    });
  }

  selectGraph(graphId: string): void {
    this.preview.set(null);
    this.message.set('');
    if (!graphId) return;
    this.loadLatestGraph(graph => {
      const existing = this.registry.fromGraph(graph);
      const generated = existing || this.registry.compileFromGraph(graph);
      this.draft = {
        id: generated.id,
        title: generated.title,
        description: generated.description,
        icon: generated.icon,
        caseType: generated.caseType,
      };
      if (existing) this.preview.set(existing);
    });
  }

  generatePreview(): void {
    this.loadLatestGraph(graph => {
      this.preview.set(this.registry.compileFromGraph(graph, this.draft));
      this.message.set('UI-Struktur aus dem aktuellen Workflow erzeugt.');
    });
  }

  publish(): void {
    this.loadLatestGraph(graph => {
      const scenario = this.registry.compileFromGraph(graph, this.draft);
      this.preview.set(scenario);
      this.busy.set(true);
      this.registry.publish(graph, scenario).pipe(
        takeUntilDestroyed(this.destroyRef),
      ).subscribe({
        next: () => {
          this.busy.set(false);
          this.hasError.set(false);
          this.message.set(`CaseFlow „${scenario.title}“ wurde am Visual Process veröffentlicht.`);
        },
        error: error => {
          this.busy.set(false);
          this.hasError.set(true);
          this.message.set(error?.error?.message || 'CaseFlow konnte nicht veröffentlicht werden.');
        },
      });
    });
  }

  private loadLatestGraph(onSuccess: (graph: VpGraph) => void): void {
    if (!this.selectedGraphId) return;
    this.busy.set(true);
    this.hasError.set(false);
    this.visualProcessApi.loadSavedGraph(this.selectedGraphId).pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe({
      next: graph => {
        this.busy.set(false);
        onSuccess(graph);
      },
      error: () => {
        this.busy.set(false);
        this.hasError.set(true);
        this.message.set('Der aktuelle Workflow konnte nicht geladen werden.');
      },
    });
  }
}
