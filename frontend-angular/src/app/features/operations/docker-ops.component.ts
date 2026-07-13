import { CommonModule } from '@angular/common';
import { Component, Input, OnChanges, SimpleChanges, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { catchError, forkJoin, of } from 'rxjs';
import { OpsApiClient } from '../../services/ops-api.client';
import {
  DockerContainerDetails,
  DockerContainerStats,
  DockerContainerSummary,
  DockerDiskUsage,
  DockerEngineStatus,
  DockerImageSummary,
  DockerInfo,
  DockerNetworkSummary,
  DockerVolumeSummary,
  OpsActionResult,
  OpsTextResult,
} from './ops.models';
import { OpsApprovalPanelComponent } from './ops-approval-panel.component';
import { OpsLogViewerComponent } from './ops-log-viewer.component';

type DockerView = 'containers' | 'images' | 'networks' | 'volumes' | 'storage';
type ContainerAction = 'start' | 'stop' | 'restart';
type RetryAction = { approvalId: string; label: string; run: (approvalId?: string) => void };

@Component({
  selector: 'app-docker-ops',
  standalone: true,
  imports: [CommonModule, FormsModule, OpsLogViewerComponent, OpsApprovalPanelComponent],
  template: `
    <section class="ops-section">
      <div class="section-toolbar">
        <div><h4>Docker-Verwaltung</h4><p class="muted no-margin">Engine, Ressourcen und kontrollierte Container-Aktionen über die konfigurierte Hub-Boundary.</p></div>
        <button type="button" class="secondary" (click)="loadAll()" [disabled]="loading">{{ loading ? 'Lädt…' : 'Aktualisieren' }}</button>
      </div>
      @if (loadError) { <div class="state-banner error">{{ loadError }}</div> }
      @if (engine?.available && resourceWarnings.length) { <div class="state-banner warning">Teilansichten nicht verfügbar: {{ resourceWarnings.join(' · ') }}</div> }
      @if (engine?.error) {
        <div class="state-banner warning">
          <strong>{{ engine?.error?.code }}</strong> · {{ engine?.error?.message }}
          @if (engine?.platform_hint) { <div class="font-sm">{{ engine?.platform_hint }}</div> }
        </div>
      }
      @if (engine) {
        <div class="engine-summary">
          <div><span class="muted">Boundary</span><strong>{{ engine.boundary }}</strong></div>
          <div><span class="muted">Engine</span><strong [class.success]="engine.available" [class.danger]="!engine.available">{{ engine.available ? 'erreichbar' : 'nicht erreichbar' }}</strong></div>
          <div><span class="muted">Docker</span><strong>{{ engine.docker_version || info?.server_version || '–' }}</strong></div>
          <div><span class="muted">Compose</span><strong>{{ engine.compose_available ? 'verfügbar' : 'nicht verfügbar' }}</strong></div>
          <div><span class="muted">Container</span><strong>{{ containers.length }}</strong></div>
          <div><span class="muted">Host</span><strong>{{ info?.name || info?.operating_system || '–' }}</strong></div>
        </div>
      }
      @if (actionMessage) {
        <div class="state-banner" [class.error]="actionError" [class.warning]="pendingApprovalId">
          {{ actionMessage }}
          @if (pendingApprovalId) { <button type="button" class="secondary btn-small" (click)="showApprovals = true">Freigabe öffnen</button> }
          @if (retryAction) { <button type="button" class="primary btn-small" (click)="retryApprovedAction()">{{ retryAction.label }} erneut ausführen</button> }
        </div>
      }

      <nav class="subtabs" aria-label="Docker Bereiche">
        <button type="button" [class.active]="view === 'containers'" (click)="view = 'containers'">Container <span>{{ containers.length }}</span></button>
        <button type="button" [class.active]="view === 'images'" (click)="view = 'images'">Images <span>{{ images.length }}</span></button>
        <button type="button" [class.active]="view === 'networks'" (click)="view = 'networks'">Netzwerke <span>{{ networks.length }}</span></button>
        <button type="button" [class.active]="view === 'volumes'" (click)="view = 'volumes'">Volumes <span>{{ volumes.length }}</span></button>
        <button type="button" [class.active]="view === 'storage'" (click)="view = 'storage'">Speicher</button>
      </nav>

      @if (view === 'containers') {
        <div class="list-layout" [class.has-detail]="selectedContainer">
          <div class="container-list">
            <label class="search-field">Filter <input [(ngModel)]="containerFilter" placeholder="Name, Image, Projekt…" /></label>
            @for (container of visibleContainers(); track container.id) {
              <article class="container-row" [class.selected]="selectedContainer?.id === container.id" (click)="selectContainer(container)" tabindex="0" (keydown.enter)="selectContainer(container)">
                <span class="state-dot" [class.running]="isRunning(container)" [class.unhealthy]="container.health === 'unhealthy'"></span>
                <div class="container-main"><strong>{{ container.name }}</strong><code>{{ container.image }}</code><span class="muted font-sm">{{ container.compose_project || 'standalone' }} @if (container.compose_service) { · {{ container.compose_service }} }</span></div>
                <div class="container-state"><strong>{{ container.state || container.status }}</strong><span class="muted font-sm">{{ container.health || container.uptime || '' }}</span></div>
                @if (container.registered === false) { <span class="readonly-badge">nur lesen</span> }
              </article>
            } @empty { <div class="empty-state">Keine Container verfügbar.</div> }
          </div>
          @if (selectedContainer) {
            <aside class="container-detail">
              <div class="row flex-between"><div><h5>{{ selectedContainer.name }}</h5><code>{{ selectedContainer.id }}</code></div><button type="button" class="secondary btn-small" (click)="closeContainer()">Schließen</button></div>
              <div class="action-row">
                <button type="button" class="primary btn-small" (click)="containerAction('start')" [disabled]="actionBusy || isRunning(selectedContainer) || !actionAllowed('start')">Start</button>
                <button type="button" class="secondary btn-small" (click)="containerAction('restart')" [disabled]="actionBusy || !isRunning(selectedContainer) || !actionAllowed('restart')">Restart</button>
                <button type="button" class="danger btn-small" (click)="containerAction('stop')" [disabled]="actionBusy || !isRunning(selectedContainer) || !actionAllowed('stop')">Stop</button>
                <button type="button" class="secondary btn-small" (click)="openLogs()">Logs</button>
                <button type="button" class="secondary btn-small" (click)="loadContainerDetails()" [disabled]="detailLoading">Details neu laden</button>
              </div>
              @if (selectedContainer.registered === false) { <div class="state-banner warning font-sm">Dieser Container ist nicht in der erlaubten Ops-Registry eingetragen. Mutationen sind gesperrt.</div> }
              @if (detailError) { <div class="state-banner error">{{ detailError }}</div> }
              @if (detailLoading) { <p class="muted">Details und Live-Statistik werden geladen…</p> }
              <div class="metric-grid">
                <div><span>CPU</span><strong>{{ percent(stats?.cpu_percent) }}</strong></div>
                <div><span>RAM</span><strong>{{ bytes(stats?.memory_usage) }} <small>{{ percent(stats?.memory_percent) }}</small></strong></div>
                <div><span>PIDs</span><strong>{{ stats?.pids ?? '–' }}</strong></div>
                <div><span>Netz I/O</span><strong>{{ printable(stats?.net_io || stats?.network_io) }}</strong></div>
              </div>
              <dl class="detail-list">
                <dt>Status</dt><dd>{{ selectedContainer.state || selectedContainer.status }}</dd>
                <dt>Ports</dt><dd>{{ selectedContainer.ports || '–' }}</dd>
                <dt>Command</dt><dd><code>{{ details?.command || selectedContainer.command || '–' }}</code></dd>
                <dt>Restart Policy</dt><dd>{{ printable(details?.restart_policy || details?.inspect?.restart_policy) }}</dd>
                <dt>Netzwerke</dt><dd>{{ printable(details?.networks || details?.inspect?.networks || selectedContainer.networks) }}</dd>
                <dt>Mounts</dt><dd>{{ printable(details?.mounts || details?.inspect?.mounts || selectedContainer.mounts) }}</dd>
                <dt>Ressourcen</dt><dd>{{ printable(details?.inspect?.resources) }}</dd>
              </dl>
            </aside>
          }
        </div>
        @if (showLogs && selectedContainer) {
          <app-ops-log-viewer [title]="'Container-Logs · ' + selectedContainer.name" [content]="logs.logs || ''" [stderr]="logs.stderr || ''" [error]="logError" [loading]="logsLoading" [truncated]="!!logs.truncated" [tail]="logTail" (reload)="loadLogs($event)" (close)="showLogs = false" />
        }
      }

      @if (view === 'images') {
        <p class="muted font-sm">Images werden bewusst nur lesend dargestellt; Löschen und Pruning bleiben außerhalb der sicheren Ops-Oberfläche.</p>
        <div class="table-scroll"><table class="table-full"><thead><tr><th>Repository</th><th>Tag</th><th>ID / Digest</th><th>Erstellt</th><th>Größe</th></tr></thead><tbody>
          @for (item of images; track item.id) { <tr><td>{{ item.repository || '–' }}</td><td>{{ item.tag || '–' }}</td><td><code>{{ item.digest || item.id }}</code></td><td>{{ item.created_at || '–' }}</td><td>{{ bytes(item.size) }}</td></tr> }
          @empty { <tr><td colspan="5" class="muted">Keine Image-Daten verfügbar.</td></tr> }
        </tbody></table></div>
      }

      @if (view === 'networks') {
        <div class="resource-grid">
          @for (item of networks; track item.id) { <article><div class="row flex-between"><strong>{{ item.name }}</strong><code>{{ item.id.slice(0, 12) }}</code></div><dl><dt>Driver</dt><dd>{{ item.driver || '–' }}</dd><dt>Scope</dt><dd>{{ item.scope || '–' }}</dd><dt>Container</dt><dd>{{ item.containers ?? '–' }}</dd><dt>Flags</dt><dd>{{ truthy(item.internal) ? 'intern' : 'extern' }} · {{ truthy(item.ipv6) ? 'IPv6' : 'IPv4' }}</dd></dl></article> }
          @empty { <div class="empty-state">Keine Netzwerk-Daten verfügbar.</div> }
        </div>
      }

      @if (view === 'volumes') {
        <p class="muted font-sm">Mountpoints und Label-Werte können sensible Pfade enthalten und werden serverseitig begrenzt beziehungsweise maskiert.</p>
        <div class="resource-grid">
          @for (item of volumes; track item.name) { <article><div class="row flex-between"><strong>{{ item.name }}</strong><span [class.success]="item.in_use">{{ item.in_use === true ? 'in Verwendung' : item.in_use === false ? 'ungenutzt' : 'Nutzung nicht gemeldet' }}</span></div><dl><dt>Driver</dt><dd>{{ item.driver || '–' }}</dd><dt>Scope</dt><dd>{{ item.scope || '–' }}</dd><dt>Host-Mountpoint</dt><dd><code>{{ item.mountpoint || 'aus Sicherheitsgründen nicht offengelegt' }}</code></dd></dl></article> }
          @empty { <div class="empty-state">Keine Volume-Daten verfügbar.</div> }
        </div>
      }

      @if (view === 'storage') {
        <div class="storage-grid">
          @for (row of diskRows(); track row.label) { <article><span class="muted">{{ row.label }}</span><strong>{{ bytes(row.value?.size) }}</strong><div class="font-sm">{{ row.value?.active || 0 }} aktiv von {{ row.value?.count || 0 }} · {{ bytes(row.value?.reclaimable) }} reclaimable</div></article> }
        </div>
        <div class="host-details"><h5>Engine-Informationen</h5><dl class="detail-list"><dt>Betriebssystem</dt><dd>{{ info?.operating_system || '–' }}</dd><dt>Architektur</dt><dd>{{ info?.architecture || '–' }}</dd><dt>Kernel</dt><dd>{{ info?.kernel_version || '–' }}</dd><dt>Storage Driver</dt><dd>{{ info?.storage_driver || info?.driver || '–' }}</dd><dt>CPU / RAM</dt><dd>{{ info?.cpus || '–' }} CPU · {{ bytes(info?.memory_total || info?.memory_bytes) }}</dd></dl></div>
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
    .engine-summary { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
    .engine-summary > div { display: grid; gap: 3px; padding: 11px; border-right: 1px solid var(--border); background: var(--surface-raised); }
    .subtabs { display: flex; gap: 3px; border-bottom: 1px solid var(--border); overflow-x: auto; }
    .subtabs button { border: 0; border-radius: 6px 6px 0 0; background: transparent; white-space: nowrap; }
    .subtabs button.active { color: var(--accent); box-shadow: inset 0 -2px var(--accent); }
    .subtabs span { padding: 1px 5px; border-radius: 999px; background: var(--border); font-size: 11px; }
    .list-layout { display: grid; grid-template-columns: 1fr; gap: 12px; }
    .list-layout.has-detail { grid-template-columns: minmax(320px, .8fr) minmax(420px, 1.2fr); }
    .container-list { display: grid; gap: 5px; align-content: start; }
    .search-field { display: flex; align-items: center; gap: 8px; margin-bottom: 5px; }
    .search-field input { flex: 1; }
    .container-row { display: grid; grid-template-columns: 14px minmax(0, 1fr) auto auto; align-items: center; gap: 10px; padding: 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-raised); cursor: pointer; }
    .container-row.selected { border-color: var(--accent); }
    .container-main, .container-state { display: grid; gap: 2px; }
    .container-main code { overflow-wrap: anywhere; }
    .container-state { text-align: right; }
    .state-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); }
    .state-dot.running { background: var(--tone-success); box-shadow: 0 0 0 3px color-mix(in srgb, var(--tone-success) 20%, transparent); }
    .state-dot.unhealthy { background: var(--tone-error); }
    .readonly-badge { padding: 2px 6px; border-radius: 999px; background: var(--tone-technical-bg); font-size: 10px; }
    .container-detail { display: grid; gap: 12px; align-content: start; padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-raised); }
    .action-row { display: flex; gap: 7px; flex-wrap: wrap; }
    .metric-grid, .storage-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
    .metric-grid > div, .storage-grid article { display: grid; gap: 3px; padding: 10px; border-radius: 7px; background: var(--surface-soft); }
    .detail-list, .resource-grid dl { display: grid; grid-template-columns: 125px minmax(0, 1fr); gap: 7px 12px; margin: 0; }
    dt { color: var(--muted); } dd { margin: 0; overflow-wrap: anywhere; }
    .resource-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }
    .resource-grid article, .host-details { padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--surface-raised); }
    .empty-state { padding: 28px; border: 1px dashed var(--border); border-radius: 8px; text-align: center; color: var(--muted); }
    .approval-drawer { border: 1px solid var(--tone-warning); border-radius: 8px; padding: 12px; background: var(--warning-bg); }
    code { font-size: 12px; }
    @media (max-width: 1050px) { .engine-summary { grid-template-columns: repeat(3, 1fr); } .list-layout.has-detail { grid-template-columns: 1fr; } }
    @media (max-width: 650px) { .engine-summary, .metric-grid, .storage-grid { grid-template-columns: 1fr 1fr; } }
  `],
})
export class DockerOpsComponent implements OnChanges {
  private api = inject(OpsApiClient);
  @Input({ required: true }) baseUrl = '';
  @Input() refreshGeneration = 0;

