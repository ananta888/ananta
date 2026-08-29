import { Component, OnDestroy, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription, switchMap } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import {
  DspyOptimizationApiService,
  DspyOptimizationCapability,
  DspyOptimizationRun,
} from '../../services/dspy-optimization-api.service';

@Component({
  standalone: true,
  selector: 'app-dspy-optimization-workbench',
  imports: [FormsModule],
  template: `
    <main aria-labelledby="dspy-title">
      <header><p class="eyebrow">Hub-owned / optional engine</p><h1 id="dspy-title">DSPy Optimization</h1>
        <p>Experimente optimieren versionierte Prompt-Programme. DSPy ist weder Provider noch Orchestrator.</p></header>
      <section aria-labelledby="scope-title"><h2 id="scope-title">Tenant-Scope</h2>
        <label>Tenant ID <input [(ngModel)]="tenantId" maxlength="192" autocomplete="off" /></label>
        <button type="button" (click)="load()" [disabled]="loading">Capabilities und Runs laden</button>
        <p role="status" aria-live="polite"><code>{{ status }}</code></p></section>
      @if (capability) {
        <section aria-labelledby="capability-title"><h2 id="capability-title">Capability</h2>
          <dl><div><dt>State</dt><dd>{{ capability.state }}</dd></div><div><dt>Mode</dt><dd>{{ capability.mode }}</dd></div>
            <div><dt>Version</dt><dd>{{ capability.installedVersion || 'nicht installiert' }}</dd></div>
            <div><dt>Optimizer</dt><dd>{{ capability.optimizerCapabilities.join(', ') }}</dd></div>
            <div><dt>Programme</dt><dd>{{ capability.programKinds.join(', ') }}</dd></div></dl>
          <p>Fehlende Capabilities oder Evidence blockieren automatisch; kein Lauf wartet auf menschliche Eingabe.</p></section>
      }
      <section aria-labelledby="runs-title"><h2 id="runs-title">Runs</h2>
        @if (!runs.length) { <p>Keine Runs im gewaehlten Scope.</p> }
        @for (run of runs; track run.runId) {
          <article><h3>{{ run.runId }}</h3><p>{{ run.state }} · {{ run.reasonCode }} · Revision {{ run.revision }}</p>
            <code>{{ run.specDigest }}</code>
            @if (run.state === 'admitted' || run.state === 'running') {
              <button type="button" (click)="cancel(run)">Automatisch sicher abbrechen</button>
            }
          </article>
        }
      </section>
    </main>
  `,
  styles: [`
    :host{display:block;background:#f5f1e8;color:#17251f;min-height:100%}main{max-width:1100px;margin:auto;padding:clamp(18px,4vw,54px);font-family:system-ui,sans-serif}
    header{border-bottom:4px solid #17251f;margin-bottom:28px}h1{font:700 clamp(2.6rem,7vw,5.8rem)/.9 Georgia,serif;letter-spacing:-.05em;margin:.2em 0}h2,h3{font-family:Georgia,serif}
    .eyebrow{color:#a55b18;text-transform:uppercase;font-weight:800;letter-spacing:.14em}section,article{background:#fffdf7;border:1px solid #b9c5bd;padding:20px;margin:16px 0}article{border-left:7px solid #27614c}
    label{display:grid;gap:6px;max-width:420px;font-weight:700}input,button{min-height:44px;padding:8px 12px;border:2px solid #17251f;background:white}button{margin-top:12px;background:#27614c;color:white;font-weight:800;cursor:pointer}button:focus-visible,input:focus-visible{outline:3px solid #db8b28;outline-offset:3px}
    dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}dt{color:#5c6e65;font-size:.8rem;text-transform:uppercase}dd{margin:4px 0 0;font-weight:700;overflow-wrap:anywhere}code{overflow-wrap:anywhere}
  `],
})
export class DspyOptimizationWorkbenchComponent implements OnDestroy {
  private readonly api = inject(DspyOptimizationApiService);
  private readonly directory = inject(AgentDirectoryService);
  private readonly subscriptions = new Subscription();
  tenantId = '';
  status = 'dspy_scope_required';
  loading = false;
  capability: DspyOptimizationCapability | null = null;
  runs: readonly DspyOptimizationRun[] = [];

  ngOnDestroy(): void { this.subscriptions.unsubscribe(); }

  load(): void {
    const hub = this.directory.list().find(value => value.role === 'hub');
    if (!hub || !this.tenantId) { this.status = hub ? 'dspy_scope_required' : 'dspy_hub_unavailable'; return; }
    this.loading = true; this.status = 'dspy_loading';
    this.subscriptions.add(this.api.capabilities(hub.url).pipe(
      switchMap(capability => { this.capability = capability; return this.api.runs(hub.url, this.tenantId); }),
    ).subscribe({
      next: runs => { this.runs = runs; this.loading = false; this.status = 'dspy_snapshot_loaded'; },
      error: error => { this.loading = false; this.status = error instanceof Error ? error.message : 'dspy_load_failed'; },
    }));
  }

  cancel(run: DspyOptimizationRun): void {
    const hub = this.directory.list().find(value => value.role === 'hub');
    if (!hub) { this.status = 'dspy_hub_unavailable'; return; }
    this.subscriptions.add(this.api.cancel(hub.url, run).subscribe({
      next: updated => { this.runs = this.runs.map(item => item.runId === updated.runId ? updated : item); this.status = updated.reasonCode; },
      error: error => { this.status = error instanceof Error ? error.message : 'dspy_cancel_failed'; },
    }));
  }
}
