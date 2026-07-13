import { Component, Input } from '@angular/core';
import { ComposeOpsComponent } from './compose-ops.component';
import { DockerOpsComponent } from './docker-ops.component';
import { GitOpsComponent } from './git-ops.component';
import { OpsApprovalPanelComponent } from './ops-approval-panel.component';
import { OpsArea } from './ops.models';

@Component({
  selector: 'app-operations-surface',
  standalone: true,
  imports: [GitOpsComponent, DockerOpsComponent, ComposeOpsComponent, OpsApprovalPanelComponent],
  template: `
    <section class="operations-surface">
      <header class="surface-header">
        <div>
          <h3>Git, Docker & Compose</h3>
          <p class="muted">Transparente Leseansichten und eng begrenzte, auditierbare Verwaltungsaktionen.</p>
        </div>
        <button type="button" class="secondary" (click)="refresh()">Alles aktualisieren</button>
      </header>
      <nav class="area-tabs" aria-label="Operations Bereiche">
        <button type="button" [class.active]="area === 'git'" (click)="area = 'git'"><span class="area-icon">⑂</span><span><strong>Git</strong><small>Workspaces & Historie</small></span></button>
        <button type="button" [class.active]="area === 'docker'" (click)="area = 'docker'"><span class="area-icon">▣</span><span><strong>Docker</strong><small>Engine & Container</small></span></button>
        <button type="button" [class.active]="area === 'compose'" (click)="area = 'compose'"><span class="area-icon">▦</span><span><strong>Compose</strong><small>Stacks & Services</small></span></button>
        <button type="button" [class.active]="area === 'approvals'" (click)="area = 'approvals'"><span class="area-icon">✓</span><span><strong>Freigaben</strong><small>Policy & Audit</small></span></button>
      </nav>
      <div class="area-content">
        @switch (area) {
          @case ('git') { <app-git-ops [baseUrl]="baseUrl" [refreshGeneration]="effectiveRefreshGeneration()" /> }
          @case ('docker') { <app-docker-ops [baseUrl]="baseUrl" [refreshGeneration]="effectiveRefreshGeneration()" /> }
          @case ('compose') { <app-compose-ops [baseUrl]="baseUrl" [refreshGeneration]="effectiveRefreshGeneration()" /> }
          @case ('approvals') { <app-ops-approval-panel [baseUrl]="baseUrl" [refreshGeneration]="effectiveRefreshGeneration()" /> }
        }
      </div>
      <footer class="surface-footer">Mutationen laufen ausschließlich über Hub-API, Registry, Policy, eng gebundene Freigabe und Audit. Destruktive Aktionen wie Force-Push, Prune oder Volume-Löschung werden nicht angeboten.</footer>
    </section>
  `,
  styles: [`
    .operations-surface { display: grid; gap: 14px; }
    .surface-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
    h3, p { margin: 0; }
    .area-tabs { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 8px; }
    .area-tabs button { display: grid; grid-template-columns: 32px minmax(0, 1fr); gap: 8px; text-align: left; align-items: center; background: var(--surface-raised); }
    .area-tabs button.active { border-color: var(--accent); color: var(--accent); box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 25%, transparent); }
    .area-tabs button span:last-child { display: grid; gap: 2px; }
    .area-tabs small { color: var(--muted); }
    .area-icon { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 7px; background: var(--tone-technical-bg); font-size: 17px; }
    .area-content { min-width: 0; }
    .surface-footer { padding-top: 10px; border-top: 1px solid var(--border); color: var(--muted); font-size: 12px; }
    @media (max-width: 760px) { .area-tabs { grid-template-columns: 1fr 1fr; } }
  `],
})
export class OperationsSurfaceComponent {
  @Input({ required: true }) baseUrl = '';
  @Input() refreshGeneration = 0;
  area: OpsArea = 'git';
  private manualRefreshGeneration = 0;

  refresh(): void { this.manualRefreshGeneration += 1; }
  effectiveRefreshGeneration(): number { return this.refreshGeneration + this.manualRefreshGeneration; }
}