  view: DockerView = 'containers';
  engine: DockerEngineStatus | null = null;
  info: DockerInfo | null = null;
  containers: DockerContainerSummary[] = [];
  images: DockerImageSummary[] = [];
  networks: DockerNetworkSummary[] = [];
  volumes: DockerVolumeSummary[] = [];
  diskUsage: DockerDiskUsage | null = null;
  containerFilter = '';
  selectedContainer: DockerContainerSummary | null = null;
  details: DockerContainerDetails | null = null;
  stats: DockerContainerStats | null = null;
  detailLoading = false;
  detailError = '';
  showLogs = false;
  logs: OpsTextResult = { ok: true };
  logsLoading = false;
  logError = '';
  logTail = 200;
  loading = false;
  loadError = '';
  resourceWarnings: string[] = [];
  actionBusy = false;
  actionMessage = '';
  actionError = false;
  pendingApprovalId = '';
  retryAction: RetryAction | null = null;
  showApprovals = false;
  approvalRefresh = 0;

  ngOnChanges(changes: SimpleChanges): void {
    if (this.baseUrl && (changes['baseUrl'] || changes['refreshGeneration'])) this.loadAll();
  }

  loadAll(): void {
    if (!this.baseUrl) return;
    this.loading = true;
    this.loadError = '';
    this.resourceWarnings = [];
    forkJoin({
      engine: this.api.getDockerStatus(this.baseUrl).pipe(catchError((error) => of(this.errorEngine(error)))),
      info: this.api.getDockerInfo(this.baseUrl).pipe(catchError(() => { this.resourceWarnings.push('Engine-Info'); return of(null); })),
      containers: this.api.listDockerContainers(this.baseUrl).pipe(catchError(() => { this.resourceWarnings.push('Container'); return of({ items: [], count: 0 }); })),
      images: this.api.listDockerImages(this.baseUrl).pipe(catchError(() => { this.resourceWarnings.push('Images'); return of({ items: [], count: 0 }); })),
      networks: this.api.listDockerNetworks(this.baseUrl).pipe(catchError(() => { this.resourceWarnings.push('Netzwerke'); return of({ items: [], count: 0 }); })),
      volumes: this.api.listDockerVolumes(this.baseUrl).pipe(catchError(() => { this.resourceWarnings.push('Volumes'); return of({ items: [], count: 0 }); })),
      disk: this.api.getDockerDiskUsage(this.baseUrl).pipe(catchError(() => { this.resourceWarnings.push('Speicher'); return of(null); })),
    }).subscribe({
      next: (data) => {
        this.engine = data.engine;
        this.info = data.info;
        this.containers = this.items(data.containers);
        this.images = this.items(data.images);
        this.networks = this.items(data.networks);
        this.volumes = this.items(data.volumes);
        this.diskUsage = data.disk;
        if (this.selectedContainer) {
          this.selectedContainer = this.containers.find((item) => item.id === this.selectedContainer?.id) || null;
          if (this.selectedContainer) this.loadContainerDetails();
        }
        this.loading = false;
      },
      error: (error) => { this.loading = false; this.loadError = this.errorMessage(error, 'Docker-Daten konnten nicht geladen werden.'); },
    });
  }

