import { CommonModule } from '@angular/common';
import { Component, Input, OnChanges, SimpleChanges, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { OpsApiClient } from '../../services/ops-api.client';
import { ComposeProjectSummary, ComposeServiceStatus, OpsActionResult, OpsTextResult } from './ops.models';
import { OpsApprovalPanelComponent } from './ops-approval-panel.component';
import { OpsLogViewerComponent } from './ops-log-viewer.component';

type ComposeAction = 'pull' | 'up' | 'stop' | 'restart' | 'down';
type RetryAction = { approvalId: string; label: string; run: (approvalId?: string) => void };

@Component({
  selector: 'app-compose-ops',
  standalone: true,
  imports: [CommonModule, FormsModule, OpsApprovalPanelComponent, OpsLogViewerComponent],
  template: `
    <section class="ops-section">
      <div class="section-toolbar">
        <div><h4>Docker-Compose-Verwaltung</h4><p class="muted no-margin">Registrierte Stacks, Profile, Services und policy-gesteuerte Lifecycle-Aktionen.</p></div>
        <button type="button" class="secondary" (click)="loadProjects()" [disabled]="loading">{{ loading ? 'Lädt…' : 'Aktualisieren' }}</button>
      </div>
      @if (loadError) { <div class="state-banner error">{{ loadError }}</div> }
      @if (actionMessage) {
        <div class="state-banner" [class.error]="actionError" [class.warning]="pendingApprovalId">
          {{ actionMessage }}
          @if (pendingApprovalId) { <button type="button" class="secondary btn-small" (click)="showApprovals = true">Freigabe öffnen</button> }
          @if (retryAction) { <button type="button" class="primary btn-small" (click)="retryApprovedAction()">{{ retryAction.label }} erneut ausführen</button> }
        </div>
      }

      <div class="compose-layout" [class.has-project]="selectedProject">
        <div class="project-list">
          <label class="search-field">Projektfilter <input [(ngModel)]="projectFilter" placeholder="Name, Kategorie, Datei…" /></label>
          @for (project of visibleProjects(); track project.project_id) {
            <article class="project-row" [class.selected]="selectedProject?.project_id === project.project_id" (click)="selectProject(project)" tabindex="0" (keydown.enter)="selectProject(project)">
              <span class="stack-icon">▦</span>
              <div><strong>{{ project.name }}</strong><div class="muted font-sm">{{ project.category }} · {{ project.marker }} · {{ project.services?.length || 0 }} Services</div><code>{{ shortFiles(project) }}</code></div>
              <span class="profile-count">{{ project.profiles?.length || 0 }} Profile</span>
            </article>
          } @empty { <div class="empty-state">Keine registrierten Compose-Projekte verfügbar.</div> }
        </div>

        @if (selectedProject) {
          <div class="project-detail">
            <div class="row flex-between"><div><h5>{{ selectedProject.name }}</h5><code>{{ selectedProject.project_id }}</code></div><div class="row"><button type="button" class="secondary btn-small" (click)="loadProjectStatus()" [disabled]="statusLoading">Status aktualisieren</button><button type="button" class="secondary btn-small" (click)="closeProject()">Schließen</button></div></div>
            @if (selectedProject.error) { <div class="state-banner error">{{ selectedProject.error.message || selectedProject.error.code }}</div> }
            <div class="project-meta">
              <div><span>Verzeichnis</span><code>{{ selectedProject.project_directory }}</code></div>
              <div><span>Compose-Dateien</span><code>{{ selectedProject.compose_files.join(', ') }}</code></div>
              <div><span>Aktive Profile</span><strong>{{ selectedProject.profiles.join(', ') || 'default' }}</strong></div>
              <div><span>Verfügbare Profile</span><strong>{{ selectedProject.available_profiles?.join(', ') || 'keine gemeldet' }}</strong></div>
            </div>
            <div class="action-row">
              <button type="button" class="secondary btn-small" (click)="projectAction('pull')" [disabled]="actionBusy || !isAllowed('pull')">Images pullen</button>
              <button type="button" class="primary btn-small" (click)="projectAction('up')" [disabled]="actionBusy || !isAllowed('up')">Stack starten / aktualisieren</button>
              <button type="button" class="secondary btn-small" (click)="projectAction('restart')" [disabled]="actionBusy || !isAllowed('restart')">Restart</button>
              <button type="button" class="secondary btn-small" (click)="projectAction('stop')" [disabled]="actionBusy || !isAllowed('stop')">Stop</button>
              <button type="button" class="danger btn-small" (click)="projectAction('down')" [disabled]="actionBusy || !isAllowed('down')">Down</button>
              <span class="spacer"></span>
              <button type="button" class="secondary btn-small" (click)="openProjectLogs()">Projekt-Logs</button>
              <button type="button" class="secondary btn-small" (click)="openConfig()">Aufgelöste Config</button>
            </div>
            <p class="muted font-sm no-margin">„Down“ entfernt keine Volumes. Volume-Löschung und Pruning bleiben ausdrücklich gesperrt.</p>

            @if (statusLoading) { <p class="muted">Service-Status wird geladen…</p> }
            <div class="table-scroll"><table class="table-full services-table"><thead><tr><th>Service</th><th>Status</th><th>Health</th><th>Image</th><th>Ports</th><th>Aktionen</th></tr></thead><tbody>
              @for (service of selectedProject.services || []; track service.name) {
                <tr><td><strong>{{ service.name }}</strong></td><td><span class="service-state" [class.running]="isServiceRunning(service)">{{ service.state || service.status || '–' }}</span></td><td>{{ service.health || '–' }}</td><td><code>{{ service.image || '–' }}</code></td><td>{{ service.ports || '–' }}</td><td><button type="button" class="secondary btn-small" (click)="openServiceLogs(service)">Logs</button><button type="button" class="secondary btn-small" (click)="projectAction('restart', service.name)" [disabled]="actionBusy || !isAllowed('restart')">Restart</button><button type="button" class="secondary btn-small" (click)="projectAction('stop', service.name)" [disabled]="actionBusy || !isAllowed('stop')">Stop</button></td></tr>
              } @empty { <tr><td colspan="6" class="muted">Keine laufenden Services gemeldet.</td></tr> }
            </tbody></table></div>
          </div>
        }
      </div>

      @if (outputVisible) {
        <app-ops-log-viewer [title]="outputTitle" [content]="outputContent()" [stderr]="output.stderr || ''" [error]="outputError" [loading]="outputLoading" [truncated]="!!output.truncated" [tail]="outputTail" [showTail]="outputMode === 'logs'" (reload)="reloadOutput($event)" (close)="outputVisible = false" />
      }

      @if (showApprovals) {
        <div class="approval-drawer"><div class="row flex-between"><h5>Ops-Freigaben</h5><button type="button" class="secondary btn-small" (click)="showApprovals = false">Schließen</button></div><app-ops-approval-panel [baseUrl]="baseUrl" [focusRequestId]="pendingApprovalId" [refreshGeneration]="approvalRefresh" (decided)="approvalDecided($event)" /></div>
      }
    </section>
  `,
  styles: [`
    .ops-section { display: grid; gap: 14px; }
    .section-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
    h4, h5 { margin: 0; }
    .compose-layout { display: grid; grid-template-columns: 1fr; gap: 12px; }
    .compose-layout.has-project { grid-template-columns: minmax(300px, .65fr) minmax(500px, 1.35fr); }
    .project-list { display: grid; gap: 5px; align-content: start; }
    .search-field { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
    .search-field input { flex: 1; }
    .project-row { display: grid; grid-template-columns: 32px minmax(0, 1fr) auto; gap: 9px; align-items: center; padding: 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-raised); cursor: pointer; }
    .project-row.selected { border-color: var(--accent); }
    .project-row code { display: block; margin-top: 3px; overflow-wrap: anywhere; }
    .stack-icon { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 6px; background: var(--tone-technical-bg); }
    .profile-count { font-size: 11px; white-space: nowrap; color: var(--muted); }
    .project-detail { display: grid; gap: 12px; align-content: start; padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-raised); }
    .project-meta { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .project-meta > div { display: grid; gap: 3px; padding: 9px; background: var(--surface-soft); border-radius: 7px; overflow-wrap: anywhere; }
    .project-meta span { color: var(--muted); font-size: 12px; }
    .action-row { display: flex; gap: 7px; align-items: center; flex-wrap: wrap; }
    .spacer { flex: 1 1 auto; }
    .service-state { display: inline-flex; padding: 2px 7px; border-radius: 999px; background: var(--tone-technical-bg); }
    .service-state.running { background: var(--tone-success-bg); color: var(--tone-success-text); }
    .services-table td { vertical-align: middle; }
    .empty-state { padding: 28px; border: 1px dashed var(--border); border-radius: 8px; text-align: center; color: var(--muted); }
    .approval-drawer { border: 1px solid var(--tone-warning); border-radius: 8px; padding: 12px; background: var(--warning-bg); }
    code { font-size: 12px; }
    @media (max-width: 1100px) { .compose-layout.has-project { grid-template-columns: 1fr; } }
    @media (max-width: 650px) { .project-meta { grid-template-columns: 1fr; } }
  `],
})
export class ComposeOpsComponent implements OnChanges {
  private api = inject(OpsApiClient);
  @Input({ required: true }) baseUrl = '';
  @Input() refreshGeneration = 0;

  projects: ComposeProjectSummary[] = [];
  projectFilter = '';
  selectedProject: ComposeProjectSummary | null = null;
  loading = false;
  loadError = '';
  statusLoading = false;
  actionBusy = false;
  actionMessage = '';
  actionError = false;
  pendingApprovalId = '';
  retryAction: RetryAction | null = null;
  showApprovals = false;
  approvalRefresh = 0;
  outputVisible = false;
  outputMode: 'logs' | 'config' = 'logs';
  outputService = '';
  outputTitle = '';
  output: OpsTextResult = { ok: true };
  outputLoading = false;
  outputError = '';
  outputTail = 200;

  ngOnChanges(changes: SimpleChanges): void {
    if (this.baseUrl && (changes['baseUrl'] || changes['refreshGeneration'])) this.loadProjects();
  }

  loadProjects(): void {
    if (!this.baseUrl) return;
    this.loading = true; this.loadError = '';
    this.api.listComposeProjects(this.baseUrl).subscribe({
      next: (data) => {
        this.projects = Array.isArray(data?.items) ? data.items : [];
        if (this.selectedProject) {
          this.selectedProject = this.projects.find((project) => project.project_id === this.selectedProject?.project_id) || null;
          if (this.selectedProject) this.loadProjectStatus();
        }
        this.loading = false;
      },
      error: (error) => { this.loading = false; this.loadError = this.errorMessage(error, 'Compose-Projekte konnten nicht geladen werden.'); },
    });
  }

  visibleProjects(): ComposeProjectSummary[] {
    const needle = this.projectFilter.trim().toLowerCase();
    if (!needle) return this.projects;
    return this.projects.filter((project) => [project.name, project.category, project.marker, project.project_directory, ...(project.compose_files || [])].some((value) => String(value || '').toLowerCase().includes(needle)));
  }

  selectProject(project: ComposeProjectSummary): void { this.selectedProject = project; this.outputVisible = false; this.loadProjectStatus(); }
  closeProject(): void { this.selectedProject = null; this.outputVisible = false; }

  loadProjectStatus(): void {
    if (!this.selectedProject) return;
    const id = this.selectedProject.project_id;
    this.statusLoading = true;
    this.api.getComposeProjectStatus(this.baseUrl, id).subscribe({
      next: (project) => {
        this.selectedProject = project;
        const index = this.projects.findIndex((row) => row.project_id === id);
        if (index >= 0) this.projects = this.projects.map((row, i) => i === index ? project : row);
        this.statusLoading = false;
      },
      error: (error) => {
        this.statusLoading = false;
        if (this.selectedProject) this.selectedProject = { ...this.selectedProject, error: this.errorDto(error) };
      },
    });
  }

  projectAction(action: ComposeAction, service = '', approvalId?: string): void {
    const project = this.selectedProject;
    if (!project) return;
    const target = service ? `${project.name}/${service}` : project.name;
    if (!approvalId && ['stop', 'restart', 'down'].includes(action) && !globalThis.confirm(`${target}: Compose „${action}“ ausführen?`)) return;
    const label = `${target}: ${action}`;
    const run = (grant?: string) => this.executeAction(label, (id) => this.api.runComposeProjectAction(this.baseUrl, project.project_id, action, service, id), run, grant);
    run(approvalId);
  }

  isAllowed(action: ComposeAction): boolean {
    const allowed = this.selectedProject?.allowed_actions;
    return !Array.isArray(allowed) || allowed.length === 0 || allowed.includes(action);
  }

  isServiceRunning(service: ComposeServiceStatus): boolean { const state = String(service.state || service.status || '').toLowerCase(); return state.includes('running') || state.startsWith('up'); }

  openProjectLogs(): void { this.openLogs(''); }
  openServiceLogs(service: ComposeServiceStatus): void { this.openLogs(service.name); }
  private openLogs(service: string): void { this.outputMode = 'logs'; this.outputService = service; this.outputTitle = `Compose-Logs · ${this.selectedProject?.name}${service ? ` / ${service}` : ''}`; this.outputVisible = true; this.loadOutput(this.outputTail); }
  openConfig(): void { this.outputMode = 'config'; this.outputService = ''; this.outputTitle = `Aufgelöste Compose-Config · ${this.selectedProject?.name}`; this.outputVisible = true; this.loadOutput(this.outputTail); }
  reloadOutput(tail: number): void { this.loadOutput(tail); }

  outputContent(): string { return this.outputMode === 'config' ? this.output.config || '' : this.output.logs || ''; }

  loadOutput(tail: number): void {
    if (!this.selectedProject) return;
    this.outputTail = tail; this.outputLoading = true; this.outputError = '';
    const call = this.outputMode === 'config'
      ? this.api.getComposeProjectConfig(this.baseUrl, this.selectedProject.project_id)
      : this.api.getComposeProjectLogs(this.baseUrl, this.selectedProject.project_id, this.outputService || undefined, tail);
    call.subscribe({
      next: (output) => { this.output = output; this.outputLoading = false; this.outputError = output.error?.message || (output.ok ? '' : output.stderr || 'Ausgabe konnte nicht geladen werden.'); },
      error: (error) => { this.outputLoading = false; this.outputError = this.errorMessage(error, 'Ausgabe konnte nicht geladen werden.'); },
    });
  }

  retryApprovedAction(): void { const retry = this.retryAction; if (retry) { this.retryAction = null; retry.run(retry.approvalId); } }

  approvalDecided(event: { request: { request_id: string }; decision: 'granted' | 'denied' }): void {
    if (event.request.request_id !== this.pendingApprovalId) return;
    if (event.decision === 'granted' && this.retryAction) { this.actionError = false; this.actionMessage = 'Freigabe erteilt. Prüfen Sie Stack und Service und führen Sie die Aktion bewusst erneut aus.'; }
    else if (event.decision === 'denied') { this.retryAction = null; this.pendingApprovalId = ''; this.actionError = true; this.actionMessage = 'Die Compose-Aktion wurde abgelehnt.'; }
  }

  shortFiles(project: ComposeProjectSummary): string { return (project.compose_files || []).map((file) => file.split('/').slice(-3).join('/')).join(', '); }

  private executeAction(label: string, call: (approvalId?: string) => ReturnType<OpsApiClient['runComposeProjectAction']>, retry: (approvalId?: string) => void, approvalId?: string): void {
    this.actionBusy = true; this.actionError = false; this.actionMessage = `${label} wird ausgeführt…`;
    call(approvalId).subscribe({
      next: (result) => {
        this.actionBusy = false;
        if (result.ok) { this.pendingApprovalId = ''; this.retryAction = null; this.actionMessage = `${label} erfolgreich.`; this.loadProjects(); return; }
        this.handleBlockedAction(label, result, retry);
      },
      error: (error) => { this.actionBusy = false; this.actionError = true; this.actionMessage = this.errorMessage(error, `${label} fehlgeschlagen.`); },
    });
  }

  private handleBlockedAction(label: string, result: OpsActionResult, retry: (approvalId?: string) => void): void {
    const id = result.approval_id || '';
    if (result.decision === 'approval_required' || result.error?.code === 'approval_required') { this.pendingApprovalId = id; this.retryAction = id ? { approvalId: id, label, run: retry } : null; this.showApprovals = true; this.approvalRefresh += 1; this.actionMessage = `${label} benötigt eine Freigabe.`; }
    else { this.actionError = true; this.actionMessage = result.error?.message || result.error?.code || `${label} wurde abgelehnt.`; }
  }

  private errorDto(error: any): { code: string; message: string } { let body = error?.error; for (let i = 0; i < 4; i += 1) body = body?.data ?? body; return body?.error || { code: 'compose_request_failed', message: error?.message || 'Compose-Anfrage fehlgeschlagen.' }; }
  private errorMessage(error: any, fallback: string): string { const dto = this.errorDto(error); return dto.message || dto.code || fallback; }
}
