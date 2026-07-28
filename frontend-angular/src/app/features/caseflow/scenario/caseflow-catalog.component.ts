import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ActionCardComponent } from '../../../shared/ui/layout/action-card.component';
import { PageIntroComponent } from '../../../shared/ui/layout/page-intro.component';
import { SectionCardComponent } from '../../../shared/ui/layout/section-card.component';
import { EmptyStateComponent } from '../../../shared/ui/state/empty-state.component';
import { CaseFlowScenarioDefinition } from './caseflow-scenario.models';
import { CaseFlowScenarioRegistryService } from './caseflow-scenario-registry.service';

@Component({
  standalone: true,
  selector: 'app-caseflow-catalog',
  imports: [ActionCardComponent, EmptyStateComponent, PageIntroComponent, RouterLink, SectionCardComponent],
  template: `
    <main class="caseflow-page">
      <app-page-intro
        eyebrow="Anwendungsszenarien"
        title="CaseFlow"
        subtitle="Workflowbasierte Anwendungen aus wiederverwendbaren Angular-Komponenten."
      >
        <a intro-actions class="primary" routerLink="/caseflow/studio">CaseFlow entwickeln</a>
      </app-page-intro>

      <app-section-card
        title="Verfügbare Szenarien"
        subtitle="Bestehende Anwendungen und aus Visual Processes veröffentlichte CaseFlows."
      >
        @if (loading()) {
          <p class="muted">CaseFlows werden geladen …</p>
        } @else if (!scenarios().length) {
          <app-empty-state
            title="Noch keine CaseFlows"
            description="Erstelle im CaseFlow Studio ein Anwendungsszenario aus einem Workflow."
          />
        } @else {
          <div class="caseflow-grid">
            @for (scenario of scenarios(); track scenario.id) {
              <app-action-card
                [title]="scenario.title"
                [description]="scenario.description"
                [badge]="scenario.tags.includes('caseflow') ? 'Workflow' : 'Anwendung'"
                [routerLink]="scenario.route || ['/caseflow/scenario', scenario.id]"
              />
            }
          </div>
        }
      </app-section-card>
    </main>
  `,
  styles: [`
    .caseflow-page { display: grid; gap: 1rem; padding: 1rem; }
    .caseflow-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr)); gap: 1rem; }
  `],
})
export class CaseFlowCatalogComponent implements OnInit {
  private readonly registry = inject(CaseFlowScenarioRegistryService);
  readonly scenarios = signal<CaseFlowScenarioDefinition[]>([]);
  readonly loading = signal(true);

  ngOnInit(): void {
    this.registry.listScenarios().subscribe(items => {
      this.scenarios.set(items);
      this.loading.set(false);
    });
  }
}
