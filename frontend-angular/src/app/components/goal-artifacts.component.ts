
import { HttpClient } from '@angular/common/http';
import { Component, OnInit, inject } from '@angular/core';

type SourceGrant = {
  grant_id: string;
  artifact_ref: string;
  data_boundary: string;
  sensitivity: string;
  allowed_usages: string[];
  policy_decision_ref?: string;
  revoked_at?: string;
  expires_at?: string;
};

type SourceUsage = {
  usage_id: string;
  grant_id: string;
  artifact_ref: string;
  usage_kind: string;
  task_id?: string;
  worker_id?: string;
};

type OutputArtifact = {
  output_artifact_id: string;
  artifact_type: string;
  status: string;
  task_id?: string;
  worker_id?: string;
  input_usage_refs?: string[];
  provenance_summary?: string;
  content_hash?: string;
};

type SourceCandidate = {
  artifact_ref: string;
  artifact_type: string;
  sensitivity: string;
  source_id?: string;
};

type GraphRow = {
  from: string;
  via: string;
  to: string;
};

export function buildProvenanceRows(usages: SourceUsage[], outputs: OutputArtifact[]): GraphRow[] {
  const usageById = new Map(usages.map(item => [item.usage_id, item] as const));
  const rows: GraphRow[] = [];
  for (const output of outputs) {
    const refs = Array.isArray(output.input_usage_refs) ? output.input_usage_refs : [];
    if (!refs.length) {
      rows.push({
        from: 'undocumented',
        via: `${output.worker_id || 'worker?'}:${output.task_id || 'task?'}`,
        to: output.output_artifact_id,
      });
      continue;
    }
    for (const ref of refs) {
      const usage = usageById.get(ref);
      if (!usage) continue;
      rows.push({
        from: usage.artifact_ref,
        via: `${usage.worker_id || output.worker_id || 'worker?'}:${usage.task_id || output.task_id || 'task?'}`,
        to: output.output_artifact_id,
      });
    }
  }
  return rows.slice(0, 120);
}