  visibleContainers(): DockerContainerSummary[] {
    const needle = this.containerFilter.trim().toLowerCase();
    if (!needle) return this.containers;
    return this.containers.filter((item) => [item.name, item.image, item.compose_project, item.compose_service, item.status].some((value) => String(value || '').toLowerCase().includes(needle)));
  }

  selectContainer(container: DockerContainerSummary): void {
    this.selectedContainer = container;
    this.showLogs = false;
    this.loadContainerDetails();
  }

  closeContainer(): void { this.selectedContainer = null; this.details = null; this.stats = null; this.showLogs = false; }

  loadContainerDetails(): void {
    if (!this.selectedContainer) return;
    this.detailLoading = true;
    this.detailError = '';
    forkJoin({
      details: this.api.inspectDockerContainer(this.baseUrl, this.selectedContainer.id).pipe(catchError((error) => of({ ...this.selectedContainer!, error: this.errorDto(error) } as DockerContainerDetails))),
      stats: this.api.getDockerContainerStats(this.baseUrl, this.selectedContainer.id).pipe(catchError((error) => of({ id: this.selectedContainer!.id, error: this.errorDto(error) } as DockerContainerStats))),
    }).subscribe((data) => {
      this.details = data.details;
      this.stats = data.stats;
      this.detailError = data.stats.error?.message || (data.details as any).error?.message || '';
      this.detailLoading = false;
    });
  }

