import { Component, Input, OnChanges, SimpleChanges, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { catchError, forkJoin, of } from 'rxjs';
import { OpsApiClient } from '../../services/ops-api.client';
import {
  GitActivityEntry,
  GitBranchSummary,
  GitChangedFile,
  GitCommitSummary,
  GitDiff,
  GitRemoteSummary,
  GitStatus,
  GitWorkspaceSummary,
  OpsActionResult,
} from './ops.models';
import { GitDiffViewerComponent } from './git-diff-viewer.component';
import { OpsApprovalPanelComponent } from './ops-approval-panel.component';

type GitView = 'changes' | 'history' | 'branches' | 'remotes' | 'activity';
type RetryAction = { approvalId: string; label: string; run: (approvalId?: string) => void };

@Component({
  selector: 'app-git-ops',
  standalone: true,
  imports: [CommonModule, FormsModule, GitDiffViewerComponent, OpsApprovalPanelComponent],
  template: `
    <section class="ops-section">
      <div class="section-toolbar">
        <div>
          <h4>Git-Verwaltung</h4>
          <p class="muted no-margin">Repository-Zustand, Ananta-Aktivität und kontrollierte Änderungen über den Hub.</p>
        </div>
        <div class="row">
          <label class="workspace-select">Workspace
            <select [(ngModel)]="workspaceId" (ngModelChange)="workspaceChanged()" [disabled]="loading">
              @for (workspace of workspaces; track workspace.workspace_id) {
                <option [value]="workspace.workspace_id">{{ workspace.label || workspace.workspace_id }}</option>
              }
            </select>
          </label>
          <button type="button" class="secondary" (click)="loadAll()" [disabled]="loading">{{ loading ? 'Lädt…' : 'Aktualisieren' }}</button>
        </div>
      </div>

      @if (loadError) { <div class="state-banner error">{{ loadError }}</div> }
      @if (readWarnings.length) { <div class="state-banner warning">Teilansichten nicht verfügbar: {{ readWarnings.join(' · ') }}</div> }
      @if (status?.error) { <div class="state-banner error">{{ status?.error?.message || status?.error?.code }}</div> }
      @if (status) {
        <div class="git-summary">
          <div><span class="muted">Branch</span><strong>{{ status.branch || '(detached)' }}</strong></div>
          <div><span class="muted">HEAD</span><code>{{ shortSha(status.head_sha) || '-' }}</code></div>
          <div><span class="muted">Upstream</span><strong>{{ status.upstream || 'nicht gesetzt' }}</strong></div>
          <div><span class="muted">Synchronisation</span><strong>{{ syncLabel() }}</strong></div>
          <div><span class="muted">Arbeitskopie</span><strong [class.warning]="status.dirty">{{ status.dirty ? changedFiles.length + ' Änderungen' : 'sauber' }}</strong></div>
          <div><span class="muted">Git-Vorgang</span><strong>{{ status.operation_state || 'bereit' }}</strong></div>
        </div>
      }

      <div class="git-actions">
        <label class="sync-target">Remote
          <select [(ngModel)]="syncRemote" [disabled]="actionBusy || !remotes.length">
            @for (remote of remotes; track remote.name) { <option [value]="remote.name">{{ remote.name }}</option> }
          </select>
        </label>
        <label class="sync-target">Zielbranch <input [(ngModel)]="syncBranch" placeholder="Branch" /></label>
        <button type="button" class="secondary" (click)="runSync('fetch')" [disabled]="actionBusy || !status || !remotes.length">Fetch</button>
        <button type="button" class="secondary" (click)="runSync('pull')" [disabled]="actionBusy || !status?.upstream || status?.can_pull === false">Pull (fast-forward)</button>
        <button type="button" class="primary" (click)="runSync('push')" [disabled]="actionBusy || status?.can_push === false">Push</button>
        <span class="muted font-sm">Netzwerkaktionen zeigen vor der Ausführung Ziel-Remote und Branch.</span>
      </div>

      @if (actionMessage) {
        <div class="state-banner" [class.error]="actionError" [class.warning]="pendingApprovalId">
          {{ actionMessage }}
          @if (pendingApprovalId) {
            <button type="button" class="secondary btn-small" (click)="showApprovals = true">Freigabe {{ shortSha(pendingApprovalId) }} öffnen</button>
          }
          @if (retryAction) {
            <button type="button" class="primary btn-small" (click)="retryApprovedAction()">{{ retryAction.label }} erneut ausführen</button>
          }
        </div>
      }

      <nav class="subtabs" aria-label="Git Bereiche">
        <button type="button" [class.active]="view === 'changes'" (click)="view = 'changes'">Änderungen <span>{{ changedFiles.length }}</span></button>
        <button type="button" [class.active]="view === 'history'" (click)="view = 'history'">Historie <span>{{ commits.length }}</span></button>
        <button type="button" [class.active]="view === 'branches'" (click)="view = 'branches'">Branches <span>{{ branches.length }}</span></button>
        <button type="button" [class.active]="view === 'remotes'" (click)="view = 'remotes'">Remotes <span>{{ remotes.length }}</span></button>
        <button type="button" [class.active]="view === 'activity'" (click)="view = 'activity'">Ananta-Aktivität <span>{{ activity.length }}</span></button>
      </nav>

      @if (view === 'changes') {
        <div class="changes-toolbar">
          <label class="search-field">Filter <input [(ngModel)]="fileFilter" placeholder="Pfad filtern…" /></label>
          <button type="button" class="secondary btn-small" (click)="toggleAllVisible()" [disabled]="!visibleFiles().length">
            {{ allVisibleSelected() ? 'Auswahl aufheben' : 'Alle sichtbaren wählen' }}
          </button>
          <span class="muted">{{ selectedPaths.size }} ausgewählt</span>
          <span class="spacer"></span>
          <button type="button" class="primary btn-small" (click)="stageSelected()" [disabled]="actionBusy || !hasSelectedUnstaged()">Stagen</button>
          <button type="button" class="secondary btn-small" (click)="unstageSelected()" [disabled]="actionBusy || !hasSelectedStaged()">Unstagen</button>
          <button type="button" class="danger btn-small" (click)="discardSelected()" [disabled]="actionBusy || !hasDiscardableSelected()">Getrackte Änderungen verwerfen</button>
        </div>
        <p class="muted font-sm no-margin">Nicht getrackte Dateien werden von „Verwerfen“ nie gelöscht. Konflikte müssen explizit im Git-Workflow aufgelöst werden.</p>
        @if (!changedFiles.length && !loading) { <div class="empty-state">Die Arbeitskopie ist sauber.</div> }
        @if (changedFiles.length && !visibleFiles().length) { <div class="empty-state">Keine Änderung passt zum Filter.</div> }
        @if (visibleFiles().length) {
          <div class="table-scroll">
            <table class="table-full changes-table">
              <thead><tr><th><span class="sr-only">Auswahl</span></th><th>Status</th><th>Pfad</th><th>Δ</th><th>Staged</th><th>Unstaged</th><th>Diff</th></tr></thead>
              <tbody>
                @for (file of visibleFiles(); track file.path) {
                  <tr [class.selected-row]="selectedPaths.has(file.path)">
                    <td><input type="checkbox" [checked]="selectedPaths.has(file.path)" (change)="togglePath(file.path)" [attr.aria-label]="file.path + ' auswählen'" /></td>
                    <td><span class="file-status" [class.conflict]="file.conflicted">{{ statusLabel(file) }}</span></td>
                    <td><button type="button" class="path-button" (click)="openDiff(file, !!file.staged && !file.unstaged)">{{ file.path }}</button></td>
                    <td><span class="success">+{{ file.additions || 0 }}</span> <span class="danger">−{{ file.deletions || 0 }}</span></td>
                    <td>{{ file.staged ? (file.index_status || 'ja') : '–' }}</td>
                    <td>{{ file.untracked ? 'neu' : file.unstaged ? (file.worktree_status || 'ja') : '–' }}</td>
                    <td><button type="button" class="secondary btn-small" (click)="openDiff(file, false)">Arbeitskopie</button> @if (file.staged) { <button type="button" class="secondary btn-small" (click)="openDiff(file, true)">Staging</button> }</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }

        @if (selectedDiffPath || diffLoading || diffError) {
          <app-git-diff-viewer
            [workspaceId]="workspaceId" [path]="selectedDiffPath" [scope]="diffScope"
            [diff]="diffContent()" [truncated]="!!diff?.truncated" [loading]="diffLoading" [error]="diffError"
            (modeChange)="changeDiffMode($event)" (close)="closeDiff()" />
        }

        <div class="commit-card">
          <div>
            <strong>Commit erstellen</strong>
            <p class="muted font-sm no-margin">Es werden ausschließlich bereits gestagte Änderungen committet.</p>
          </div>
          <textarea rows="3" [(ngModel)]="commitMessage" placeholder="type(scope): kurze, präzise Beschreibung"></textarea>
          <div class="row flex-between">
            <span class="muted font-sm">{{ stagedCount() }} Datei(en) in der Staging Area</span>
            <button type="button" class="primary" (click)="commit()" [disabled]="actionBusy || !stagedCount() || status?.can_commit === false || commitMessage.trim().length < 3">Commit erstellen</button>
          </div>
        </div>
      }

      @if (view === 'history') {
        @if (!commits.length) { <div class="empty-state">Keine Commit-Historie verfügbar.</div> }
        <div class="timeline">
          @for (commit of commits; track commit.sha) {
            <article><code>{{ commit.short_sha || shortSha(commit.sha) }}</code><div><strong>{{ commit.subject }}</strong>@if (commit.refs?.length) { <div class="ref-list">@for (ref of commit.refs; track ref) { <span>{{ ref }}</span> }</div> }<div class="muted font-sm">{{ commit.author_name || 'unbekannter Autor' }} @if (commit.authored_at) { · {{ commit.authored_at | date:'dd.MM.yyyy HH:mm:ss' }} }</div></div></article>
          }
        </div>
      }

      @if (view === 'branches') {
        <div class="table-scroll"><table class="table-full"><thead><tr><th>Branch</th><th>Typ</th><th>Upstream</th><th>Abweichung</th><th>SHA</th></tr></thead><tbody>
          @for (branch of branches; track branch.name) {
            <tr [class.current-branch]="branch.current"><td><strong>{{ branch.current ? '● ' : '' }}{{ branch.name }}</strong><div class="muted font-sm">{{ branch.last_commit_subject || '' }}</div></td><td>{{ branch.remote ? 'Remote' : 'Lokal' }}</td><td>{{ branch.upstream || '–' }}</td><td>↑ {{ branch.ahead || 0 }} · ↓ {{ branch.behind || 0 }}</td><td><code>{{ shortSha(branch.sha) }}</code></td></tr>
          } @empty { <tr><td colspan="5" class="muted">Keine Branch-Daten verfügbar.</td></tr> }
        </tbody></table></div>
      }

      @if (view === 'remotes') {
        <div class="table-scroll"><table class="table-full"><thead><tr><th>Name</th><th>Fetch-URL</th><th>Push-URL</th></tr></thead><tbody>
          @for (remote of remotes; track remote.name) { <tr><td><strong>{{ remote.name }}</strong></td><td><code>{{ remote.fetch_url || '–' }}</code></td><td><code>{{ remote.push_url || '–' }}</code></td></tr> }
          @empty { <tr><td colspan="3" class="muted">Keine Remotes konfiguriert.</td></tr> }
        </tbody></table></div>
      }

      @if (view === 'activity') {
        <p class="muted">Hub-seitig beobachtete Git-Aktionen machen auch interne Ananta-Änderungen nachvollziehbar.</p>
        <div class="timeline activity-timeline">
          @for (entry of activity; track $index) {
            <article><span class="activity-icon">{{ activityIcon(entry) }}</span><div><strong>{{ entry.operation || entry.action || 'Git-Aktion' }}</strong><div>{{ entry.summary || 'Git-Aktion' }}</div><div class="muted font-sm">{{ entry.actor || entry.source || 'Ananta' }} @if (entry.timestamp) { · {{ entry.timestamp | date:'dd.MM.yyyy HH:mm:ss' }} } @if (entry.outcome) { · {{ entry.outcome }} } @if (entry.task_id) { · Task {{ entry.task_id }} }</div></div></article>
          } @empty { <div class="empty-state">Noch keine Git-Aktivität im verfügbaren Audit-Fenster.</div> }
        </div>
      }

      @if (showApprovals) {
        <div class="approval-drawer">
          <div class="row flex-between"><h5>Ops-Freigaben</h5><button type="button" class="secondary btn-small" (click)="showApprovals = false">Schließen</button></div>
          <app-ops-approval-panel [baseUrl]="baseUrl" [focusRequestId]="pendingApprovalId" [refreshGeneration]="approvalRefresh" (decided)="approvalDecided($event)" />
        </div>
      }
    </section>
  `,
  styles: [`
    .ops-section { display: grid; gap: 14px; }
    .section-toolbar, .changes-toolbar, .git-actions { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; }
    h4, h5 { margin: 0; }
    .workspace-select { display: inline-flex; gap: 7px; align-items: center; font-size: 13px; }
    .sync-target { display: inline-flex; gap: 6px; align-items: center; font-size: 12px; }
    .sync-target input, .sync-target select { max-width: 180px; }
    .git-summary { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
    .git-summary > div { display: grid; gap: 3px; padding: 11px; border-right: 1px solid var(--border); background: var(--surface-raised); }
    .git-summary > div:last-child { border-right: 0; }
    .subtabs { display: flex; gap: 3px; border-bottom: 1px solid var(--border); overflow-x: auto; }
    .subtabs button { border: 0; border-radius: 6px 6px 0 0; background: transparent; white-space: nowrap; }
    .subtabs button.active { color: var(--accent); box-shadow: inset 0 -2px var(--accent); }
    .subtabs span { display: inline-block; min-width: 18px; padding: 1px 5px; border-radius: 999px; background: var(--border); font-size: 11px; }
    .search-field { display: inline-flex; gap: 7px; align-items: center; }
    .spacer { flex: 1 1 auto; }
    .changes-table td { vertical-align: middle; }
    .selected-row { background: color-mix(in srgb, var(--accent) 8%, transparent); }
    .file-status { display: inline-flex; padding: 2px 6px; border-radius: 4px; background: var(--tone-technical-bg); font: 11px ui-monospace, monospace; }
    .file-status.conflict { background: var(--danger-bg); color: var(--tone-error-text); }
    .path-button { border: 0; padding: 0; background: transparent; color: var(--accent); font: 12px ui-monospace, monospace; text-align: left; overflow-wrap: anywhere; }
    .commit-card { display: grid; grid-template-columns: minmax(170px, .65fr) minmax(280px, 1.35fr); gap: 12px; border: 1px solid var(--border); border-radius: 8px; padding: 12px; background: var(--surface-raised); }
    .commit-card textarea { resize: vertical; }
    .commit-card .row { grid-column: 1 / -1; }
    .empty-state { padding: 28px; border: 1px dashed var(--border); border-radius: 8px; text-align: center; color: var(--muted); }
    .timeline { display: grid; }
    .timeline article { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); }
    .ref-list { display: flex; flex-wrap: wrap; gap: 4px; margin: 4px 0; }
    .ref-list span { padding: 1px 5px; border-radius: 999px; background: var(--tone-technical-bg); color: var(--muted); font-size: 11px; }
    .activity-timeline article { grid-template-columns: 34px minmax(0, 1fr); }
    .activity-icon { display: grid; place-items: center; width: 28px; height: 28px; border-radius: 50%; background: var(--tone-technical-bg); }
    .current-branch { background: color-mix(in srgb, var(--tone-success) 9%, transparent); }
    .approval-drawer { border: 1px solid var(--tone-warning); border-radius: 8px; padding: 12px; background: var(--warning-bg); }
    code { font-size: 12px; overflow-wrap: anywhere; }
    .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
    @media (max-width: 1150px) { .git-summary { grid-template-columns: repeat(3, 1fr); } .git-summary > div:nth-child(3) { border-right: 0; } }
    @media (max-width: 700px) { .git-summary { grid-template-columns: 1fr 1fr; } .commit-card { grid-template-columns: 1fr; } .commit-card .row { grid-column: auto; } }
  `],
})
export class GitOpsComponent implements OnChanges {
  private api = inject(OpsApiClient);
  @Input({ required: true }) baseUrl = '';
  @Input() refreshGeneration = 0;

  workspaceId = 'repo';
  workspaces: GitWorkspaceSummary[] = [{ workspace_id: 'repo', label: 'Ananta Repository', is_default: true }];
  status: GitStatus | null = null;
  changedFiles: GitChangedFile[] = [];
  commits: GitCommitSummary[] = [];
  branches: GitBranchSummary[] = [];
  remotes: GitRemoteSummary[] = [];
  syncRemote = '';
  syncBranch = '';
  activity: GitActivityEntry[] = [];
  view: GitView = 'changes';
  fileFilter = '';
  selectedPaths = new Set<string>();
  selectedDiffPath = '';
  diffScope: 'unstaged' | 'staged' | 'combined' = 'unstaged';
  diff: GitDiff | null = null;
  diffLoading = false;
  diffError = '';
  commitMessage = '';
  loading = false;
  loadError = '';
  readWarnings: string[] = [];
  actionBusy = false;
  actionMessage = '';
  actionError = false;
  pendingApprovalId = '';
  retryAction: RetryAction | null = null;
  showApprovals = false;
  approvalRefresh = 0;

  ngOnChanges(changes: SimpleChanges): void {
    if (!this.baseUrl) return;
    if (changes['baseUrl']) this.loadWorkspaces();
    else if (changes['refreshGeneration']) this.loadAll();
  }

  loadWorkspaces(): void {
    this.api.listGitWorkspaces(this.baseUrl).pipe(catchError(() => of({ items: this.workspaces, count: this.workspaces.length }))).subscribe((data) => {
      const items = Array.isArray(data?.items) && data.items.length ? data.items : this.workspaces;
      this.workspaces = items;
      if (!items.some((item) => item.workspace_id === this.workspaceId)) this.workspaceId = items.find((item) => item.is_default)?.workspace_id || items[0].workspace_id;
      this.loadAll();
    });
  }

  loadAll(): void {
    if (!this.baseUrl || !this.workspaceId) return;
    this.loading = true;
    this.loadError = '';
    this.readWarnings = [];
    forkJoin({
      status: this.api.getGitStatus(this.baseUrl, this.workspaceId).pipe(catchError((error) => of({ workspace_id: this.workspaceId, branch: '', upstream: '', remote_name: '', dirty: false, changed_files: [], recent_commits: [], error: this.errorDto(error) } as GitStatus))),
      changes: this.api.getGitChanges(this.baseUrl, this.workspaceId).pipe(catchError(() => { this.readWarnings.push('Änderungen'); return of({ items: [], count: 0 }); })),
      history: this.api.getGitHistory(this.baseUrl, this.workspaceId).pipe(catchError(() => { this.readWarnings.push('Historie'); return of({ items: [], count: 0 }); })),
      branches: this.api.getGitBranches(this.baseUrl, this.workspaceId).pipe(catchError(() => { this.readWarnings.push('Branches'); return of({ items: [], count: 0 }); })),
      remotes: this.api.getGitRemotes(this.baseUrl, this.workspaceId).pipe(catchError(() => { this.readWarnings.push('Remotes'); return of({ items: [], count: 0 }); })),
      activity: this.api.getGitActivity(this.baseUrl, this.workspaceId).pipe(catchError(() => { this.readWarnings.push('Aktivität'); return of({ items: [], count: 0 }); })),
    }).subscribe({
      next: (data) => {
        this.status = data.status;
        this.changedFiles = this.readWarnings.includes('Änderungen')
          ? (Array.isArray(data.status.changed_files) ? data.status.changed_files : [])
          : this.items<GitChangedFile>(data.changes, 'changes');
        this.commits = this.items<GitCommitSummary>(data.history, 'commits', data.status.recent_commits || []);
        this.branches = this.items<GitBranchSummary>(data.branches, 'branches');
        this.remotes = this.items<GitRemoteSummary>(data.remotes, 'remotes');
        if (!this.remotes.some((remote) => remote.name === this.syncRemote)) this.syncRemote = data.status.remote_name || this.remotes[0]?.name || '';
        if (!this.syncBranch) {
          const prefix = this.syncRemote ? `${this.syncRemote}/` : '';
          this.syncBranch = prefix && data.status.upstream?.startsWith(prefix)
            ? data.status.upstream.slice(prefix.length)
            : data.status.branch || '';
        }
        this.activity = this.items<GitActivityEntry>(data.activity, 'activity');
        this.selectedPaths = new Set([...this.selectedPaths].filter((path) => this.changedFiles.some((file) => file.path === path)));
        this.loading = false;
      },
      error: (error) => {
        this.loading = false;
        this.loadError = this.errorMessage(error, 'Git-Daten konnten nicht geladen werden.');
      },
    });
  }

  workspaceChanged(): void {
    this.selectedPaths.clear();
    this.syncRemote = '';
    this.syncBranch = '';
    this.closeDiff();
    this.loadAll();
  }

  visibleFiles(): GitChangedFile[] {
    const needle = this.fileFilter.trim().toLowerCase();
    return needle ? this.changedFiles.filter((file) => file.path.toLowerCase().includes(needle)) : this.changedFiles;
  }

  togglePath(path: string): void {
    if (this.selectedPaths.has(path)) this.selectedPaths.delete(path);
    else this.selectedPaths.add(path);
  }

  toggleAllVisible(): void {
    const visible = this.visibleFiles();
    if (this.allVisibleSelected()) visible.forEach((file) => this.selectedPaths.delete(file.path));
    else visible.forEach((file) => this.selectedPaths.add(file.path));
  }

  allVisibleSelected(): boolean {
    const visible = this.visibleFiles();
    return !!visible.length && visible.every((file) => this.selectedPaths.has(file.path));
  }

  hasSelectedUnstaged(): boolean { return this.selectedFiles().some((file) => file.unstaged || file.untracked); }
  hasSelectedStaged(): boolean { return this.selectedFiles().some((file) => file.staged); }
  hasDiscardableSelected(): boolean { return this.selectedFiles().some((file) => file.unstaged && !file.untracked && !file.conflicted); }
  stagedCount(): number { return this.changedFiles.filter((file) => file.staged).length; }

  openDiff(file: GitChangedFile, cached: boolean): void {
    this.selectedDiffPath = file.path;
    this.diffScope = cached ? 'staged' : 'unstaged';
    this.loadDiff();
  }

  changeDiffMode(scope: 'unstaged' | 'staged' | 'combined'): void { this.diffScope = scope; this.loadDiff(); }
  closeDiff(): void { this.selectedDiffPath = ''; this.diff = null; this.diffError = ''; this.diffLoading = false; }

  loadDiff(): void {
    this.diffLoading = true;
    this.diffError = '';
    this.api.getGitDiff(this.baseUrl, this.workspaceId, { path: this.selectedDiffPath, cached: this.diffScope === 'staged', scope: this.diffScope }).subscribe({
      next: (diff) => { this.diff = diff; this.diffLoading = false; this.diffError = diff.error?.message || ''; },
      error: (error) => { this.diff = null; this.diffLoading = false; this.diffError = this.errorMessage(error, 'Diff konnte nicht geladen werden.'); },
    });
  }

  diffContent(): string {
    if (!this.diff) return '';
    if (this.diffScope !== 'combined') return this.diff.diff || '';
    const parts: string[] = [];
    if (this.diff.staged_diff) parts.push('### STAGING AREA ###\n' + this.diff.staged_diff);
    if (this.diff.unstaged_diff) parts.push('### ARBEITSKOPIE ###\n' + this.diff.unstaged_diff);
    if (this.diff.untracked_diff) parts.push('### NEUE DATEIEN ###\n' + this.diff.untracked_diff);
    return parts.join('\n\n') || this.diff.diff || '';
  }

  stageSelected(approvalId?: string): void {
    const paths = this.selectedFiles().filter((file) => file.unstaged || file.untracked).map((file) => file.path);
    if (!paths.length) return;
    const run = (grant?: string) => this.executeAction('Auswahl stagen', (id) => this.api.stageGitPaths(this.baseUrl, this.workspaceId, paths, false, id), run, grant);
    run(approvalId);
  }

  unstageSelected(approvalId?: string): void {
    const paths = this.selectedFiles().filter((file) => file.staged).map((file) => file.path);
    if (!paths.length) return;
    const run = (grant?: string) => this.executeAction('Auswahl unstagen', (id) => this.api.unstageGitPaths(this.baseUrl, this.workspaceId, paths, id), run, grant);
    run(approvalId);
  }

  discardSelected(approvalId?: string): void {
    const paths = this.selectedFiles().filter((file) => file.unstaged && !file.untracked && !file.conflicted).map((file) => file.path);
    if (!paths.length) return;
    if (!approvalId && !globalThis.confirm(`Lokale Änderungen in ${paths.length} Datei(en) unwiderruflich verwerfen?\n\n${paths.join('\n')}`)) return;
    const run = (grant?: string) => this.executeAction('Lokale Änderungen verwerfen', (id) => this.api.discardGitPaths(this.baseUrl, this.workspaceId, paths, id), run, grant);
    run(approvalId);
  }

  commit(approvalId?: string): void {
    const message = this.commitMessage.trim();
    if (!message) return;
    const run = (grant?: string) => this.executeAction('Commit erstellen', (id) => this.api.commitGit(this.baseUrl, this.workspaceId, message, id), run, grant, () => { this.commitMessage = ''; });
    run(approvalId);
  }

  runSync(action: 'fetch' | 'pull' | 'push', approvalId?: string): void {
    const remote = this.syncRemote || undefined;
    const branch = this.syncBranch || this.status?.branch || undefined;
    const target = `${remote || this.status?.upstream || this.status?.remote_name || 'konfiguriertes Remote'} (${branch || 'HEAD'})`;
    if (!approvalId && (action === 'pull' || action === 'push') && !globalThis.confirm(`${action === 'pull' ? 'Änderungen abrufen' : 'Commits übertragen'}: ${target}?`)) return;
    const label = action === 'fetch' ? 'Remote-Status abrufen' : action === 'pull' ? 'Fast-forward Pull' : 'Push';
    const call = (grant?: string) => action === 'fetch'
      ? this.api.fetchGit(this.baseUrl, this.workspaceId, { remote, approvalId: grant })
      : action === 'pull'
        ? this.api.pullGit(this.baseUrl, this.workspaceId, { remote, branch, approvalId: grant })
        : this.api.pushGit(this.baseUrl, this.workspaceId, { remote, branch, approvalId: grant });
    const run = (grant?: string) => this.executeAction(label, call, run, grant);
    run(approvalId);
  }

  retryApprovedAction(): void {
    const retry = this.retryAction;
    if (!retry) return;
    this.retryAction = null;
    retry.run(retry.approvalId);
  }

  approvalDecided(event: { request: { request_id: string }; decision: 'granted' | 'denied' }): void {
    if (event.request.request_id !== this.pendingApprovalId) return;
    if (event.decision === 'granted' && this.retryAction) {
      this.actionError = false;
      this.actionMessage = 'Freigabe erteilt. Prüfen Sie Ziel und Auswahl und führen Sie die Aktion bewusst erneut aus.';
    } else if (event.decision === 'denied') {
      this.retryAction = null;
      this.pendingApprovalId = '';
      this.actionError = true;
      this.actionMessage = 'Die Git-Aktion wurde abgelehnt.';
    }
  }

  syncLabel(): string {
    const ahead = Number(this.status?.ahead || 0);
    const behind = Number(this.status?.behind || 0);
    if (!this.status?.upstream) return 'kein Upstream';
    if (!ahead && !behind) return 'synchron';
    return `↑ ${ahead} · ↓ ${behind}`;
  }

  statusLabel(file: GitChangedFile): string {
    if (file.conflicted) return 'Konflikt';
    if (file.untracked) return 'Neu';
    if (file.deleted || file.index_status === 'D' || file.worktree_status === 'D') return 'Gelöscht';
    if (file.renamed || file.index_status === 'R' || file.worktree_status === 'R') return 'Umbenannt';
    return 'Geändert';
  }

  shortSha(value?: string | null): string { return String(value || '').slice(0, 12); }
  activityIcon(entry: GitActivityEntry): string { const op = String(entry.operation || entry.action || '').toLowerCase(); return op.includes('commit') ? '●' : op.includes('push') ? '↑' : op.includes('pull') || op.includes('fetch') ? '↓' : '◇'; }

  private selectedFiles(): GitChangedFile[] { return this.changedFiles.filter((file) => this.selectedPaths.has(file.path)); }

  private executeAction(
    label: string,
    call: (approvalId?: string) => ReturnType<OpsApiClient['commitGit']>,
    retry: (approvalId?: string) => void,
    approvalId?: string,
    onSuccess?: () => void,
  ): void {
    this.actionBusy = true;
    this.actionMessage = `${label} wird ausgeführt…`;
    this.actionError = false;
    call(approvalId).subscribe({
      next: (result) => {
        this.actionBusy = false;
        if (result.ok) {
          this.pendingApprovalId = '';
          this.retryAction = null;
          this.actionMessage = `${label} erfolgreich abgeschlossen.${result.audit_ref ? ` Audit: ${result.audit_ref}` : ''}`;
          onSuccess?.();
          this.loadAll();
          return;
        }
        this.handleBlockedAction(label, result, retry);
      },
      error: (error) => {
        this.actionBusy = false;
        this.actionError = true;
        this.actionMessage = this.errorMessage(error, `${label} fehlgeschlagen.`);
      },
    });
  }

  private handleBlockedAction(label: string, result: OpsActionResult, retry: (approvalId?: string) => void): void {
    const approvalId = result.approval_id || '';
    if (result.decision === 'approval_required' || result.error?.code === 'approval_required') {
      this.pendingApprovalId = approvalId;
      this.retryAction = approvalId ? { approvalId, label, run: retry } : null;
      this.showApprovals = true;
      this.approvalRefresh += 1;
      this.actionError = false;
      this.actionMessage = `${label} benötigt eine eng gebundene Freigabe.`;
    } else {
      this.actionError = true;
      this.actionMessage = result.error?.message || result.error?.code || `${label} wurde durch die Policy abgelehnt.`;
    }
  }

  private items<T>(data: unknown, alternateKey: string, fallback: T[] = []): T[] {
    const value = data as Record<string, unknown> | null;
    if (Array.isArray(value?.['items'])) return value!['items'] as T[];
    if (Array.isArray(value?.[alternateKey])) return value![alternateKey] as T[];
    return fallback;
  }

  private errorDto(error: any): { code: string; message: string } {
    let body = error?.error;
    for (let i = 0; i < 4; i += 1) body = body?.data ?? body;
    return body?.error || { code: 'git_load_failed', message: error?.message || 'Git-Status konnte nicht geladen werden.' };
  }

  private errorMessage(error: any, fallback: string): string {
    let body = error?.error;
    for (let i = 0; i < 4; i += 1) body = body?.data ?? body;
    return body?.error?.message || body?.error?.code || body?.message || error?.message || fallback;
  }
}
