import {
  Component,
  inject,
  OnInit,
  OnDestroy,
  ChangeDetectionStrategy,
  ChangeDetectorRef,
} from '@angular/core';
import { CommonModule, AsyncPipe } from '@angular/common';
import { Subscription } from 'rxjs';
import { AiSnakeTraceService, AiSnakeTraceEvent, AiSnakeTraceMeta } from '../services/ai-snake-trace.service';
import { AiSnakeChatService } from '../services/ai-snake-chat.service';

const PHASE_ICONS: Record<string, string> = {
  request_received: '📥',
  session_resolved: '🔑',
  config_loaded: '⚙',
  retrieval_profile_selected: '🎯',
  domain_scope_resolved: '🗺',
  codecompass_retrieval_started: '🔍',
  codecompass_retrieval_completed: '✅',
  full_scan_detected: '🔭',
  full_scan_batch_started: '📦',
  full_scan_batch_completed: '📦',
  prompt_built: '📝',
  llm_call_started: '🤖',
  llm_token_delta: '💬',
  llm_call_completed: '✅',
  tool_call_requested: '🔧',
  tool_call_started: '🔧',
  tool_call_completed: '✅',
  tool_call_failed: '❌',
  answer_postprocessed: '✂',
  chat_message_written: '💬',
  cancel_requested: '⏹',
  failed: '❌',
  default: '◦',
};

const TOOL_PHASES = new Set([
  'tool_call_requested',
  'tool_call_started',
  'tool_call_completed',
  'tool_call_failed',
]);