  openLogs(): void { this.showLogs = true; this.loadLogs(this.logTail); }
  loadLogs(tail: number): void {
    if (!this.selectedContainer) return;
    this.logTail = tail;
    this.logsLoading = true;
    this.logError = '';
    this.api.getDockerContainerLogs(this.baseUrl, this.selectedContainer.id, tail).subscribe({
      next: (data) => { this.logs = data; this.logsLoading = false; this.logError = data.error?.message || (data.ok ? '' : data.stderr || 'Logs konnten nicht geladen werden.'); },
      error: (error) => { this.logsLoading = false; this.logError = this.errorMessage(error, 'Logs konnten nicht geladen werden.'); },
    });
  }

  containerAction(action: ContainerAction, approvalId?: string): void {
    const container = this.selectedContainer;
    if (!container) return;
    if (!approvalId && action !== 'start' && !globalThis.confirm(`Container „${container.name}“ ${action === 'stop' ? 'stoppen' : 'neu starten'}?`)) return;
    const label = `${container.name}: ${action}`;
    const run = (grant?: string) => this.executeAction(label, (id) => this.api.runDockerContainerAction(this.baseUrl, container.id, action, id), run, grant);
    run(approvalId);
  }

  actionAllowed(action: ContainerAction): boolean {
    if (!this.selectedContainer || this.selectedContainer.registered === false) return false;
    const allowed = this.selectedContainer.allowed_actions;
    return !Array.isArray(allowed) || allowed.length === 0 || allowed.includes(action);
  }

