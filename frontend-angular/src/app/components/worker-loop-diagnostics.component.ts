import { Component, OnInit, inject } from '@angular/core';
import { JsonPipe } from '@angular/common';
import { WorkerLoopDiagnosticsApiService } from '../services/worker-loop-diagnostics-api.service';
import { SystemFacade } from '../features/system/system.facade';

// AWTCL-019 / AWWPI-019: shows ToolCalls, policy decisions, ToolResults,
// mutation modes, diffs and blocked changes per ananta-worker run. Batch
// iteration reports do not appear here — only tool-loop and feedback
// mutation runs write the underlying reports.
@Component({
  standalone: true,
  imports: [JsonPipe],
  selector: 'app-worker-loop-diagnostics',
  template: `
    <h2>Ananta-Worker Loop Diagnostik</h2>
    <p class="muted">
      Tool-Calling-Loop und Workspace-Mutations-Läufe (Feedback-Iteration) pro Worker-Run.
      Geblockte ToolCalls und Policy-Verletzungen sind je Iteration ausgewiesen.
    </p>
    <div class="row" style="gap:8px; margin-bottom: 12px;">
      <button (click)="load()">Aktualisieren</button>
    </div>

    <table style="width:100%; margin-bottom: 16px;">
      <thead>
        <tr>
          <th style="text-align:left;">Workspace</th>
          <th style="text-align:left;">Loop</th>
          <th style="text-align:left;">Mutation Mode</th>
          <th style="text-align:left;">Outcome</th>
          <th style="text-align:left;">Iterationen</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        @for (run of runs; track run.workspace + run.kind) {
          <tr>
            <td>{{ run.workspace }}</td>
            <td>{{ run.kind === 'mutation' ? 'Feedback-Mutation' : 'Tool-Loop' }}</td>
            <td>{{ run.mutation_mode || '—' }}</td>
            <td [style.color]="isBlocked(run.outcome) ? 'var(--color-danger, #c0392b)' : ''">{{ run.outcome }}</td>
            <td>{{ run.iteration_count }}</td>
            <td><button class="button-outline" (click)="open(run)">Details</button></td>
          </tr>
        } @empty {
          <tr><td colspan="6" class="muted">Keine Loop-Reports gefunden.</td></tr>
        }
      </tbody>
    </table>

    @if (report) {
      <h3>{{ reportTitle }}</h3>
      @for (iteration of report.iterations || []; track $index) {
        <details style="margin-bottom: 6px;">
          <summary>
            Iteration {{ iteration.iteration }} — {{ iteration.kind }}
            @if (iteration.tool_name) { · Tool: {{ iteration.tool_name }} }
            @if (iteration.policy_decision) {
              · Policy:
              <strong [style.color]="iteration.policy_decision !== 'allow' ? 'var(--color-danger, #c0392b)' : ''">
                {{ iteration.policy_decision }}
              </strong>
              @if (iteration.policy_reason) { ({{ iteration.policy_reason }}) }
            }
          </summary>
          <pre style="white-space: pre-wrap; word-break: break-word;">{{ iteration | json }}</pre>
        </details>
      }
      <details>
        <summary>Vollständiger Report (inkl. final_policy_result / Evidence)</summary>
        <pre style="white-space: pre-wrap; word-break: break-word;">{{ report | json }}</pre>
      </details>
    }
  `,
})
export class WorkerLoopDiagnosticsComponent implements OnInit {
  private api = inject(WorkerLoopDiagnosticsApiService);
  private system = inject(SystemFacade);

  runs: any[] = [];
  report: any = null;
  reportTitle = '';

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) return;
    this.api.listRuns(hub.url).subscribe({ next: (data) => (this.runs = data?.runs || []) });
  }

  open(run: any): void {
    const hub = this.system.resolveHubAgent();
    if (!hub?.url) return;
    this.api.getReport(hub.url, run.workspace, run.kind).subscribe({
      next: (data) => {
        this.report = data?.report || null;
        this.reportTitle = `${run.workspace} (${run.kind === 'mutation' ? 'Feedback-Mutation' : 'Tool-Loop'})`;
      },
    });
  }

  isBlocked(outcome: string): boolean {
    return ['policy_blocked', 'approval_required', 'invalid_output_limit_reached', 'no_progress_detected'].includes(
      String(outcome || '')
    );
  }
}
