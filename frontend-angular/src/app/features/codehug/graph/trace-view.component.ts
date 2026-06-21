import {
  ChangeDetectionStrategy,
  Component,
  EventEmitter,
  Input,
  Output,
  computed,
  signal,
} from '@angular/core';
import { DatePipe } from '@angular/common';
import { ChAgentStepReadModel, ChRunPhase } from '../models/codehug.models';

export type ChTraceDetail = 'simplified' | 'details' | 'raw';

/**
 * TraceViewComponent — zeigt die Schrittliste eines Agent-Runs in 3 Stufen:
 * - 'simplified' (default): Phase, Title, Duration, Worker, Backend
 * - 'details': + Tool-Calls mit Input/Output-Summary
 * - 'raw': + args, rawOutput, stderr, latency
 *
 * Filter: Phase (plan/det/llm/apply/verify), Worker, Backend, Layer.
 * Visuelle Differenzierung: deterministic = grau, LLM = accent (CH-014-006).
 */
@Component({
  selector: 'ch-trace-view',
  standalone: true,
  imports: [DatePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ch-trace">
      <header class="ch-trace-toolbar">
        <div class="ch-trace-toggle" role="tablist" aria-label="Trace-Aufloesung">
          <button
            type="button"
            role="tab"
            [attr.aria-selected]="detail() === 'simplified'"
            [class.active]="detail() === 'simplified'"
            (click)="detail.set('simplified')">Simplified</button>
          <button
            type="button"
            role="tab"
            [attr.aria-selected]="detail() === 'details'"
            [class.active]="detail() === 'details'"
            (click)="detail.set('details')">Details</button>
          <button
            type="button"
            role="tab"
            [attr.aria-selected]="detail() === 'raw'"
            [class.active]="detail() === 'raw'"
            (click)="detail.set('raw')">Raw Trace</button>
        </div>

        <div class="ch-trace-filters">
          <label>Phase:
            <select [value]="phaseFilter()" (change)="phaseFilter.set($any($event.target).value)">
              <option value="">alle</option>
              <option value="plan">plan</option>
              <option value="det">det</option>
              <option value="llm">llm</option>
              <option value="apply">apply</option>
              <option value="verify">verify</option>
              <option value="tool">tool</option>
              <option value="policy">policy</option>
            </select>
          </label>
          <label>Worker:
            <input type="text" [value]="workerFilter()" (input)="workerFilter.set($any($event.target).value)" placeholder="alle" />
          </label>
          <label>Backend:
            <input type="text" [value]="backendFilter()" (input)="backendFilter.set($any($event.target).value)" placeholder="alle" />
          </label>
        </div>

        <p class="ch-trace-summary">
          {{ deterministicCount() }} det, {{ llmCount() }} LLM, {{ filteredSteps().length }} sichtbar
        </p>
      </header>

      <ol class="ch-trace-steps">
        @for (step of filteredSteps(); track step.id) {
          <li class="ch-trace-step" [attr.data-phase]="step.phase" [attr.data-det]="isDeterministic(step) ? '1' : '0'">
            <header class="ch-trace-step-head">
              <span class="ch-trace-step-index">{{ step.index }}</span>
              <span class="ch-trace-step-phase" [attr.data-phase]="step.phase">{{ step.phase }}</span>
              <span class="ch-trace-step-title">{{ step.title }}</span>
              <span class="ch-trace-step-status" [attr.data-status]="step.status">{{ step.status }}</span>
              @if (step.durationMs !== null) {
                <span class="ch-trace-step-duration">{{ step.durationMs }}ms</span>
              }
              <button
                type="button"
                class="ch-trace-step-jump"
                (click)="stepSelected.emit(step.id)"
                [attr.aria-label]="'Springe zu Schritt ' + step.index">→</button>
            </header>

            @if (step.cliBackend) {
              <p class="ch-trace-step-meta">
                <span class="ch-trace-tag" [class.ch-trace-tag-det]="isDeterministic(step)">
                  {{ step.cliBackend }}{{ isDeterministic(step) ? ' [det]' : '' }}
                </span>
                @if (step.model) {
                  <span class="ch-trace-tag">{{ step.model }}</span>
                }
                @if (step.workerId) {
                  <span class="ch-trace-tag">worker: {{ step.workerId }}</span>
                }
              </p>
            }

            @if (step.outputSummary) {
              <p class="ch-trace-step-summary">{{ step.outputSummary }}</p>
            }

            @if (detail() !== 'simplified' && step.toolCalls && step.toolCalls.length > 0) {
              <ul class="ch-trace-toolcalls">
                @for (tc of step.toolCalls; track tc.id) {
                  <li class="ch-trace-toolcall" [attr.data-risk]="tc.riskLevel">
                    <span class="ch-trace-toolcall-name">{{ tc.toolName }}</span>
                    <span class="ch-trace-toolcall-input">{{ tc.inputSummary }}</span>
                    @if (tc.outputSummary) {
                      <span class="ch-trace-toolcall-output">→ {{ tc.outputSummary }}</span>
                    }
                    <span class="ch-trace-toolcall-status" [attr.data-status]="tc.status">{{ tc.status }}</span>
                  </li>
                }
              </ul>
            }

            @if (detail() === 'raw') {
              <details class="ch-trace-raw">
                <summary>Raw-Daten anzeigen</summary>
                @if (step.args) {
                  <pre class="ch-trace-pre"><strong>args:</strong> {{ stringify(step.args) }}</pre>
                }
                @if (step.rawOutput) {
                  <pre class="ch-trace-pre"><strong>rawOutput:</strong> {{ step.rawOutput }}</pre>
                }
                @if (step.stderr) {
                  <pre class="ch-trace-pre ch-trace-pre-stderr"><strong>stderr:</strong> {{ step.stderr }}</pre>
                }
                @if (step.errorMessage) {
                  <pre class="ch-trace-pre ch-trace-pre-error"><strong>error:</strong> {{ step.errorMessage }}</pre>
                }
                @if (step.startedAt) {
                  <p class="ch-trace-meta">started: {{ step.startedAt | date: 'mediumTime' }}</p>
                }
                @if (step.finishedAt) {
                  <p class="ch-trace-meta">finished: {{ step.finishedAt | date: 'mediumTime' }}</p>
                }
              </details>
            }
          </li>
        }
      </ol>

      @if (filteredSteps().length === 0) {
        <p class="ch-trace-empty">Keine Schritte matchen den Filter.</p>
      }
    </div>
  `,
  styles: [`
    :host { display: block; }
    .ch-trace { display: flex; flex-direction: column; gap: 10px; }
    .ch-trace-toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px;
      padding: 6px 10px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 12px;
    }
    .ch-trace-toggle {
      display: flex;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
    }
    .ch-trace-toggle button {
      padding: 4px 10px;
      border: none;
      background: var(--bg);
      color: var(--fg);
      cursor: pointer;
      font-size: 12px;
    }
    .ch-trace-toggle button.active {
      background: var(--accent);
      color: #fff;
    }
    .ch-trace-filters { display: flex; gap: 8px; align-items: center; }
    .ch-trace-filters select, .ch-trace-filters input {
      padding: 3px 6px;
      border: 1px solid var(--border);
      border-radius: 4px;
      font-size: 11px;
      background: var(--bg);
      color: var(--fg);
    }
    .ch-trace-summary { margin: 0; color: var(--muted); font-size: 11px; }

    .ch-trace-steps { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; }
    .ch-trace-step {
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      background: var(--card-bg);
    }
    .ch-trace-step[data-det="1"] {
      background: color-mix(in srgb, #6b7280 8%, var(--card-bg));
    }
    .ch-trace-step[data-det="0"] {
      background: color-mix(in srgb, var(--accent) 6%, var(--card-bg));
    }
    .ch-trace-step-head {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }
    .ch-trace-step-index {
      width: 24px;
      height: 24px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: var(--bg);
      border-radius: 50%;
      font-size: 11px;
      font-weight: 600;
    }
    .ch-trace-step-phase {
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .ch-trace-step-phase[data-phase="plan"] { background: #dbeafe; color: #1e40af; }
    .ch-trace-step-phase[data-phase="det"] { background: #e5e7eb; color: #374151; }
    .ch-trace-step-phase[data-phase="llm"] { background: #fef3c7; color: #92400e; }
    .ch-trace-step-phase[data-phase="apply"] { background: #fed7aa; color: #9a3412; }
    .ch-trace-step-phase[data-phase="verify"] { background: #bbf7d0; color: #065f46; }
    .ch-trace-step-phase[data-phase="tool"] { background: #ddd6fe; color: #5b21b6; }
    .ch-trace-step-phase[data-phase="policy"] { background: #fecaca; color: #991b1b; }
    .ch-trace-step-title { flex: 1; font-size: 12px; font-weight: 500; }
    .ch-trace-step-status {
      font-size: 10px;
      padding: 1px 6px;
      border-radius: 4px;
      background: var(--bg);
    }
    .ch-trace-step-status[data-status="succeeded"] { background: #bbf7d0; color: #065f46; }
    .ch-trace-step-status[data-status="failed"] { background: #fecaca; color: #991b1b; }
    .ch-trace-step-status[data-status="running"] { background: #dbeafe; color: #1e40af; }
    .ch-trace-step-duration { font-size: 10px; color: var(--muted); }
    .ch-trace-step-jump {
      padding: 2px 6px;
      border: 1px solid var(--border);
      border-radius: 4px;
      background: var(--bg);
      cursor: pointer;
      font-size: 11px;
    }

    .ch-trace-step-meta { margin: 4px 0; display: flex; gap: 4px; flex-wrap: wrap; }
    .ch-trace-tag {
      font-size: 10px;
      padding: 1px 6px;
      border-radius: 4px;
      background: color-mix(in srgb, var(--accent) 16%, transparent);
    }
    .ch-trace-tag-det { background: color-mix(in srgb, #6b7280 24%, transparent); }

    .ch-trace-step-summary {
      margin: 4px 0;
      font-size: 11px;
      color: var(--muted);
      font-family: var(--mono, ui-monospace, monospace);
    }

    .ch-trace-toolcalls {
      list-style: none;
      padding: 0;
      margin: 6px 0 0;
      display: grid;
      gap: 3px;
    }
    .ch-trace-toolcall {
      display: grid;
      grid-template-columns: max-content 1fr 1fr max-content;
      gap: 6px;
      align-items: center;
      padding: 3px 6px;
      border: 1px solid var(--border);
      border-radius: 4px;
      font-size: 10px;
    }
    .ch-trace-toolcall-name { font-weight: 600; }
    .ch-trace-toolcall-input, .ch-trace-toolcall-output {
      font-family: var(--mono, ui-monospace, monospace);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .ch-trace-toolcall-status { font-size: 10px; padding: 1px 4px; border-radius: 3px; background: var(--bg); }
    .ch-trace-toolcall[data-risk="high"] .ch-trace-toolcall-name::before {
      content: '⚠ ';
      color: #b91c1c;
    }

    .ch-trace-raw { margin-top: 6px; font-size: 11px; }
    .ch-trace-pre {
      margin: 4px 0;
      padding: 4px 8px;
      background: var(--bg);
      border-radius: 4px;
      overflow: auto;
      max-height: 200px;
      font-family: var(--mono, ui-monospace, monospace);
      font-size: 10px;
    }
    .ch-trace-pre-stderr { background: color-mix(in srgb, #fecaca 20%, var(--bg)); }
    .ch-trace-pre-error { background: color-mix(in srgb, #ef4444 20%, var(--bg)); }
    .ch-trace-meta { font-size: 10px; color: var(--muted); margin: 2px 0; }
    .ch-trace-empty { color: var(--muted); font-size: 12px; }
  `]
})
export class TraceViewComponent {
  @Input() set steps(value: ChAgentStepReadModel[]) {
    this._steps.set(value ?? []);
  }
  get steps(): ChAgentStepReadModel[] { return this._steps(); }

  @Output() stepSelected = new EventEmitter<string>();

  readonly detail = signal<ChTraceDetail>('simplified');
  readonly phaseFilter = signal<ChRunPhase | ''>('');
  readonly workerFilter = signal('');
  readonly backendFilter = signal('');

  private _steps = signal<ChAgentStepReadModel[]>([]);

  readonly filteredSteps = computed(() => {
    const all = this._steps();
    const phase = this.phaseFilter();
    const worker = this.workerFilter().toLowerCase();
    const backend = this.backendFilter().toLowerCase();
    return all.filter(s => {
      if (phase && s.phase !== phase) return false;
      if (worker && !(s.workerId ?? '').toLowerCase().includes(worker)) return false;
      if (backend && !(s.cliBackend ?? '').toLowerCase().includes(backend)) return false;
      return true;
    });
  });

  readonly deterministicCount = computed(() =>
    this._steps().filter(s => this.isDeterministic(s)).length
  );

  readonly llmCount = computed(() =>
    this._steps().filter(s => !this.isDeterministic(s)).length
  );

  isDeterministic(step: ChAgentStepReadModel): boolean {
    return step.cliBackend === 'deterministic' || step.model === 'deterministic';
  }

  stringify(v: unknown): string {
    try {
      return JSON.stringify(v, null, 2);
    } catch {
      return String(v);
    }
  }
}