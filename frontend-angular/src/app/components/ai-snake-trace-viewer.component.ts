import {
  Component,
  inject,
  OnInit,
  OnDestroy,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
} from '@angular/core';
import { CommonModule, AsyncPipe, DecimalPipe, KeyValuePipe } from '@angular/common';
import { Subscription } from 'rxjs';
import { AiSnakeTraceService, AiSnakeTraceEvent, AiSnakeTraceMeta } from '../services/ai-snake-trace.service';
import { AiSnakeChatService } from '../services/ai-snake-chat.service';

const PHASE_LABELS: Record<string, string> = {
  request_received: 'Anfrage',
  config_loaded: 'Konfiguration',
  retrieval_profile_selected: 'Retrieval-Profil',
  codecompass_retrieval_started: 'CodeCompass …',
  codecompass_retrieval_completed: 'Dateien abgerufen',
  rag_iterative_detected: 'RAG-Iterativ',
  rag_iterative_plan: 'RAG-Plan',
  rag_iterative_completed: 'RAG fertig',
  rag_iterative_synthesis: 'Synthese läuft',
  rag_iterative_synthesis_done: 'Synthese fertig',
  rag_iterative_tool_loop_start: 'Tool-Loop',
  rag_iterative_tool_loop_done: 'Tool-Loop fertig',
  full_scan_detected: 'Full-Scan',
  full_scan_batch_started: 'Batch läuft',
  full_scan_batch_completed: 'Full-Scan fertig',
  prompt_built: 'Prompt gebaut',
  llm_call_started: 'LLM läuft …',
  llm_call_completed: 'LLM fertig',
  answer_postprocessed: 'Antwort aufbereitet',
  chat_message_written: 'Gesendet',
  failed: 'Fehler',
};

const STATUS_DOT: Record<string, string> = {
  completed: '●',
  running: '◎',
  failed: '✗',
  skipped: '–',
  pending: '○',
  cancelled: '⊘',
};

interface ChunkMeta { path: string; source_type: string; score: number; }

