import { CommonModule } from '@angular/common';
import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  EffectiveWorkflowNode,
  EffectiveWorkflowOptions,
  EffectiveWorkflowRequest,
  EffectiveWorkflowResult,
  EffectiveWorkflowService,
} from '../services/effective-workflow.service';

@Component({
  standalone: true,
  selector: 'app-effective-workflow-explorer',
  imports: [CommonModule, FormsModule],
  template: `
    <div class="ew-root">
      <header class="ew-header">
        <div>
          <h2>Effective Workflow</h2>
          <p>Surface, Pfad und Task-Art auf die wirksame Blueprint-, Profil-, Worker-, Tool- und Policy-Kette auflösen.</p>
        </div>
        <div class="header-actions">
          <button class="secondary" (click)="reloadOptions()" [disabled]="loading">Aktualisieren</button>
          <button class="secondary" (click)="downloadSnapshot()" [disabled]="!result">Snapshot</button>
        </div>
      </header>

      <section class="query-band">
        <label>
          <span>Surface</span>
          <input [(ngModel)]="surface" list="ew-surfaces" placeholder="z.B. ai_snake_chat" />
        </label>
        <label>
          <span>Task-Art</span>
          <input [(ngModel)]="taskKind" list="ew-task-kinds" placeholder="z.B. bugfix, repair, review" />
        </label>
        <label>
          <span>Pfad</span>
          <input [(ngModel)]="path" list="ew-paths" placeholder="z.B. agent/routes/tasks/goals.py" />
        </label>
        <div class="query-actions">
          <button (click)="resolve()" [disabled]="loading || !surface.trim()">Auflösen</button>
          <button class="secondary" (click)="swapCompare()" [disabled]="!result">Vergleich übernehmen</button>
        </div>
      </section>

      <datalist id="ew-surfaces">
        <option *ngFor="let item of options?.surfaces || []" [value]="item"></option>
      </datalist>
      <datalist id="ew-task-kinds">
        <option *ngFor="let item of options?.task_kinds || []" [value]="item"></option>
      </datalist>
      <datalist id="ew-paths">
        <option *ngFor="let item of options?.path_suggestions || []" [value]="item"></option>
      </datalist>

      <div class="status error" *ngIf="error">{{ error }}</div>
      <div class="status" *ngIf="loading">Lade wirksame Workflow-Konfiguration...</div>

      <main class="ew-layout" *ngIf="result">
        <section class="summary-strip" [class.warning]="result.status === 'warning'" [class.blocked]="result.status === 'blocked'">
          <strong>{{ result.status | uppercase }}</strong>
          <span>{{ result.summary }}</span>
        </section>

        <aside class="left-pane">
          <div class="pane-head">Wirksame Kette</div>
          <button
            class="chain-node"
            *ngFor="let node of result.effective_chain"
            [class.active]="node.id === selectedNodeId"
            [class.readonly]="!node.writable"
            (click)="selectNode(node.id)">
            <span class="node-type">{{ node.node_type }}</span>
            <strong>{{ node.label }}</strong>
            <small>{{ node.source_file || node.source_kind || 'derived' }}</small>
          </button>

          <div class="pane-head spaced">Edit-Ziele</div>
          <a
            class="edit-link"
            *ngFor="let link of result.edit_links"
            [href]="asString(link['route'])">
            <span>{{ link['label'] }}</span>
            <small>{{ link['editor'] }} · {{ link['writable'] ? 'schreibbar' : 'read-only' }}</small>
          </a>
        </aside>

        <section class="center-pane">
          <div class="section-row">
            <article>
              <h3>Auswahl</h3>
              <dl class="kv">
                <div><dt>Profil</dt><dd>{{ profileLabel() }}</dd></div>
                <div><dt>Blueprint</dt><dd>{{ blueprintLabel() }}</dd></div>
                <div><dt>Teamtyp</dt><dd>{{ asString(result.selected['team_type']) || '-' }}</dd></div>
                <div><dt>Worker</dt><dd>{{ selectedCount('worker_routing') }}</dd></div>
                <div><dt>Tools</dt><dd>{{ toolsLabel() }}</dd></div>
                <div><dt>Write Policy</dt><dd>{{ writePolicyLabel() }}</dd></div>
              </dl>
            </article>

            <article *ngIf="result.warnings.length || result.blocked.length">
              <h3>Diagnostik</h3>
              <ul class="diag-list">
                <li *ngFor="let item of result.blocked" class="blocked">{{ item['code'] }}: {{ item['message'] }}</li>
                <li *ngFor="let item of result.warnings">{{ item['code'] }}: {{ item['message'] }}</li>
              </ul>
            </article>
          </div>

          <article class="node-detail" *ngIf="selectedNode() as node">
            <div class="detail-head">
              <div>
                <span class="node-type">{{ node.node_type }}</span>
                <h3>{{ node.label }}</h3>
              </div>
              <button class="secondary" (click)="explainSelectedNode()">Node erklären</button>
            </div>
            <dl class="kv">
              <div><dt>Quelle</dt><dd>{{ sourceLabel(node) }}</dd></div>
              <div><dt>Schreibbar</dt><dd>{{ node.writable ? 'ja' : node.readonly_reason || 'nein' }}</dd></div>
              <div><dt>Grund</dt><dd>{{ node.reason || '-' }}</dd></div>
            </dl>
            <div class="value-grid">
              <div>
                <h4>Deklariert</h4>
                <pre>{{ node.declared_value | json }}</pre>
              </div>
              <div>
                <h4>Wirksam</h4>
                <pre>{{ node.effective_value | json }}</pre>
              </div>
            </div>
          </article>

          <article *ngIf="nodeExplanation">
            <h3>Node-Erklärung</h3>
            <pre>{{ nodeExplanation | json }}</pre>
          </article>

          <article>
            <h3>Graph</h3>
            <div class="graph-list">
              <button
                *ngFor="let node of graphNodes()"
                [class.active]="node.id === selectedNodeId"
                (click)="selectNode(node.id)">
                <span class="node-type">{{ node.node_type }}</span>
                {{ node.label }}
              </button>
            </div>
          </article>

          <article>
            <h3>Raw JSON</h3>
            <pre>{{ result | json }}</pre>
          </article>
        </section>

        <aside class="right-pane">
          <div class="pane-head">Quellen</div>
          <div class="source-item" *ngFor="let item of result.source_index">
            <strong>{{ item['label'] }}</strong>
            <span>{{ item['source_file'] || item['source_kind'] }}</span>
            <small>{{ item['source_pointer'] }}</small>
          </div>

          <div class="pane-head spaced">Trace</div>
          <div class="trace-item" *ngFor="let item of result.explanation_trace">
            <strong>#{{ item['step'] }} {{ item['node_id'] }}</strong>
            <span>{{ item['message'] }}</span>
          </div>
        </aside>
      </main>

      <section class="compare-panel" *ngIf="compareBase">
        <div class="compare-head">
          <strong>Vergleich gegen aktuelle Auswahl</strong>
          <button class="secondary" (click)="compare()" [disabled]="loading || !surface.trim()">Vergleichen</button>
        </div>
        <pre *ngIf="compareResult">{{ compareResult | json }}</pre>
      </section>

      <section class="empty" *ngIf="!result && !loading">
        Surface auswählen und auflösen, um die wirksame Konfigurationskette zu sehen.
      </section>
    </div>
  `,
  styles: [`
    .ew-root { min-height:calc(100vh - 150px); background:var(--bg); color:var(--fg); }
    .ew-header { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; padding:18px 22px; border-bottom:1px solid var(--border); }
    .ew-header h2 { margin:0 0 4px; font-size:22px; }
    .ew-header p { margin:0; color:var(--muted); max-width:820px; }
    .header-actions { display:flex; gap:8px; }
    .query-band { display:grid; grid-template-columns:minmax(180px, 1fr) minmax(160px, 0.8fr) minmax(240px, 1.3fr) auto; gap:12px; align-items:end; padding:14px 22px; border-bottom:1px solid var(--border); background:var(--card-bg); }
    label { display:flex; flex-direction:column; gap:5px; font-size:12px; color:var(--muted); }
    input { width:100%; box-sizing:border-box; border:1px solid var(--border); border-radius:6px; background:var(--input-bg, var(--bg)); color:var(--fg); padding:8px; font:inherit; }
    button, .edit-link { border:1px solid var(--accent); border-radius:6px; background:var(--accent); color:var(--accent-contrast, #fff); padding:8px 10px; font:inherit; text-decoration:none; cursor:pointer; }
    button.secondary { background:transparent; color:var(--fg); border-color:var(--border); }
    button:disabled { opacity:.55; cursor:not-allowed; }
    .query-actions { display:flex; gap:8px; }
    .status, .empty, .compare-panel { margin:14px 22px; border:1px solid var(--border); border-radius:8px; padding:12px; background:var(--card-bg); color:var(--muted); }
    .status.error { border-color:#b3261e; color:#ff8a80; }
    .ew-layout { display:grid; grid-template-columns:280px minmax(0, 1fr) 300px; gap:0; min-height:calc(100vh - 275px); }
    .summary-strip { grid-column:1 / -1; display:flex; gap:12px; align-items:center; padding:10px 22px; border-bottom:1px solid var(--border); background:color-mix(in srgb, #2e7d32 14%, var(--card-bg)); }
    .summary-strip.warning { background:color-mix(in srgb, #f9a825 18%, var(--card-bg)); }
    .summary-strip.blocked { background:color-mix(in srgb, #b3261e 18%, var(--card-bg)); }
    .left-pane, .right-pane { padding:14px; border-right:1px solid var(--border); overflow:auto; }
    .right-pane { border-right:0; border-left:1px solid var(--border); }
    .center-pane { padding:14px; overflow:auto; display:grid; gap:14px; align-content:start; }
    .pane-head { color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; margin-bottom:8px; }
    .pane-head.spaced { margin-top:18px; }
    .chain-node, .edit-link, .source-item, .trace-item { width:100%; box-sizing:border-box; display:flex; flex-direction:column; gap:4px; text-align:left; margin-bottom:8px; border:1px solid var(--border); border-radius:8px; background:var(--card-bg); color:var(--fg); }
    .chain-node.active, .graph-list button.active { border-color:var(--accent); background:color-mix(in srgb, var(--accent) 12%, var(--card-bg)); }
    .chain-node.readonly { border-style:dashed; }
    .chain-node small, .edit-link small, .source-item small, .trace-item span { color:var(--muted); overflow-wrap:anywhere; }
    .node-type { display:inline-block; width:max-content; border:1px solid var(--border); border-radius:999px; padding:2px 7px; color:var(--muted); font-size:11px; }
    .section-row { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
    article { border:1px solid var(--border); border-radius:8px; background:var(--card-bg); padding:12px; min-width:0; }
    article h3, article h4 { margin:0 0 10px; }
    .detail-head, .compare-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }
    .detail-head h3 { margin:6px 0 0; }
    .kv { display:grid; gap:8px; margin:0; }
    .kv div { display:grid; grid-template-columns:120px minmax(0, 1fr); gap:10px; }
    .kv dt { color:var(--muted); }
    .kv dd { margin:0; overflow-wrap:anywhere; }
    .diag-list { margin:0; padding-left:18px; }
    .diag-list li { margin-bottom:6px; }
    .diag-list .blocked { color:#ff8a80; }
    .value-grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:12px; }
    pre { margin:0; border:1px solid var(--border); border-radius:8px; background:var(--bg); padding:10px; overflow:auto; max-height:430px; font-size:12px; }
    .graph-list { display:flex; flex-wrap:wrap; gap:8px; }
    .graph-list button { width:auto; max-width:260px; display:flex; align-items:center; gap:6px; background:var(--bg); color:var(--fg); border-color:var(--border); }
    .source-item, .trace-item { padding:9px; }
    @media (max-width: 1100px) {
      .query-band, .ew-layout, .section-row, .value-grid { grid-template-columns:1fr; }
      .summary-strip { grid-column:1; }
      .left-pane, .right-pane { border:0; border-bottom:1px solid var(--border); }
    }
  `],
})
export class EffectiveWorkflowExplorerComponent implements OnInit {
  private api = inject(EffectiveWorkflowService);