  isRunning(container: DockerContainerSummary): boolean { return String(container.state || container.status || '').toLowerCase().includes('running') || String(container.status || '').toLowerCase().startsWith('up'); }

  retryApprovedAction(): void { const retry = this.retryAction; if (retry) { this.retryAction = null; retry.run(retry.approvalId); } }

  approvalDecided(event: { request: { request_id: string }; decision: 'granted' | 'denied' }): void {
    if (event.request.request_id !== this.pendingApprovalId) return;
    if (event.decision === 'granted' && this.retryAction) {
      this.actionError = false;
      this.actionMessage = 'Freigabe erteilt. Prüfen Sie den Container und führen Sie die Aktion bewusst erneut aus.';
    } else if (event.decision === 'denied') {
      this.retryAction = null; this.pendingApprovalId = ''; this.actionError = true; this.actionMessage = 'Die Container-Aktion wurde abgelehnt.';
    }
  }

  diskRows(): Array<{ label: string; value: DockerDiskUsage['images'] }> {
    return [
      { label: 'Images', value: this.diskUsage?.images },
      { label: 'Container', value: this.diskUsage?.containers },
      { label: 'Volumes', value: this.diskUsage?.volumes },
      { label: 'Build Cache', value: this.diskUsage?.build_cache },
    ];
  }

