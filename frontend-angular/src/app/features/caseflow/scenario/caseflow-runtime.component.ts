import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { catchError, of, switchMap } from 'rxjs';
import { CaseFlowApiService } from '../caseflow-api.service';
import { CaseAction, CaseFlowCase } from '../caseflow.models';
import { VisualProcessEditorComponent } from '../../visual-process/visual-process-editor.component';
import { PageIntroComponent } from '../../../shared/ui/layout/page-intro.component';
import { SectionCardComponent } from '../../../shared/ui/layout/section-card.component';
import { SummaryPanelComponent } from '../../../shared/ui/display/summary-panel.component';
import { TableShellComponent } from '../../../shared/ui/display/table-shell.component';
import { EmptyStateComponent } from '../../../shared/ui/state/empty-state.component';
import { StatusBadgeComponent } from '../../../shared/ui/state/status-badge.component';
import { CaseFlowScenarioDefinition } from './caseflow-scenario.models';
import { CaseFlowScenarioRegistryService } from './caseflow-scenario-registry.service';

@Component({
  standalone: true,
  selector: 'app-caseflow-runtime',
  imports: [
    EmptyStateComponent,
    PageIntroComponent,
    RouterLink,
    SectionCardComponent,
    StatusBadgeComponent,
    SummaryPanelComponent,
    TableShellComponent,
    VisualProcessEditorComponent,
  ],
  template: `
    <main class="caseflow-page">
      @if (scenario(); as current) {
        <app-page-intro eyebrow="CaseFlow" [title]="current.title" [subtitle]="current.description">
          <a intro-actions class="secondary" routerLink="/caseflow/studio" [queryParams]="{ graph: current.workflowGraphId }">
            Im Studio bearbeiten
          </a>
        </app-page-intro>

        @for (block of current.pages[0].blocks; track block.id) {
          @switch (block.kind) {
            @case ('summary') {
              <app-summary-panel
                [title]="block.title"
                [summary]="block.description || current.description"
                eyebrow="Generische CaseFlow-Komponente"
                [metrics]="summaryMetrics()"
              />
            }
            @case ('metrics') {
              <app-summary-panel [title]="block.title" [metrics]="statusMetrics()" />
            }
            @case ('case-list') {
              <app-section-card [title]="block.title" [subtitle]="block.description || ''">
                <app-table-shell
                  [loading]="loadingCases()"
                  [empty]="!loadingCases() && !cases().length"
                  emptyTitle="Noch keine Cases"
                  emptyDescription="Cases dieses Typs erscheinen hier, sobald der Hub sie angelegt hat."
                >
                  <table>
                    <thead><tr><th>Titel</th><th>Status</th><th>Priorität</th><th>Owner</th></tr></thead>
                    <tbody>
                      @for (item of cases(); track item.id) {
                        <tr>
                          <td>{{ item.title }}</td>
                          <td><app-status-badge [label]="item.status" /></td>
                          <td>{{ item.priority }}</td>
                          <td>{{ item.owner || '–' }}</td>
                        </tr>
                      }
                    </tbody>
                  </table>
                </app-table-shell>
              </app-section-card>
            }
            @case ('actions') {
              <app-section-card [title]="block.title">
                @if (scenarioActions().length) {
                  <ul>
                    @for (action of scenarioActions(); track action.id) {
                      <li><strong>{{ action.title }}</strong> · {{ action.status }}</li>
                    }
                  </ul>
                } @else {
                  <app-empty-state title="Keine offenen Aktionen" [compact]="true" />
                }
              </app-section-card>
            }
            @case ('artifacts') {
              <app-section-card [title]="block.title" [subtitle]="block.description || ''">
                <p class="muted">Workflow-Ergebnisse werden den jeweiligen Cases versioniert zugeordnet.</p>
              </app-section-card>
            }
            @case ('workflow') {
              <app-section-card [title]="block.title" [subtitle]="block.description || ''" variant="technical">
                <app-visual-process-editor
                  [graphId]="current.workflowGraphId"
                  editorMode="compact-readonly"
                />
              </app-section-card>
            }
          }
        }
      } @else if (!loadingScenario()) {
        <app-empty-state
          title="CaseFlow nicht gefunden"
          description="Das Szenario ist nicht veröffentlicht oder wurde entfernt."
          primaryLabel="Zum CaseFlow-Katalog"
          primaryRouterLink="/caseflow"
        />
      }
    </main>
  `,
  styles: [`
    .caseflow-page { display: grid; gap: 1rem; padding: 1rem; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: .65rem; text-align: left; border-bottom: 1px solid var(--border-color, #333); }
  `],
})
export class CaseFlowRuntimeComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  private readonly registry = inject(CaseFlowScenarioRegistryService);
  private readonly caseFlowApi = inject(CaseFlowApiService);

  readonly scenario = signal<CaseFlowScenarioDefinition | null>(null);
  readonly cases = signal<CaseFlowCase[]>([]);
  readonly actions = signal<CaseAction[]>([]);
  readonly loadingScenario = signal(true);
  readonly loadingCases = signal(false);
  readonly scenarioActions = computed(() => {
    const caseIds = new Set(this.cases().map(item => item.id));
    return this.actions().filter(action => caseIds.has(action.case_id));
  });
  readonly summaryMetrics = computed(() => [
    { label: 'Cases', value: this.cases().length },
    { label: 'Offene Aktionen', value: this.scenarioActions().length },
    { label: 'Workflow-Schritte', value: this.scenario()?.pages[0]?.blocks.length || 0 },
  ]);
  readonly statusMetrics = computed(() => {
    const counts = new Map<string, number>();
    this.cases().forEach(item => counts.set(item.status, (counts.get(item.status) || 0) + 1));
    return [...counts.entries()].slice(0, 6).map(([label, value]) => ({ label, value }));
  });

  ngOnInit(): void {
    this.route.paramMap.pipe(
      switchMap(params => this.registry.getScenario(params.get('scenarioId') || '')),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(scenario => {
      this.scenario.set(scenario);
      this.loadingScenario.set(false);
      if (scenario) this.loadCaseData(scenario.caseType);
    });
  }

  private loadCaseData(caseType: string): void {
    this.loadingCases.set(true);
    this.caseFlowApi.listCases({ case_type: caseType }).pipe(
      catchError(() => of({ items: [], total: 0 })),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(result => {
      this.cases.set(result.items);
      this.loadingCases.set(false);
    });
    this.caseFlowApi.getOpenActions().pipe(
      catchError(() => of([])),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(actions => this.actions.set(actions));
  }
}