  options: EffectiveWorkflowOptions | null = null;
  result: EffectiveWorkflowResult | null = null;
  selectedNodeId = '';
  compareBase: EffectiveWorkflowRequest | null = null;
  compareResult: unknown = null;
  nodeExplanation: unknown = null;
  loading = false;
  error = '';

  surface = 'ai_snake_chat';
  taskKind = '';
  path = '';

  ngOnInit(): void {
    this.reloadOptions();
  }

  reloadOptions(): void {
    this.loading = true;
    this.error = '';
    this.api.getOptions().subscribe({
      next: options => {
        this.options = options;
        if (!this.surface && options.surfaces?.length) this.surface = options.surfaces[0];
      },
      error: err => this.error = this.errorMessage(err),
      complete: () => this.loading = false,
    });
  }

  resolve(): void {
    const request = this.currentRequest();
    this.loading = true;
    this.error = '';
    this.nodeExplanation = null;
    this.api.resolve(request).subscribe({
      next: result => {
        this.result = result;
        this.selectedNodeId = result.effective_chain?.[0]?.id || Object.keys(result.graph.nodes || {})[0] || '';
      },
      error: err => this.error = this.errorMessage(err),
      complete: () => this.loading = false,
    });
  }

  compare(): void {
    if (!this.compareBase) return;
    this.loading = true;
    this.error = '';
    this.api.compare(this.compareBase, this.currentRequest()).subscribe({
      next: result => this.compareResult = result,
      error: err => this.error = this.errorMessage(err),
      complete: () => this.loading = false,
    });
  }