@Component({
  standalone: true,
  selector: 'app-goal-artifacts',
  imports: [],
  template: `
    <section class="card goal-artifacts-page">
      <div class="head">
        <div>
          <h2>Goal Artifacts</h2>
          <p class="muted">Freigegeben · Genutzt · Erzeugt inkl. Provenance pro Goal.</p>
        </div>
        <div class="goal-picker">
          <input
            class="input"
            [value]="goalId"
            (input)="goalId = getInputValue($event)"
            placeholder="goal-id"
            aria-label="Goal ID"
          />
          <button class="btn" (click)="loadGraph()" [disabled]="loading">Load</button>
          <button class="btn btn-secondary" (click)="openGrantDialog()">Grant source</button>
        </div>
      </div>

      @if (loading) {
        <p class="muted">Loading goal artifact graph ...</p>
      } @else if (error) {
        <p class="error">{{ error }}</p>
      } @else if (!goalId.trim()) {
        <p class="muted">Bitte Goal auswählen.</p>
      } @else {
        <div class="status-row">
          <span class="badge">Goal: {{ goalId }}</span>
          <span class="badge">Granted: {{ grants.length }}</span>
          <span class="badge">Used: {{ usages.length }}</span>
          <span class="badge">Outputs: {{ outputs.length }}</span>
        </div>

        <div class="columns">
          <article class="card card-light col">
            <h3>Sources Granted</h3>
            @if (!grants.length) {
              <p class="muted">Keine Freigaben.</p>
            } @else {
              @for (grant of grants; track grant.grant_id) {
                <div class="item">
                  <div class="item-head">
                    <strong>{{ grant.artifact_ref }}</strong>
                    <span class="badge">{{ grant.sensitivity }}</span>
                  </div>
                  <p class="line">grant: {{ grant.grant_id }}</p>
                  <p class="line">boundary: {{ grant.data_boundary }}</p>
                  <p class="line">usage: {{ grant.allowed_usages.join(', ') }}</p>
                  @if (grant.revoked_at) {
                    <p class="warn">revoked: {{ grant.revoked_at }}</p>
                  }
                  <div class="actions">
                    <button class="btn btn-secondary" (click)="copyText(grant.artifact_ref)">Copy ref</button>
                    <button class="btn btn-secondary" (click)="revokeGrant(grant)" [disabled]="Boolean(grant.revoked_at)">
                      Revoke
                    </button>
                  </div>
                </div>
              }
            }
          </article>

          <article class="card card-light col">
            <h3>Sources Used</h3>
            @if (!usages.length) {
              <p class="muted">Keine dokumentierte Nutzung.</p>
            } @else {
              @for (usage of usages; track usage.usage_id) {
                <div class="item">
                  <div class="item-head">
                    <strong>{{ usage.artifact_ref }}</strong>
                    <span class="badge">{{ usage.usage_kind }}</span>
                  </div>
                  <p class="line">usage: {{ usage.usage_id }}</p>
                  <p class="line">grant: {{ usage.grant_id }}</p>
                  <p class="line">task/worker: {{ usage.task_id || '-' }} / {{ usage.worker_id || '-' }}</p>
                </div>
              }
            }
          </article>

          <article class="card card-light col">
            <h3>Outputs</h3>
            @if (!outputs.length) {
              <p class="muted">Keine Outputs.</p>
            } @else {
              @for (output of outputs; track output.output_artifact_id) {
                <div class="item">
                  <div class="item-head">
                    <strong>{{ output.output_artifact_id }}</strong>
                    <span class="badge">{{ output.artifact_type }}</span>
                  </div>
                  <p class="line">status: {{ output.status }}</p>
                  <p class="line">task/worker: {{ output.task_id || '-' }} / {{ output.worker_id || '-' }}</p>
                  <p class="line">input refs: {{ (output.input_usage_refs || []).join(', ') || 'none' }}</p>
                  <p class="line compact">{{ output.provenance_summary || '-' }}</p>
                </div>
              }
            }
          </article>
        </div>

        <article class="card card-light mini-graph">
          <h3>Provenance Mini Graph</h3>
          @if (!graphRows.length) {
            <p class="muted">Keine Kanten vorhanden.</p>
          } @else {
            <div class="desktop-graph">
              @for (row of graphRows; track row.from + row.via + row.to) {
                <p class="line"><strong>{{ row.from }}</strong> → {{ row.via }} → <strong>{{ row.to }}</strong></p>
              }
            </div>
            <div class="mobile-fallback">
              @for (row of graphRows; track row.from + row.via + row.to) {
                <div class="item">
                  <p class="line">source: {{ row.from }}</p>
                  <p class="line">worker/task: {{ row.via }}</p>
                  <p class="line">output: {{ row.to }}</p>
                </div>
              }
            </div>
          }
        </article>
      }
    </section>

    @if (grantDialogOpen) {
      <section class="overlay" (click)="closeGrantDialog()">
        <article class="dialog card card-light" (click)="$event.stopPropagation()">
          <h3>Grant source for {{ goalId }}</h3>
          <input class="input" [value]="candidateSearch" (input)="candidateSearch = getInputValue($event)" placeholder="Filter candidates" />

          @if (candidateLoading) {
            <p class="muted">Loading candidates ...</p>
          } @else if (!filteredCandidates.length) {
            <p class="muted">Keine Candidates.</p>
          } @else {
            <div class="candidate-list">
              @for (candidate of filteredCandidates; track candidate.artifact_ref) {
                <button class="candidate" [class.active]="candidate.artifact_ref === selectedCandidateRef" (click)="selectedCandidateRef = candidate.artifact_ref">
                  <strong>{{ candidate.artifact_ref }}</strong>
                  <span>{{ candidate.artifact_type }} · {{ candidate.sensitivity }}</span>
                </button>
              }
            </div>
          }
          <div class="usage-box">
            @for (usage of usageOptions; track usage) {
              <label><input type="checkbox" [checked]="selectedUsages.has(usage)" (change)="toggleUsage(usage)" /> {{ usage }}</label>
            }
          </div>
          @if (grantWarning) {
            <p class="warn">{{ grantWarning }}</p>
          }
          <div class="dialog-actions">
            <button class="btn btn-secondary" (click)="closeGrantDialog()">Cancel</button>
            <button class="btn" (click)="submitGrant()" [disabled]="grantSubmitting || !selectedCandidateRef">
              {{ grantSubmitting ? 'Granting ...' : 'Grant' }}
            </button>
          </div>
        </article>
      </section>
    }
  `,
  styles: [`
    .goal-artifacts-page { max-width: 1200px; margin: 0 auto; }
    .head { display: flex; justify-content: space-between; align-items: end; gap: 12px; }
    .goal-picker { display: flex; gap: 8px; align-items: center; }
    .input { min-width: 180px; border: 1px solid var(--border); border-radius: 8px; padding: 6px 8px; background: var(--panel-2); color: var(--text); }
    .status-row { margin: 12px 0; display: flex; gap: 8px; flex-wrap: wrap; }
    .badge { border: 1px solid var(--border); border-radius: 999px; padding: 2px 8px; font-size: 12px; }
    .columns { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
    .col { display: flex; flex-direction: column; gap: 8px; }
    .item { border: 1px solid var(--border); border-radius: 8px; padding: 8px; display: flex; flex-direction: column; gap: 6px; }
    .item-head { display: flex; justify-content: space-between; gap: 8px; align-items: center; }
    .line { margin: 0; word-break: break-word; }
    .compact { font-size: 12px; color: var(--muted); }
    .actions { display: flex; gap: 8px; }
    .mini-graph { margin-top: 12px; }
    .desktop-graph { display: block; }
    .mobile-fallback { display: none; }
    .error { color: #c53030; }
    .warn { color: #975a16; }
    .overlay { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.55); display: grid; place-items: center; z-index: 25; }
    .dialog { width: min(760px, calc(100vw - 24px)); max-height: calc(100vh - 24px); overflow: auto; display: flex; flex-direction: column; gap: 10px; }
    .candidate-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 8px; }
    .candidate { text-align: left; border: 1px solid var(--border); border-radius: 8px; background: var(--panel); color: var(--text); padding: 8px; display: flex; flex-direction: column; gap: 4px; }
    .candidate.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
    .usage-box { display: flex; gap: 12px; flex-wrap: wrap; }
    .dialog-actions { display: flex; justify-content: end; gap: 8px; }
    @media (max-width: 900px) {
      .columns { grid-template-columns: 1fr; }
      .head { flex-direction: column; align-items: stretch; }
      .goal-picker { flex-wrap: wrap; }
    }
    @media (max-width: 680px) {
      .desktop-graph { display: none; }
      .mobile-fallback { display: flex; flex-direction: column; gap: 8px; }
      .candidate-list { grid-template-columns: 1fr; }
      .dialog-actions { justify-content: stretch; }
      .dialog-actions .btn { flex: 1; }
    }
  `],
})
export class GoalArtifactsComponent implements OnInit {
  private http = inject(HttpClient);
  goalId = '';
  grants: SourceGrant[] = [];
  usages: SourceUsage[] = [];
  outputs: OutputArtifact[] = [];
  graphRows: GraphRow[] = [];
  loading = false;
  error = '';