@Component({
  selector: 'app-ai-snake-trace-viewer',
  standalone: true,
  imports: [CommonModule, AsyncPipe],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="trace-viewer">

      <!-- Header bar -->
      <div class="trace-header">
        @if (traceId) {
          <span class="trace-id" title="{{ traceId }}">
            Trace {{ traceId.slice(0, 8) }}…
          </span>
          <span class="trace-status" [class]="'st-' + (traceSvc.traceStatus$ | async)">
            {{ (traceSvc.traceStatus$ | async) || '–' }}
          </span>
        } @else {
          <span class="no-trace-hint">Kein aktiver Trace</span>
        }
        <span class="spacer"></span>
        @if (!autoFollow && (traceSvc.traceEvents$ | async)?.length) {
          <button class="live-btn" (click)="resumeAutoFollow()">▶ Live folgen</button>
        }
        @if (traceId) {
          <button class="ghost-btn" (click)="clear()" title="Trace schließen">✕</button>
        }
      </div>

      <!-- No trace state -->
      @if (!traceId) {
        <div class="empty-state">
          <div class="empty-icon">🔍</div>
          <div class="empty-msg">Noch kein Trace.<br>Sende eine Nachricht um einen Antwortlauf zu starten.</div>
          @if ((chatSvc.snakeId$ | async)) {
            <button class="ghost-btn" (click)="loadHistory()">Letzte Traces laden</button>
          }
          @if (historyList.length) {
            <div class="history-list">
              @for (t of historyList; track t.trace_id) {
                <div class="history-item" (click)="selectHistoricTrace(t.trace_id)">
                  <span class="hi-icon">{{ t.status === 'completed' ? '✓' : t.status === 'failed' ? '✗' : '○' }}</span>
                  <span class="hi-id">{{ t.trace_id.slice(0, 8) }}</span>
                  <span class="hi-status" [class]="'st-' + t.status">{{ t.status }}</span>
                  <span class="hi-count">{{ t.event_count }} Events</span>
                </div>
              }
            </div>
          }
        </div>
      }

      <!-- Main content: timeline + detail -->
      @if (traceId && (traceSvc.traceEvents$ | async); as events) {
        <div class="trace-body">
          <!-- Timeline -->
          <div class="timeline" #timelineEl>
            @if (events.length === 0) {
              <div class="tl-empty">Warte auf Events…</div>
            }
            @for (ev of events; track ev.event_id) {
              <div
                class="tl-event"
                [class.tl-selected]="selectedEventId === ev.event_id"
                [class.tl-running]="ev.status === 'running'"
                [class.tl-failed]="ev.status === 'failed'"
                [class.tl-tool]="isToolPhase(ev.phase)"
                (click)="selectEvent(ev)"
              >
                <span class="tl-icon">{{ phaseIcon(ev.phase) }}</span>
                <span class="tl-title">{{ ev.title }}</span>
                <span class="tl-dur" *ngIf="ev.duration_ms != null">{{ ev.duration_ms | number:'1.0-0' }}ms</span>
                <span class="tl-status-dot" [class]="'dot-' + ev.status"></span>
              </div>
            }
          </div>

          <!-- Detail panel -->
          @if (selectedEvent) {
            <div class="detail-panel">
              <div class="dp-phase">{{ phaseIcon(selectedEvent.phase) }} {{ selectedEvent.phase }}</div>
              <div class="dp-title">{{ selectedEvent.title }}</div>
              <div class="dp-row">
                <span class="dp-label">Status</span>
                <span class="dp-val" [class]="'st-' + selectedEvent.status">{{ selectedEvent.status }}</span>
              </div>
              @if (selectedEvent.duration_ms != null) {
                <div class="dp-row">
                  <span class="dp-label">Dauer</span>
                  <span class="dp-val">{{ selectedEvent.duration_ms | number:'1.0-0' }} ms</span>
                </div>
              }
              @if (selectedEvent.summary) {
                <div class="dp-summary">{{ selectedEvent.summary }}</div>
              }
              @if (selectedEvent.redaction_applied) {
                <div class="dp-redact">🔒 Secrets wurden entfernt</div>
              }
              @if (selectedEvent.error) {
                <div class="dp-error">{{ selectedEvent.error }}</div>
              }

              <!-- Input Preview -->
              @if (selectedEvent.input_preview != null) {
                <div class="dp-section">
                  <button class="dp-toggle" (click)="toggleSection('input')">
                    {{ openSections.input ? '▼' : '▶' }} Input Preview
                  </button>
                  @if (openSections.input) {
                    <pre class="dp-pre">{{ formatPreview(selectedEvent.input_preview) }}</pre>
                  }
                </div>
              }

              <!-- Output Preview -->
              @if (selectedEvent.output_preview != null) {
                <div class="dp-section">
                  <button class="dp-toggle" (click)="toggleSection('output')">
                    {{ openSections.output ? '▼' : '▶' }} Output Preview
                  </button>
                  @if (openSections.output) {
                    <pre class="dp-pre">{{ formatPreview(selectedEvent.output_preview) }}</pre>
                  }
                </div>
              }

              <!-- Details -->
              @if (hasDetails(selectedEvent)) {
                <div class="dp-section">
                  <button class="dp-toggle" (click)="toggleSection('details')">
                    {{ openSections.details ? '▼' : '▶' }} Details
                  </button>
                  @if (openSections.details) {
                    <pre class="dp-pre">{{ formatPreview(selectedEvent.details) }}</pre>
                  }
                </div>
              }

              <!-- Tool Call Card -->
              @if (isToolPhase(selectedEvent.phase)) {
                <div class="tool-card">
                  <div class="tc-label">Tool Call</div>
                  @if (selectedEvent.details?.['tool']) {
                    <div class="tc-row">
                      <span class="tc-key">Tool</span>
                      <span class="tc-val">{{ selectedEvent.details?.['tool'] }}</span>
                    </div>
                  }
                  @if (selectedEvent.details?.['args']) {
                    <div class="tc-row">
                      <span class="tc-key">Args</span>
                      <pre class="tc-pre">{{ formatPreview(selectedEvent.details?.['args']) }}</pre>
                    </div>
                  }
                  <div class="tc-row">
                    <span class="tc-key">Status</span>
                    <span class="tc-val" [class]="'st-' + selectedEvent.status">{{ selectedEvent.status }}</span>
                  </div>
                  @if (selectedEvent.duration_ms != null) {
                    <div class="tc-row">
                      <span class="tc-key">Dauer</span>
                      <span class="tc-val">{{ selectedEvent.duration_ms | number:'1.0-0' }} ms</span>
                    </div>
                  }
                  @if (selectedEvent.output_preview != null) {
                    <div class="tc-row">
                      <span class="tc-key">Ergebnis</span>
                      <pre class="tc-pre">{{ formatPreview(selectedEvent.output_preview) }}</pre>
                    </div>
                  }
                  @if (selectedEvent.error) {
                    <div class="tc-row tc-error">
                      <span class="tc-key">Fehler</span>
                      <span class="tc-val">{{ selectedEvent.error }}</span>
                    </div>
                  }
                </div>
              }
            </div>
          }
        </div>
      }
    </div>
  `,
  styles: [`
    :host { display: flex; flex-direction: column; height: 100%; min-height: 0; }

    .trace-viewer {
      display: flex; flex-direction: column; height: 100%; min-height: 0;
      background: #080f1c; color: #c0d0e8; font-size: 11px; font-family: inherit;
    }

    /* Header */
    .trace-header {
      display: flex; align-items: center; gap: 6px;
      padding: 5px 8px; background: #0a1628; border-bottom: 1px solid #152040;
      flex-shrink: 0;
    }
    .trace-id { color: #4a7aaa; font-size: 10px; }
    .no-trace-hint { color: #2a4060; font-size: 10px; }
    .spacer { flex: 1; }
    .live-btn {
      background: #0a2030; border: 1px solid #1a5a7a; color: #3acccc;
      padding: 2px 7px; cursor: pointer; font-size: 10px; border-radius: 2px;
    }
    .live-btn:hover { background: #0a3040; }
    .ghost-btn {
      background: transparent; border: 1px solid #1a2d4a; color: #4a6a9a;
      padding: 2px 6px; cursor: pointer; font-size: 10px; border-radius: 2px;
    }
    .ghost-btn:hover { color: #7fffd4; }

    /* Status colours */
    .st-running { color: #7fffd4; }
    .st-completed { color: #3acc88; }
    .st-failed { color: #fb7185; }
    .st-pending { color: #4a6a9a; }
    .st-skipped { color: #4a5a7a; }
    .st-cancelled { color: #7a5a3a; }
    .st-idle { color: #2a4060; }

    /* Empty state */
    .empty-state {
      flex: 1; display: flex; flex-direction: column; align-items: center;
      justify-content: flex-start; gap: 8px; padding: 20px 12px;
    }
    .empty-icon { font-size: 22px; }
    .empty-msg { color: #2a4060; text-align: center; line-height: 1.6; }
    .history-list { width: 100%; display: flex; flex-direction: column; gap: 3px; margin-top: 6px; }
    .history-item {
      display: flex; gap: 6px; align-items: center; padding: 4px 6px;
      background: #0a1628; border: 1px solid #152040; cursor: pointer; border-radius: 2px;
    }
    .history-item:hover { border-color: #2a4070; }
    .hi-icon { font-size: 10px; width: 12px; }
    .hi-id { color: #4a7aaa; }
    .hi-status { font-size: 10px; }
    .hi-count { margin-left: auto; color: #2a4060; }

    /* Main body: timeline + detail */
    .trace-body {
      flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden;
    }

    /* Timeline */
    .timeline {
      flex: 0 0 auto; max-height: 38%; overflow-y: auto;
      border-bottom: 1px solid #152040; padding: 4px 0;
    }
    .tl-empty { color: #2a4060; padding: 10px 12px; }
    .tl-event {
      display: flex; align-items: center; gap: 5px;
      padding: 3px 8px; cursor: pointer; border-left: 2px solid transparent;
      transition: background 0.1s;
    }
    .tl-event:hover { background: #0d1e34; }
    .tl-selected { background: #0f2040; border-left-color: #2a6090; }
    .tl-running { animation: pulse-bg 1.2s ease-in-out infinite; }
    .tl-failed { border-left-color: #5a1a1a; }
    .tl-tool { border-left-color: #2a3a1a; }
    @keyframes pulse-bg { 0%, 100% { background: #091522; } 50% { background: #0d1e34; } }
    .tl-icon { width: 14px; flex-shrink: 0; }
    .tl-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .tl-dur { color: #2a4a6a; font-size: 9px; flex-shrink: 0; }
    .tl-status-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
    .dot-completed { background: #3acc88; }
    .dot-running { background: #7fffd4; animation: blink 1s infinite; }
    .dot-failed { background: #fb7185; }
    .dot-pending { background: #2a4060; }
    .dot-skipped { background: #2a3a5a; }
    @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.2; } }

    /* Detail panel */
    .detail-panel {
      flex: 1; min-height: 0; overflow-y: auto;
      padding: 8px 10px; display: flex; flex-direction: column; gap: 5px;
    }
    .dp-phase { color: #4a7aaa; font-size: 10px; }
    .dp-title { color: #c0d0e8; font-size: 12px; font-weight: 500; }
    .dp-row { display: flex; gap: 8px; align-items: baseline; }
    .dp-label { color: #2a4060; width: 50px; flex-shrink: 0; }
    .dp-val { color: #c0d0e8; }
    .dp-summary { color: #8aadcc; background: #0a1628; padding: 4px 6px; border-radius: 2px; line-height: 1.5; }
    .dp-redact { color: #7a5a2a; font-size: 10px; background: #1a1408; border: 1px solid #3a2a08; padding: 2px 6px; border-radius: 2px; }
    .dp-error { color: #fb7185; background: #1a0a0a; border: 1px solid #4a1a1a; padding: 4px 6px; border-radius: 2px; white-space: pre-wrap; word-break: break-word; }
    .dp-section { display: flex; flex-direction: column; gap: 3px; }
    .dp-toggle {
      background: transparent; border: none; color: #3a6a9a; cursor: pointer;
      font-size: 10px; text-align: left; padding: 2px 0; font-family: inherit;
    }
    .dp-toggle:hover { color: #7fffd4; }
    .dp-pre {
      background: #060d18; border: 1px solid #1a2d4a; padding: 6px; border-radius: 2px;
      font-family: monospace; font-size: 10px; white-space: pre-wrap; word-break: break-all;
      color: #8ab8d8; max-height: 120px; overflow: auto; margin: 0;
    }

    /* Tool call card */
    .tool-card {
      background: #060d18; border: 1px solid #1a3a1a; border-radius: 3px;
      padding: 6px 8px; display: flex; flex-direction: column; gap: 4px;
    }
    .tc-label { color: #3acc66; font-size: 10px; font-weight: 600; }
    .tc-row { display: flex; gap: 8px; align-items: flex-start; }
    .tc-key { color: #2a5a3a; width: 50px; flex-shrink: 0; }
    .tc-val { color: #c0d0e8; }
    .tc-pre {
      flex: 1; background: #0a1410; border: 1px solid #1a3020; padding: 4px 6px;
      font-family: monospace; font-size: 10px; white-space: pre-wrap; word-break: break-all;
      color: #8acd98; max-height: 80px; overflow: auto; margin: 0; border-radius: 2px;
    }
    .tc-error .tc-key { color: #5a2a2a; }
    .tc-error .tc-val { color: #fb7185; }

    /* Trace status badge */
    .trace-status {
      font-size: 9px; padding: 1px 5px; border-radius: 10px;
      background: #0d1a2a; border: 1px solid #1a2d4a;
    }
  `],
})
export class AiSnakeTraceViewerComponent implements OnInit, OnDestroy {
  readonly traceSvc = inject(AiSnakeTraceService);
  readonly chatSvc = inject(AiSnakeChatService);
  private cdr = inject(ChangeDetectorRef);

  selectedEventId: string | null = null;
  selectedEvent: AiSnakeTraceEvent | null = null;
  autoFollow = true;
  historyList: AiSnakeTraceMeta[] = [];
  openSections = { input: false, output: true, details: false };

  private eventsSub?: Subscription;

  get traceId(): string | null {
    return this.traceSvc.activeTraceId$.value;
  }

  ngOnInit(): void {
    this.eventsSub = this.traceSvc.traceEvents$.subscribe((events) => {
      if (this.autoFollow && events.length > 0) {
        const last = events[events.length - 1];
        this.selectedEventId = last.event_id;
        this.selectedEvent = last;
        this.openSections = { input: false, output: true, details: false };
      } else if (this.selectedEventId) {
        const found = events.find((e) => e.event_id === this.selectedEventId);
        if (found) this.selectedEvent = found;
      }
      this.cdr.markForCheck();
    });
  }

  ngOnDestroy(): void {
    this.eventsSub?.unsubscribe();
  }

  selectEvent(ev: AiSnakeTraceEvent): void {
    this.selectedEventId = ev.event_id;
    this.selectedEvent = ev;
    this.autoFollow = false;
    this.openSections = { input: false, output: true, details: false };
    this.cdr.markForCheck();
  }

  resumeAutoFollow(): void {
    this.autoFollow = true;
    const events = this.traceSvc.traceEvents$.value;
    if (events.length > 0) {
      const last = events[events.length - 1];
      this.selectedEventId = last.event_id;
      this.selectedEvent = last;
    }
    this.cdr.markForCheck();
  }

  clear(): void {
    this.traceSvc.clearTrace();
    this.selectedEventId = null;
    this.selectedEvent = null;
    this.autoFollow = true;
    this.historyList = [];
    this.cdr.markForCheck();
  }

  async loadHistory(): Promise<void> {
    const sid = this.chatSvc.snakeId$.value;
    if (!sid) return;
    this.historyList = await this.traceSvc.loadTraceList(sid);
    this.cdr.markForCheck();
  }

  selectHistoricTrace(traceId: string): void {
    this.traceSvc.loadTrace(traceId);
    this.historyList = [];
    this.autoFollow = false;
    this.cdr.markForCheck();
  }

  phaseIcon(phase: string): string {
    return PHASE_ICONS[phase] ?? PHASE_ICONS['default'];
  }

  isToolPhase(phase: string): boolean {
    return TOOL_PHASES.has(phase);
  }

  hasDetails(ev: AiSnakeTraceEvent): boolean {
    return ev.details != null && Object.keys(ev.details).length > 0;
  }

  formatPreview(value: unknown): string {
    if (value == null) return '';
    if (typeof value === 'string') return value;
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }

  toggleSection(key: 'input' | 'output' | 'details'): void {
    this.openSections[key] = !this.openSections[key];
  }
}