@Component({
  selector: 'app-ai-snake-trace-viewer',
  standalone: true,
  imports: [CommonModule, AsyncPipe, DecimalPipe, KeyValuePipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
<div class="tv">

  <!-- ── Kopfzeile ── -->
  <div class="tv-head">
    <span class="tv-title">Antwort-Trace</span>
    @if (traceSvc.traceStatus$ | async; as st) {
      <span class="tv-badge" [class]="'badge-' + st">{{ st }}</span>
    }
    <span class="spacer"></span>
    @if (!autoFollow && events.length) {
      <button class="btn-live" (click)="resumeFollow()">▶ Live</button>
    }
    @if (traceId) {
      <button class="btn-ghost" (click)="clear()">✕</button>
    }
  </div>

  <!-- ── Kein Trace ── -->
  @if (!traceId) {
    <div class="tv-empty">
      <div class="tv-empty-icon">🔍</div>
      <div class="tv-empty-msg">Noch kein Trace.<br>Sende eine Nachricht um den Ablauf zu sehen.</div>
      @if (chatSvc.snakeId$ | async) {
        <button class="btn-ghost" (click)="loadHistory()">Letzte Läufe laden</button>
      }
      @if (historyList.length) {
        <div class="hist-list">
          @for (t of historyList; track t.trace_id) {
            <div class="hist-row" (click)="openHistoric(t.trace_id)">
              <span class="hist-dot" [class]="'badge-' + t.status">{{ STATUS_DOT[t.status] || '○' }}</span>
              <span class="hist-id">{{ t.trace_id.slice(0,8) }}</span>
              <span class="hist-st" [class]="'badge-' + t.status">{{ t.status }}</span>
              <span class="hist-n">{{ t.event_count }} Events</span>
              <span class="hist-ago">{{ ago(t.created_at) }}</span>
            </div>
          }
        </div>
      }
    </div>
  }

  <!-- ── Haupt-Inhalt ── -->
  @if (traceId && events.length) {
    <div class="tv-body">

      <!-- Schritt-Leiste links -->
      <div class="tv-steps">
        @for (ev of events; track ev.event_id) {
          <div class="step"
               [class.step-sel]="sel?.event_id === ev.event_id"
               [class.step-run]="ev.status === 'running'"
               [class.step-fail]="ev.status === 'failed'"
               [class.step-tool]="isToolCall(ev.phase)"
               (click)="pick(ev)">
            <span class="step-dot" [class]="'dot-' + ev.status">{{ STATUS_DOT[ev.status] || '○' }}</span>
            <span class="step-label">{{ stepLabel(ev) }}</span>
            @if (ev.duration_ms != null) {
              <span class="step-ms">{{ ev.duration_ms | number:'1.0-0' }}ms</span>
            }
          </div>
        }
      </div>

      <!-- Detail rechts -->
      <div class="tv-detail" *ngIf="sel">

        <!-- Phasen-Header -->
        <div class="det-phase">{{ sel.phase }}</div>
        <div class="det-title">{{ sel.title }}</div>
        <div class="det-meta">
          <span class="badge-sm" [class]="'badge-' + sel.status">{{ sel.status }}</span>
          @if (sel.duration_ms != null) {
            <span class="det-dur">{{ sel.duration_ms | number:'1.0-0' }} ms</span>
          }
          @if (sel.redaction_applied) {
            <span class="det-redact">🔒 gekürzt</span>
          }
        </div>

        @if (sel.summary) {
          <div class="det-summary">{{ sel.summary }}</div>
        }
        @if (sel.error) {
          <div class="det-error">{{ sel.error }}</div>
        }

        <!-- ── Dateiliste (codecompass_retrieval_completed) ── -->
        @if (sel.phase === 'codecompass_retrieval_completed' && chunks(sel).length) {
          <div class="section">
            <div class="sec-head" (click)="toggle('files')">
              <span class="sec-arrow">{{ open.files ? '▼' : '▶' }}</span>
              <span class="sec-name">Abgerufene Dateien</span>
              <span class="sec-cnt">{{ chunks(sel).length }}</span>
            </div>
            @if (open.files) {
              <div class="file-list">
                @for (c of chunks(sel); track c.path) {
                  <div class="file-row">
                    <span class="file-type" [class]="'ft-' + c.source_type">{{ c.source_type }}</span>
                    <span class="file-path">{{ c.path }}</span>
                    <span class="file-score">{{ c.score | number:'1.2-3' }}</span>
                  </div>
                }
              </div>
            }
          </div>
        }

        <!-- ── Input / Prompt ── -->
        @if (inputPreviewText(sel)) {
          <div class="section">
            <div class="sec-head" (click)="toggle('prompt')">
              <span class="sec-arrow">{{ open.prompt ? '▼' : '▶' }}</span>
              <span class="sec-name">{{ inputPreviewTitle(sel) }}</span>
              <span class="sec-cnt">{{ inputPreviewLen(sel) }} Zeichen</span>
            </div>
            @if (open.prompt) {
              <pre class="code-block">{{ inputPreviewText(sel) }}</pre>
            }
          </div>
        }

        <!-- ── Output / Antwort ── -->
        @if (outputPreviewText(sel)) {
          <div class="section">
            <div class="sec-head" (click)="toggle('output')">
              <span class="sec-arrow">{{ open.output ? '▼' : '▶' }}</span>
              <span class="sec-name">{{ outputPreviewTitle(sel) }}</span>
              <span class="sec-cnt">{{ outputPreviewLen(sel) }} Zeichen</span>
            </div>
            @if (open.output) {
              <pre class="code-block">{{ outputPreviewText(sel) }}</pre>
            }
          </div>
        }

        <!-- ── Tool-Call Detail ── -->
        @if (isToolCall(sel.phase)) {
          <div class="tool-call-block">
            <div class="tool-call-header">
              <span class="tool-fn-badge">⚙ {{ toolCallName(sel) }}</span>
              <span class="tool-result-size">{{ toolResultChars(sel) }} Zeichen Ergebnis</span>
            </div>
            @if (toolCallArgs(sel) | keyvalue; as argList) {
              @if (argList.length) {
                <div class="tool-args">
                  @for (arg of argList; track arg.key) {
                    <div class="tool-arg-row">
                      <span class="tool-arg-key">{{ arg.key }}</span>
                      <span class="tool-arg-val">{{ arg.value }}</span>
                    </div>
                  }
                </div>
              }
            }
          </div>
        }

        <!-- ── Details (generisch) ── -->
        @if (hasDetails(sel)) {
          <div class="section">
            <div class="sec-head" (click)="toggle('details')">
              <span class="sec-arrow">{{ open.details ? '▼' : '▶' }}</span>
              <span class="sec-name">Details</span>
            </div>
            @if (open.details) {
              <pre class="code-block">{{ fmt(detailsWithoutChunks(sel)) }}</pre>
            }
          </div>
        }

        <!-- ── Full-Scan Dateien ── -->
        @if (sel.phase === 'full_scan_batch_completed') {
          <div class="section">
            <div class="sec-head" (click)="toggle('details')">
              <span class="sec-arrow">{{ open.details ? '▼' : '▶' }}</span>
              <span class="sec-name">Scan-Info</span>
            </div>
            @if (open.details) {
              <pre class="code-block">{{ fmt(sel.details) }}</pre>
            }
          </div>
        }

      </div>
    </div>
  }

  @if (traceId && !events.length) {
    <div class="tv-wait">⏳ Warte auf erste Events…</div>
  }

</div>
  `,
  styles: [`
    :host { display: flex; flex-direction: column; height: 100%; min-height: 0; }

    .tv {
      display: flex; flex-direction: column; height: 100%; min-height: 0;
      background: #080f1c; color: #b8cce0; font-size: 11px; font-family: inherit;
    }

    /* Head */
    .tv-head {
      display: flex; align-items: center; gap: 6px; flex-shrink: 0;
      padding: 5px 8px; background: #0a1526; border-bottom: 1px solid #152040;
    }
    .tv-title { font-weight: 600; color: #7ab8d8; font-size: 11px; }
    .spacer { flex: 1; }

    /* Badges */
    .tv-badge, .badge-sm { font-size: 9px; padding: 1px 5px; border-radius: 8px; border: 1px solid #1a2d4a; }
    .badge-running  { color: #7fffd4; border-color: #1a5a3a; background: #061810; animation: pulse 1.2s infinite; }
    .badge-completed { color: #3acc88; border-color: #1a4a2a; }
    .badge-failed   { color: #fb7185; border-color: #4a1a1a; }
    .badge-skipped  { color: #4a5a7a; border-color: #1a2a3a; }
    .badge-idle, .badge-unknown { color: #2a4060; border-color: #1a2030; }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }

    .btn-live {
      background: #061c14; border: 1px solid #1a5a3a; color: #3accaa;
      padding: 2px 7px; cursor: pointer; font-size: 10px; border-radius: 2px;
    }
    .btn-ghost {
      background: transparent; border: 1px solid #1a2d4a; color: #4a6a8a;
      padding: 2px 6px; cursor: pointer; font-size: 10px; border-radius: 2px;
    }
    .btn-ghost:hover { color: #7fffd4; }
    .btn-live:hover  { background: #0a2a1e; }

    /* Empty */
    .tv-empty {
      flex: 1; display: flex; flex-direction: column; align-items: center;
      gap: 8px; padding: 24px 14px;
    }
    .tv-empty-icon { font-size: 24px; }
    .tv-empty-msg { color: #2a4060; text-align: center; line-height: 1.7; }
    .hist-list { width: 100%; display: flex; flex-direction: column; gap: 2px; margin-top: 6px; }
    .hist-row {
      display: flex; gap: 6px; align-items: center;
      padding: 4px 8px; background: #0a1628; border: 1px solid #152040;
      cursor: pointer; border-radius: 2px;
    }
    .hist-row:hover { border-color: #2a4070; }
    .hist-dot { font-size: 9px; width: 10px; }
    .hist-id  { color: #3a6a9a; font-size: 10px; }
    .hist-st  { font-size: 9px; }
    .hist-n   { margin-left: auto; color: #2a4060; }
    .hist-ago { color: #1a3050; font-size: 9px; }

    /* Main body */
    .tv-body { flex: 1; min-height: 0; display: flex; overflow: hidden; }

    /* Steps column */
    .tv-steps {
      flex: 0 0 140px; overflow-y: auto; border-right: 1px solid #152040;
      display: flex; flex-direction: column; padding: 4px 0;
    }
    .step {
      display: flex; align-items: center; gap: 5px;
      padding: 4px 6px; cursor: pointer; border-left: 2px solid transparent;
      user-select: none;
    }
    .step:hover { background: #0d1e34; }
    .step-sel  { background: #0f2040; border-left-color: #2a6090; }
    .step-run  { animation: step-blink 1s ease-in-out infinite; }
    .step-fail { border-left-color: #5a1a1a; }
    @keyframes step-blink { 0%,100% { background: #08121e; } 50% { background: #0d1e34; } }
    .step-dot { font-size: 9px; width: 12px; flex-shrink: 0; }
    .dot-completed { color: #3acc88; }
    .dot-running   { color: #7fffd4; }
    .dot-failed    { color: #fb7185; }
    .dot-skipped   { color: #2a3a5a; }
    .dot-pending   { color: #1a2a40; }
    .step-label { flex: 1; font-size: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .step-ms { font-size: 9px; color: #2a4060; flex-shrink: 0; }
    .step-tool { border-left: 2px solid #3a1a5a; }
    .step-tool.step-sel { border-left-color: #7a3acc; background: #180a2a; }

    /* Detail panel */
    .tv-detail {
      flex: 1; min-width: 0; overflow-y: auto;
      padding: 10px 12px; display: flex; flex-direction: column; gap: 6px;
    }
    .det-phase { color: #2a5a7a; font-size: 9px; text-transform: uppercase; letter-spacing: 0.05em; }
    .det-title { color: #c0d8f0; font-size: 12px; font-weight: 500; }
    .det-meta  { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
    .det-dur   { color: #2a4060; font-size: 9px; }
    .det-redact { color: #7a5a2a; font-size: 9px; background: #1a1206; border: 1px solid #3a2a06; padding: 1px 5px; border-radius: 8px; }
    .det-summary { color: #8ab0cc; background: #0a1628; padding: 5px 7px; border-radius: 2px; line-height: 1.6; }
    .det-error   { color: #fb7185; background: #1a0810; border: 1px solid #4a1020; padding: 5px 7px; border-radius: 2px; white-space: pre-wrap; word-break: break-word; }

    /* Sections */
    .section { display: flex; flex-direction: column; gap: 3px; }
    .sec-head {
      display: flex; align-items: center; gap: 5px; cursor: pointer;
      padding: 3px 0; user-select: none;
    }
    .sec-head:hover .sec-name { color: #7fffd4; }
    .sec-arrow { color: #2a5a7a; font-size: 9px; width: 10px; }
    .sec-name  { color: #4a8ab0; font-size: 10px; font-weight: 500; }
    .sec-cnt   { color: #2a4060; font-size: 9px; margin-left: auto; }

    /* File list */
    .file-list { display: flex; flex-direction: column; gap: 1px; max-height: 220px; overflow-y: auto; }
    .file-row  {
      display: flex; gap: 5px; align-items: center;
      padding: 2px 4px; border-radius: 1px;
    }
    .file-row:hover { background: #0a1628; }
    .file-type {
      font-size: 8px; padding: 1px 4px; border-radius: 6px; flex-shrink: 0;
      background: #0a1a28; border: 1px solid #1a3040; color: #3a7a9a;
    }
    .ft-source { border-color: #1a3a1a; background: #0a1808; color: #3a8a4a; }
    .ft-docs   { border-color: #2a2a1a; background: #12100a; color: #8a7a3a; }
    .ft-test   { border-color: #2a1a3a; background: #100a16; color: #7a4a9a; }
    .file-path { flex: 1; color: #8ab0c8; font-size: 10px; font-family: monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .file-score { color: #2a4060; font-size: 9px; flex-shrink: 0; }

    /* Code block */
    .code-block {
      background: #050c18; border: 1px solid #152040; padding: 7px 8px;
      font-family: monospace; font-size: 10px; line-height: 1.5;
      white-space: pre-wrap; word-break: break-word; color: #7aaac8;
      max-height: 280px; overflow: auto; margin: 0; border-radius: 2px;
    }

    /* Tool call */
    .tool-call-block {
      background: #10071e; border: 1px solid #3a1a5a; border-radius: 3px;
      padding: 7px 9px; display: flex; flex-direction: column; gap: 5px;
    }
    .tool-call-header { display: flex; align-items: center; gap: 8px; }
    .tool-fn-badge {
      font-size: 10px; font-weight: 600; color: #c080ff;
      background: #1e0a30; border: 1px solid #5a2a90; padding: 2px 7px; border-radius: 10px;
    }
    .tool-result-size { font-size: 9px; color: #5a3a7a; margin-left: auto; }
    .tool-args { display: flex; flex-direction: column; gap: 2px; }
    .tool-arg-row { display: flex; gap: 6px; align-items: baseline; }
    .tool-arg-key { font-size: 9px; color: #7a4aaa; flex-shrink: 0; min-width: 50px; }
    .tool-arg-val { font-size: 10px; color: #a070d0; font-family: monospace; word-break: break-all; }

    /* Wait */
    .tv-wait { padding: 20px; color: #2a4060; text-align: center; }
  `],
})
export class AiSnakeTraceViewerComponent implements OnInit, OnDestroy {
  readonly traceSvc = inject(AiSnakeTraceService);
  readonly chatSvc = inject(AiSnakeChatService);
  private cdr = inject(ChangeDetectorRef);

  readonly PHASE_LABELS = PHASE_LABELS;
  readonly STATUS_DOT = STATUS_DOT;

  events: AiSnakeTraceEvent[] = [];
  sel: AiSnakeTraceEvent | null = null;
  autoFollow = true;
  historyList: AiSnakeTraceMeta[] = [];
  open = { files: true, prompt: false, output: true, details: false };

  private sub?: Subscription;

  get traceId(): string | null { return this.traceSvc.activeTraceId$.value; }

  ngOnInit(): void {
    this.sub = this.traceSvc.traceEvents$.subscribe((evs) => {
      this.events = evs;
      if (this.autoFollow && evs.length) {
        const last = evs[evs.length - 1];
        if (this.sel?.event_id !== last.event_id) {
          this.sel = last;
          this.open = { files: true, prompt: false, output: true, details: false };
        }
      } else if (this.sel) {
        this.sel = evs.find(e => e.event_id === this.sel!.event_id) ?? this.sel;
      }
      this.cdr.markForCheck();
    });
  }

  ngOnDestroy(): void { this.sub?.unsubscribe(); }

  pick(ev: AiSnakeTraceEvent): void {
    this.sel = ev;
    this.autoFollow = false;
    this.open = { files: true, prompt: false, output: true, details: false };
    this.cdr.markForCheck();
  }

  resumeFollow(): void {
    this.autoFollow = true;
    if (this.events.length) this.sel = this.events[this.events.length - 1];
    this.cdr.markForCheck();
  }

  clear(): void {
    this.traceSvc.clearTrace();
    this.sel = null;
    this.autoFollow = true;
    this.historyList = [];
    this.cdr.markForCheck();
  }

  toggle(key: keyof typeof this.open): void {
    this.open[key] = !this.open[key];
    this.cdr.markForCheck();
  }

  async loadHistory(): Promise<void> {
    const sid = this.chatSvc.snakeId$.value;
    if (!sid) return;
    this.historyList = await this.traceSvc.loadTraceList(sid);
    this.cdr.markForCheck();
  }

  openHistoric(id: string): void {
    this.traceSvc.loadTrace(id);
    this.historyList = [];
    this.autoFollow = false;
    this.cdr.markForCheck();
  }

  chunks(ev: AiSnakeTraceEvent): ChunkMeta[] {
    const raw = (ev.details as any)?.['chunks'];
    if (!Array.isArray(raw)) return [];
    return raw as ChunkMeta[];
  }

  inputPreviewText(ev: AiSnakeTraceEvent): string {
    return this.previewText(ev.input_preview);
  }

  inputPreviewLen(ev: AiSnakeTraceEvent): number {
    return this.inputPreviewText(ev).length;
  }

  inputPreviewTitle(ev: AiSnakeTraceEvent): string {
    return this.isPromptPhase(ev.phase) ? 'Prompt an LLM' : 'Input Preview';
  }

  outputPreviewText(ev: AiSnakeTraceEvent): string {
    return this.previewText(ev.output_preview);
  }

  outputPreviewLen(ev: AiSnakeTraceEvent): number {
    return this.outputPreviewText(ev).length;
  }

  outputPreviewTitle(ev: AiSnakeTraceEvent): string {
    if (ev.phase === 'prompt_built') return 'Prompt an LLM';
    return this.isPromptPhase(ev.phase) ? 'Rohantwort LLM' : 'Output Preview';
  }

  private previewText(value: unknown): string {
    if (value == null) return '';
    if (typeof value === 'string') return value;
    return this.fmt(value);
  }

  private isPromptPhase(phase: string): boolean {
    return phase === 'prompt_built'
      || phase === 'llm_call_started'
      || phase === 'llm_call_completed'
      || phase.startsWith('rag_iterative_batch_')
      || phase === 'rag_iterative_synthesis'
      || phase === 'rag_iterative_synthesis_done'
      || phase === 'rag_iterative_tool_loop_start'
      || phase === 'rag_iterative_tool_loop_done'
      || phase.startsWith('tool_call_');
  }

  hasDetails(ev: AiSnakeTraceEvent): boolean {
    if (!ev.details) return false;
    const d = ev.details as Record<string, unknown>;
    const keys = Object.keys(d).filter(k => k !== 'chunks');
    return keys.length > 0;
  }

  detailsWithoutChunks(ev: AiSnakeTraceEvent): unknown {
    const d = { ...(ev.details as Record<string, unknown>) };
    delete d['chunks'];
    return d;
  }

  fmt(v: unknown): string {
    if (v == null) return '';
    if (typeof v === 'string') return v;
    try { return JSON.stringify(v, null, 2); } catch { return String(v); }
  }

  ago(ts: number): string {
    if (!ts) return '';
    const s = Math.round(Date.now() / 1000 - ts);
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m`;
    return `${Math.floor(s / 3600)}h`;
  }

  isToolCall(phase: string): boolean {
    return phase.startsWith('tool_call_');
  }

  stepLabel(ev: AiSnakeTraceEvent): string {
    if (this.isToolCall(ev.phase)) {
      const fn = (ev.details as Record<string, unknown>)?.['function'];
      if (typeof fn === 'string') return fn;
      return ev.title || ev.phase;
    }
    return PHASE_LABELS[ev.phase] || ev.title;
  }

  toolCallName(ev: AiSnakeTraceEvent): string {
    const d = ev.details as Record<string, unknown>;
    return typeof d?.['function'] === 'string' ? d['function'] as string : ev.phase;
  }

  toolCallArgs(ev: AiSnakeTraceEvent): Record<string, string> {
    const d = ev.details as Record<string, unknown>;
    const args = d?.['args'];
    if (!args || typeof args !== 'object') return {};
    const result: Record<string, string> = {};
    for (const [k, v] of Object.entries(args as Record<string, unknown>)) {
      result[k] = String(v);
    }
    return result;
  }

  toolResultChars(ev: AiSnakeTraceEvent): number {
    const d = ev.details as Record<string, unknown>;
    return typeof d?.['result_chars'] === 'number' ? d['result_chars'] as number : 0;
  }
}