  grantDialogOpen = false;
  candidateLoading = false;
  candidates: SourceCandidate[] = [];
  candidateSearch = '';
  selectedCandidateRef = '';
  selectedUsages = new Set<string>(['read', 'use_as_context']);
  usageOptions = ['read', 'summarize', 'quote', 'transform', 'use_as_context'];
  grantSubmitting = false;
  grantWarning = '';

  get filteredCandidates(): SourceCandidate[] {
    const query = this.candidateSearch.trim().toLowerCase();
    if (!query) return this.candidates;
    return this.candidates.filter(item => [item.artifact_ref, item.artifact_type, item.sensitivity, item.source_id || '']
      .join(' ')
      .toLowerCase()
      .includes(query));
  }

  ngOnInit(): void {
    this.goalId = this.readGoalIdFromPath();
    if (this.goalId) this.loadGraph();
  }

  readGoalIdFromPath(): string {
    const params = new URLSearchParams(window.location.search);
    return String(params.get('goal') || '').trim();
  }

  getInputValue(event: Event): string {
    const target = event.target as HTMLInputElement | null;
    return String(target?.value || '');
  }

  loadGraph(): void {
    const goal = this.goalId.trim();
    if (!goal) return;
    this.loading = true;
    this.error = '';
    this.http.get<any>(`/goals/${encodeURIComponent(goal)}/artifacts/graph`).subscribe({
      next: (payload) => {
        const data = payload?.data || {};
        this.grants = Array.isArray(data?.source_grants) ? data.source_grants : [];
        this.usages = Array.isArray(data?.source_usages) ? data.source_usages : [];
        this.outputs = Array.isArray(data?.output_artifacts) ? data.output_artifacts : [];
        this.graphRows = buildProvenanceRows(this.usages, this.outputs);
        this.loading = false;
      },
      error: (err) => {
        this.error = String(err?.error?.error || err?.message || 'goal_artifacts_load_failed');
        this.loading = false;
      },
    });
  }