  swapCompare(): void {
    if (!this.result) return;
    this.compareBase = { ...this.result.request };
    this.compareResult = null;
  }

  downloadSnapshot(): void {
    if (!this.result) return;
    const payload = {
      schema: 'ananta.effective_workflow.snapshot.v1',
      exported_at: new Date().toISOString(),
      request: this.result.request,
      result: this.result,
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `effective-workflow-${Date.now()}.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  selectNode(nodeId: string): void {
    this.selectedNodeId = nodeId;
    this.nodeExplanation = null;
  }

  explainSelectedNode(): void {
    if (!this.result || !this.selectedNodeId) return;
    this.api.explainNode(this.result.request, this.selectedNodeId).subscribe({
      next: explanation => this.nodeExplanation = explanation,
      error: err => this.error = this.errorMessage(err),
    });
  }

  selectedNode(): EffectiveWorkflowNode | null {
    if (!this.result || !this.selectedNodeId) return null;
    return this.result.graph.nodes[this.selectedNodeId] || null;
  }

  graphNodes(): EffectiveWorkflowNode[] {
    return Object.values(this.result?.graph?.nodes || {})
      .sort((left, right) => left.node_type.localeCompare(right.node_type) || left.label.localeCompare(right.label));
  }

  selectedCount(key: string): number {
    const value = this.result?.selected?.[key];
    return Array.isArray(value) ? value.length : 0;
  }

  profileLabel(): string {
    const profile = this.result?.selected?.['agent_profile'] as Record<string, unknown> | null | undefined;
    return this.asString(profile?.['profile_id']) || this.asString(profile?.['primary_role']) || '-';
  }

  blueprintLabel(): string {
    const blueprint = this.result?.selected?.['blueprint'] as Record<string, unknown> | null | undefined;
    return this.asString(blueprint?.['name']) || '-';
  }

  toolsLabel(): string {
    const tools = this.result?.selected?.['tools'] as Record<string, unknown> | null | undefined;
    const allowed = tools?.['allowed'];
    if (Array.isArray(allowed) && allowed.length) return allowed.join(', ');
    return tools?.['missing_policy'] ? 'default-deny, keine explizite Tool-Policy' : 'keine';
  }

  writePolicyLabel(): string {
    const policy = this.result?.selected?.['write_policy'] as Record<string, unknown> | null | undefined;
    if (!policy) return '-';
    return policy['code_generation_blocked'] ? 'Code-Generierung blockiert' : 'Code-Generierung nicht blockiert';
  }

  sourceLabel(node: EffectiveWorkflowNode): string {
    return [node.source_file, node.source_kind, node.source_pointer].filter(Boolean).join(' · ') || 'derived';
  }

  asString(value: unknown): string {
    if (value === null || value === undefined) return '';
    return String(value);
  }

  private currentRequest(): EffectiveWorkflowRequest {
    return {
      surface: this.surface.trim(),
      task_kind: this.taskKind.trim() || null,
      path: this.path.trim() || null,
      include_readonly: true,
      include_diagnostics: true,
      include_alternatives: true,
    };
  }

  private errorMessage(err: unknown): string {
    const payload = err as { error?: { error?: string; message?: string }; message?: string };
    return payload?.error?.error || payload?.error?.message || payload?.message || 'Effective Workflow konnte nicht geladen werden.';
  }
}