  percent(value?: number | string | null): string {
    const normalized = typeof value === 'string' ? value.replace('%', '').trim() : value;
    return Number.isFinite(Number(normalized)) ? `${Number(normalized).toFixed(1)} %` : '–';
  }
  bytes(value?: number | string | null): string {
    if (typeof value === 'string' && value && !/^\d+(\.\d+)?$/.test(value)) return value;
    let bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return '–';
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']; let unit = 0;
    while (bytes >= 1024 && unit < units.length - 1) { bytes /= 1024; unit += 1; }
    return `${bytes.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  }

  printable(value: unknown): string {
    if (value === null || value === undefined || value === '') return '–';
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) return value.map((item) => this.printable(item)).join(', ') || '–';
    try { return JSON.stringify(value); } catch { return '–'; }
  }

  truthy(value: unknown): boolean {
    return value === true || ['1', 'true', 'yes', 'on'].includes(String(value || '').trim().toLowerCase());
  }

  private executeAction(label: string, call: (approvalId?: string) => ReturnType<OpsApiClient['runDockerContainerAction']>, retry: (approvalId?: string) => void, approvalId?: string): void {
    this.actionBusy = true; this.actionError = false; this.actionMessage = `${label} wird ausgeführt…`;
    call(approvalId).subscribe({
      next: (result) => {
        this.actionBusy = false;
        if (result.ok) { this.pendingApprovalId = ''; this.retryAction = null; this.actionMessage = `${label} erfolgreich.`; this.loadAll(); return; }
        this.handleBlockedAction(label, result, retry);
      },
      error: (error) => { this.actionBusy = false; this.actionError = true; this.actionMessage = this.errorMessage(error, `${label} fehlgeschlagen.`); },
    });
  }

  private handleBlockedAction(label: string, result: OpsActionResult, retry: (approvalId?: string) => void): void {
    const id = result.approval_id || '';
    if (result.decision === 'approval_required' || result.error?.code === 'approval_required') {
      this.pendingApprovalId = id; this.retryAction = id ? { approvalId: id, label, run: retry } : null; this.showApprovals = true; this.approvalRefresh += 1; this.actionMessage = `${label} benötigt eine Freigabe.`;
    } else { this.actionError = true; this.actionMessage = result.error?.message || result.error?.code || `${label} wurde abgelehnt.`; }
  }

  private items<T>(value: { items?: T[] } | null | undefined): T[] { return Array.isArray(value?.items) ? value!.items! : []; }
  private errorEngine(error: any): DockerEngineStatus {
    let body = error?.error;
    for (let i = 0; i < 4; i += 1) body = body?.data ?? body;
    if (body && typeof body === 'object' && 'available' in body && 'boundary' in body) return body as DockerEngineStatus;
    const dto = this.errorDto(error);
    return { available: false, boundary: 'unknown', docker_version: '', compose_available: false, platform_hint: '', error: dto };
  }
  private errorDto(error: any): { code: string; message: string } { let body = error?.error; for (let i = 0; i < 4; i += 1) body = body?.data ?? body; return body?.error || { code: 'docker_request_failed', message: error?.message || 'Docker-Anfrage fehlgeschlagen.' }; }
  private errorMessage(error: any, fallback: string): string { const dto = this.errorDto(error); return dto.message || dto.code || fallback; }
}