  openGrantDialog(): void {
    if (!this.goalId.trim()) {
      this.error = 'goal_id_required';
      return;
    }
    this.grantDialogOpen = true;
    this.grantWarning = '';
    this.loadCandidates();
  }

  closeGrantDialog(): void {
    this.grantDialogOpen = false;
  }

  loadCandidates(): void {
    this.candidateLoading = true;
    this.http.get<any>(`/goals/${encodeURIComponent(this.goalId.trim())}/artifacts/source-candidates`).subscribe({
      next: (payload) => {
        this.candidates = Array.isArray(payload?.data?.candidates) ? payload.data.candidates : [];
        if (!this.selectedCandidateRef && this.candidates.length) this.selectedCandidateRef = this.candidates[0].artifact_ref;
        this.candidateLoading = false;
      },
      error: (err) => {
        this.error = String(err?.error?.error || err?.message || 'goal_candidates_load_failed');
        this.candidateLoading = false;
      },
    });
  }

  toggleUsage(value: string): void {
    if (this.selectedUsages.has(value)) this.selectedUsages.delete(value);
    else this.selectedUsages.add(value);
  }

  submitGrant(): void {
    const artifactRef = this.selectedCandidateRef.trim();
    if (!artifactRef) return;
    this.grantSubmitting = true;
    this.grantWarning = '';
    const usages = Array.from(this.selectedUsages);
    const payload = {
      schema: 'source_artifact_grant.v1',
      grant_id: `grant-ui-${Date.now()}`,
      goal_id: this.goalId.trim(),
      artifact_ref: artifactRef,
      granted_by: 'angular_ui',
      granted_at: new Date().toISOString(),
      allowed_usages: usages.length ? usages : ['read', 'use_as_context'],
      data_boundary: 'project_private',
      sensitivity: 'internal',
      policy_decision_ref: 'ui-manual-policy',
    };
    this.http.post(`/goals/${encodeURIComponent(this.goalId.trim())}/artifacts/sources/grant`, payload).subscribe({
      next: () => {
        this.grantSubmitting = false;
        this.grantDialogOpen = false;
        this.loadGraph();
      },
      error: (err) => {
        const msg = String(err?.error?.error || err?.message || 'goal_grant_failed');
        if (msg.includes('cloud') || msg.includes('boundary') || msg.includes('sensitivity')) {
          this.grantWarning = `Policy warning: ${msg}`;
        } else {
          this.error = msg;
        }
        this.grantSubmitting = false;
      },
    });
  }

  revokeGrant(grant: SourceGrant): void {
    if (!grant.grant_id || grant.revoked_at) return;
    this.http.post(`/goals/${encodeURIComponent(this.goalId.trim())}/artifacts/sources/${encodeURIComponent(grant.grant_id)}/revoke`, {
      revoke_reason: 'angular_ui_revoke',
    }).subscribe({
      next: () => this.loadGraph(),
      error: (err) => {
        this.error = String(err?.error?.error || err?.message || 'goal_revoke_failed');
      },
    });
  }

  copyText(value: string): void {
    const text = String(value || '');
    if (!text) return;
    navigator.clipboard?.writeText(text).catch(() => {
      this.error = 'copy_failed';
    });
  }
}
