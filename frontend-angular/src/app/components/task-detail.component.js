var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Component, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { finalize } from 'rxjs';
import { isTaskDone, isTaskInProgress } from '../utils/task-status';
import { TaskStatusDisplayPipe } from '../pipes/task-status-display.pipe';
let TaskDetailComponent = class TaskDetailComponent {
    constructor() {
        this.route = inject(ActivatedRoute);
        this.dir = inject(AgentDirectoryService);
        this.hubApi = inject(HubApiService);
        this.ns = inject(NotificationService);
        this.hub = this.dir.list().find(a => a.role === 'hub');
        this.subtasks = [];
        this.logs = [];
        this.allAgents = this.dir.list();
        this.prompt = '';
        this.proposed = '';
        this.proposedTouched = false;
        this.toolCalls = [];
        this.comparisons = null;
        this.busy = false;
        this.activeTab = 'details';
        this.loadingTask = false;
        this.loadingLogs = false;
        this.availableProviders = [];
        this.loadProviders();
        this.routeSub = this.route.paramMap.subscribe(() => {
            this.proposedTouched = false;
            this.proposed = '';
            this.toolCalls = [];
            this.busy = false; // Sicherheits-Reset bei Task-Wechsel
            this.reload();
        });
    }
    ngOnDestroy() {
        this.stopStreaming();
        this.routeSub?.unsubscribe();
    }
    loadProviders() {
        if (!this.hub)
            return;
        this.hubApi.listProviderCatalog(this.hub.url).subscribe({
            next: (catalog) => {
                const providers = this.flattenCatalogProviders(catalog);
                if (providers.length) {
                    this.availableProviders = providers;
                    return;
                }
                this.loadProvidersFallback();
            },
            error: () => this.loadProvidersFallback()
        });
    }
    flattenCatalogProviders(catalog) {
        const blocks = Array.isArray(catalog?.providers) ? catalog.providers : [];
        const result = [];
        for (const block of blocks) {
            const provider = String(block?.provider || '').trim();
            if (!provider)
                continue;
            const models = Array.isArray(block?.models) ? block.models : [];
            for (const m of models) {
                const modelId = String(m?.id || '').trim();
                if (!modelId)
                    continue;
                result.push({
                    id: `${provider}:${modelId}`,
                    name: `${provider} (${modelId})`,
                    selected: !!m?.selected,
                });
            }
        }
        return result;
    }
    loadProvidersFallback() {
        if (!this.hub)
            return;
        this.hubApi.listProviders(this.hub.url).subscribe({
            next: (providers) => {
                this.availableProviders = providers;
            },
            error: () => {
                console.warn('Providers konnten nicht geladen werden, verwende Fallback');
                this.availableProviders = [
                    { id: 'ollama:llama3', name: 'Ollama (Llama3)', selected: true },
                    { id: 'openai:gpt-4o', name: 'OpenAI (GPT-4o)', selected: false }
                ];
            }
        });
    }
    get tid() { return this.route.snapshot.paramMap.get('id'); }
    setTab(tab) {
        this.activeTab = tab;
        if (tab === 'logs') {
            this.startStreaming();
        }
        else {
            this.stopStreaming();
        }
    }
    reload() {
        if (!this.hub)
            return;
        this.loadingTask = true;
        this.hubApi.getTask(this.hub.url, this.tid).subscribe({
            next: t => {
                this.task = t;
                this.assignUrl = t?.assignment?.agent_url;
                if (!this.proposedTouched) {
                    this.proposed = t?.last_proposal?.command || '';
                    this.toolCalls = t?.last_proposal?.tool_calls || [];
                }
                this.comparisons = t?.last_proposal?.comparisons || null;
                if (this.activeTab === 'logs')
                    this.startStreaming();
                this.loadSubtasks();
            },
            error: () => {
                this.ns.error('Task konnte nicht geladen werden');
            },
            complete: () => {
                this.loadingTask = false;
            }
        });
    }
    loadSubtasks() {
        if (!this.hub)
            return;
        this.hubApi.listTasks(this.hub.url).subscribe({
            next: (tasks) => {
                if (Array.isArray(tasks)) {
                    this.subtasks = tasks.filter(t => t.parent_task_id === this.tid);
                }
            }
        });
    }
    startStreaming() {
        if (!this.hub)
            return;
        this.stopStreaming();
        this.logs = []; // Reset für frischen Stream (Backend sendet history)
        this.loadingLogs = true;
        this.logSub = this.hubApi.streamTaskLogs(this.hub.url, this.tid).subscribe({
            next: (log) => {
                this.loadingLogs = false;
                if (!this.logs.find(l => l.timestamp === log.timestamp && l.command === log.command)) {
                    this.logs = [...this.logs, log];
                }
            },
            error: (err) => {
                console.error('SSE Error', err);
                this.ns.error('Live-Logs Verbindung verloren');
                this.loadingLogs = false;
            }
        });
    }
    stopStreaming() {
        this.logSub?.unsubscribe();
        this.logSub = undefined;
    }
    loadLogs() {
        // Veraltet, wird durch startStreaming() ersetzt, aber wir behalten es falls manuell aufgerufen
        if (!this.hub)
            return;
        this.loadingLogs = true;
        this.hubApi.taskLogs(this.hub.url, this.tid).subscribe({
            next: r => this.logs = Array.isArray(r) ? r : [],
            error: () => this.ns.error('Logs konnten nicht geladen werden'),
            complete: () => { this.loadingLogs = false; }
        });
    }
    reviewProposal(action) {
        if (!this.hub)
            return;
        this.busy = true;
        this.hubApi.reviewTaskProposal(this.hub.url, this.tid, { action }).pipe(finalize(() => this.busy = false)).subscribe({
            next: () => {
                this.ns.success(action === 'approve' ? 'Vorschlag freigegeben' : 'Vorschlag abgelehnt');
                this.reload();
            },
            error: (error) => this.ns.error(this.ns.fromApiError(error, 'Review-Aktion fehlgeschlagen'))
        });
    }
    saveStatus(newStatus) {
        if (!this.hub || !this.task)
            return;
        const status = newStatus || this.task.status;
        this.hubApi.patchTask(this.hub.url, this.tid, { status }).subscribe({
            next: () => {
                this.ns.success(`Status auf ${status} aktualisiert`);
                this.reload();
            },
            error: () => this.ns.error('Status-Update fehlgeschlagen')
        });
    }
    saveAssign() {
        if (!this.hub)
            return;
        const sel = this.allAgents.find(a => a.url === this.assignUrl);
        this.hubApi.assign(this.hub.url, this.tid, { agent_url: this.assignUrl, token: sel?.token }).subscribe({
            next: () => {
                this.ns.success(this.assignUrl ? 'Agent zugewiesen' : 'Zuweisung aufgehoben');
                this.reload();
            },
            error: () => this.ns.error('Zuweisung fehlgeschlagen')
        });
    }
    propose(multi = false) {
        if (!this.hub)
            return;
        this.busy = true;
        const body = { prompt: this.prompt };
        if (multi) {
            body.providers = this.availableProviders.filter(p => p.selected).map(p => p.id);
            if (body.providers.length === 0) {
                // Fallback falls nichts ausgewählt
                body.providers = ['ollama:llama3', 'openai:gpt-4o'];
            }
        }
        this.hubApi.propose(this.hub.url, this.tid, body).pipe(finalize(() => this.busy = false)).subscribe({
            next: (r) => {
                this.proposed = r?.command || '';
                this.toolCalls = r?.tool_calls || [];
                this.proposedTouched = false;
                this.comparisons = r?.comparisons || null;
                this.ns.success('Vorschlag erhalten');
            },
            error: () => {
                this.ns.error('Fehler beim Abrufen des Vorschlags');
            }
        });
    }
    execute() {
        if (!this.hub || (!this.proposed && !this.toolCalls.length))
            return;
        this.busy = true;
        this.hubApi.execute(this.hub.url, this.tid, {
            command: this.proposed,
            tool_calls: this.toolCalls
        }).pipe(finalize(() => this.busy = false)).subscribe({
            next: (r) => {
                this.ns.success('Befehl ausgeführt');
                this.proposed = '';
                this.proposedTouched = false;
                this.toolCalls = [];
                this.loadLogs();
            },
            error: () => {
                this.ns.error('Ausführung fehlgeschlagen');
            }
        });
    }
    useComparison(val) {
        this.proposed = val.command || '';
        this.toolCalls = val.tool_calls || [];
        this.proposedTouched = false;
        this.ns.success('Vorschlag übernommen');
    }
    canExecute() {
        if (this.busy)
            return false;
        const hasCommand = !!(this.proposed && this.proposed.trim().length > 0);
        const hasTools = !!(this.toolCalls && this.toolCalls.length > 0);
        // Debugging falls es wieder passiert
        if (!hasCommand && !hasTools && this.proposedTouched) {
            // User hat etwas getippt, aber es ist leer -> disabled ist korrekt.
        }
        return hasCommand || hasTools;
    }
    onProposedChange(value) {
        this.proposed = value;
        this.proposedTouched = true;
    }
    isDone(status) {
        return isTaskDone(status);
    }
    isInProgress(status) {
        return isTaskInProgress(status);
    }
    isFollowup(taskId) {
        return taskId?.startsWith('followup-');
    }
    qualityGateReason() {
        const out = String(this.task?.last_output || '');
        const marker = '[quality_gate] failed:';
        const idx = out.indexOf(marker);
        if (idx < 0)
            return '';
        return out.slice(idx + marker.length).trim();
    }
    reviewState() {
        return this.task?.last_proposal?.review || null;
    }
    workerContextText() {
        return String(this.task?.worker_execution_context?.context?.context_text || '').trim();
    }
    allowedTools() {
        const tools = this.task?.worker_execution_context?.allowed_tools;
        return Array.isArray(tools) ? tools : [];
    }
    expectedSchema() {
        const schema = this.task?.worker_execution_context?.expected_output_schema;
        if (!schema || typeof schema !== 'object' || !Object.keys(schema).length)
            return null;
        return schema;
    }
    researchSources() {
        const sources = this.task?.last_proposal?.research_artifact?.sources;
        return Array.isArray(sources) ? sources : [];
    }
    provenanceEvents() {
        const history = Array.isArray(this.task?.history) ? this.task.history : [];
        return history.filter((ev) => ['proposal_result', 'execution_result', 'proposal_review', 'task_delegated'].includes(ev?.event_type));
    }
};
TaskDetailComponent = __decorate([
    Component({
        standalone: true,
        selector: 'app-task-detail',
        imports: [CommonModule, FormsModule, RouterLink, TaskStatusDisplayPipe],
        styles: [`
    .tab-btn {
      padding: 8px 16px;
      border: none;
      background: none;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      font-weight: 500;
    }
    .tab-btn.active {
      border-bottom: 2px solid var(--primary-color, #007bff);
      color: var(--primary-color, #007bff);
    }
    .tab-btn:hover:not(.active) {
      background: #f0f0f0;
    }
  `],
        template: `
    <div class="row title-row">
      <h2>Task #{{tid}}</h2>
      <span class="badge" [class.success]="isDone(task?.status)" [class.warning]="isInProgress(task?.status)">{{task?.status | taskStatusDisplay}}</span>
    </div>
    <p class="muted title-muted">{{task?.title}}</p>

    <div class="row tab-row">
      <button class="tab-btn" [class.active]="activeTab === 'details'" (click)="setTab('details')">Details</button>
      <button class="tab-btn" [class.active]="activeTab === 'interact'" (click)="setTab('interact')">Interaktion</button>
      <button class="tab-btn" [class.active]="activeTab === 'context'" (click)="setTab('context')">Kontext & Review</button>
      <button class="tab-btn" [class.active]="activeTab === 'logs'" (click)="setTab('logs')">Logs</button>
    </div>

    @if (activeTab === 'details' && task) {
      <div class="card grid">
        <div class="grid cols-2">
          <label>Status
            <select [ngModel]="task?.status" (ngModelChange)="saveStatus($event)">
              <option value="todo">todo</option>
              <option value="in_progress">in_progress</option>
              <option value="blocked">blocked</option>
              <option value="completed">completed</option>
              <option value="failed">failed</option>
            </select>
          </label>
          <label>Zugewiesener Agent
            <select [(ngModel)]="assignUrl" (ngModelChange)="saveAssign()">
              <option [ngValue]="undefined">– Nicht zugewiesen –</option>
              @for (a of allAgents; track a) {
                <option [ngValue]="a.url">{{a.name}} ({{a.role||'worker'}})</option>
              }
            </select>
          </label>
        </div>
        @if (task?.parent_task_id) {
          <div class="mt-10">
            <strong>Parent Task:</strong>
            <a [routerLink]="['/task', task.parent_task_id]" class="ml-10">{{task.parent_task_id}}</a>
          </div>
        }
        @if (subtasks.length) {
          <div class="mt-10">
            <strong>Subtasks / Follow-ups:</strong>
            <div class="grid mt-5 gap-5">
              @for (st of subtasks; track st) {
                <div class="row board-item no-margin p-5-10">
                  <a [routerLink]="['/task', st.id]">{{st.title}}</a>
                  @if (isFollowup(st.id)) {
                    <span class="badge badge-purple">Auto-Followup</span>
                  }
                  <span class="badge" [class.success]="isDone(st.status)">{{st.status | taskStatusDisplay}}</span>
                </div>
              }
            </div>
          </div>
        }
        <div class="mt-10">
          <strong>Beschreibung:</strong>
          <p>{{task?.description || 'Keine Beschreibung vorhanden.'}}</p>
        </div>
        @if (reviewState()?.required) {
          <div class="card card-light mt-10">
            <div class="row space-between">
              <div>
                <strong>Review-Status:</strong>
                <span class="badge ml-10">{{ reviewState()?.status }}</span>
              </div>
              <div class="row gap-sm">
                <button class="success" (click)="reviewProposal('approve')" [disabled]="busy || reviewState()?.status === 'approved'">Freigeben</button>
                <button class="secondary" (click)="reviewProposal('reject')" [disabled]="busy || reviewState()?.status === 'rejected'">Ablehnen</button>
              </div>
            </div>
            @if (reviewState()?.reason) {
              <div class="muted font-sm mt-sm">{{ reviewState()?.reason }}</div>
            }
          </div>
        }
        @if (qualityGateReason()) {
          <div class="quality-gate-banner">
            <strong>Quality Gate:</strong> {{ qualityGateReason() }}
          </div>
        }
      </div>
    }
    @if (activeTab === 'details' && loadingTask) {
      <div class="card grid">
        <div class="grid cols-2">
          <div class="skeleton line skeleton-32"></div>
          <div class="skeleton line skeleton-32"></div>
        </div>
        <div class="skeleton block mt-10"></div>
      </div>
    }

    @if (activeTab === 'interact') {
      <div class="card grid">
        @if (busy) {
          <div class="skeleton block skeleton-120 mb-md"></div>
          <div class="skeleton line skeleton-40 mb-md"></div>
          <div class="row gap-sm">
            <div class="spinner"></div>
            <span class="muted">Arbeite...</span>
          </div>
        }
        @if (!busy) {
          <div class="grid">
            <label>Spezifischer Prompt (optional)
              <textarea [(ngModel)]="prompt" rows="5" placeholder="Überschreibt den Standard-Prompt für diesen Schritt..."></textarea>
            </label>
            <label>Vorgeschlagener Befehl
              <input [(ngModel)]="proposed" (ngModelChange)="onProposedChange($event)" placeholder="Noch kein Befehl vorgeschlagen" />
            </label>
          @if (comparisons) {
            <div class="mt-10">
              <strong>LLM Vergleich (Multi-Response):</strong>
              <div class="grid mt-5">
                @for (entry of comparisons | keyvalue; track entry) {
                  <div class="card comparison-card"
                    [style.border-color]="entry.value.error ? '#ff4444' : '#eee'">
                    <div class="row comparison-header">
                      <strong>{{entry.key}}</strong>
                      @if (!entry.value.error) {
                        <button class="button-outline comparison-btn" (click)="useComparison(entry.value)">Übernehmen</button>
                      }
                      @if (entry.value.error) {
                        <span class="badge danger">Error</span>
                      }
                    </div>
                    @if (!entry.value.error) {
                      <div class="muted comparison-reason">{{entry.value.reason}}</div>
                    }
                    @if (entry.value.command) {
                      <code class="code-block">{{entry.value.command}}</code>
                    }
                    @if (entry.value.error) {
                      <div class="danger comparison-reason">
                        <i class="fas fa-exclamation-triangle"></i> {{entry.value.error}}
                      </div>
                    }
                  </div>
                }
              </div>
            </div>
          }
          @if (toolCalls.length) {
            <div class="mt-10">
              <strong>Geplante Tool-Aufrufe:</strong>
              @for (tc of toolCalls; track tc) {
                <div class="agent-chip agent-chip-block">
                  <code>{{tc.name}}({{tc.args | json}})</code>
                </div>
              }
            </div>
          }
          <div class="row mt-lg flex-wrap-gap">
            @for (p of availableProviders; track p) {
              <div class="provider-row">
                <input type="checkbox" [id]="'p-' + p.id" [(ngModel)]="p.selected">
                <label [for]="'p-' + p.id" class="provider-label">{{p.name}}</label>
              </div>
            }
          </div>
            <div class="row mt-lg">
              <button (click)="propose()" [disabled]="busy">Vorschlag holen</button>
              <button (click)="propose(true)" [disabled]="busy" class="secondary action-btn">Multi-LLM Vergleich</button>
              <button (click)="execute()" [disabled]="!canExecute()" class="success action-btn">Ausführen</button>
            </div>
          </div>
        }
      </div>
    }

    @if (activeTab === 'context' && task) {
      <div class="grid">
        <div class="card">
          <div class="row space-between">
            <h3 class="no-margin">Worker-Kontext</h3>
            @if (task?.context_bundle_id) {
              <span class="badge">{{ task.context_bundle_id }}</span>
            }
          </div>
          @if (workerContextText()) {
            <pre class="log-output">{{ workerContextText() }}</pre>
          } @else {
            <p class="muted">Kein expliziter Worker-Kontext vorhanden.</p>
          }
          @if (allowedTools().length) {
            <div class="mt-10">
              <strong>Erlaubte Tools</strong>
              <div class="row mt-5 flex-wrap-gap">
                @for (tool of allowedTools(); track tool) {
                  <span class="agent-chip">{{ tool }}</span>
                }
              </div>
            </div>
          }
          @if (expectedSchema()) {
            <div class="mt-10">
              <strong>Erwartetes Output-Schema</strong>
              <pre class="log-output">{{ expectedSchema() | json }}</pre>
            </div>
          }
        </div>

        <div class="card">
          <h3 class="no-margin">Worker-Run & Provenance</h3>
          <div class="grid cols-2 mt-sm">
            <div>
              <div class="muted">Current Worker Job</div>
              <strong>{{ task?.current_worker_job_id || '—' }}</strong>
            </div>
            <div>
              <div class="muted">Memory Entry</div>
              <strong>{{ task?.verification_status?.memory_entry_id || '—' }}</strong>
            </div>
            <div>
              <div class="muted">Verification Record</div>
              <strong>{{ task?.verification_status?.record_id || '—' }}</strong>
            </div>
            <div>
              <div class="muted">Goal Trace</div>
              <strong>{{ task?.goal_trace_id || task?.last_proposal?.trace?.trace_id || '—' }}</strong>
            </div>
          </div>
          @if (task?.last_proposal?.trace || task?.history?.length) {
            <div class="mt-10">
              <strong>Provenance Events</strong>
              <div class="grid mt-5 gap-5">
                @for (ev of provenanceEvents(); track ev.timestamp + '-' + ev.event_type) {
                  <div class="card card-light">
                    <div class="row space-between">
                      <strong>{{ ev.event_type }}</strong>
                      <span class="muted font-sm">{{ ev.timestamp * 1000 | date:'HH:mm:ss' }}</span>
                    </div>
                    @if (ev.reason) {
                      <div class="muted mt-5">{{ ev.reason }}</div>
                    }
                    @if (ev.backend) {
                      <div class="muted font-sm mt-5">Backend: {{ ev.backend }}</div>
                    }
                    @if (ev.artifact_ref?.artifact_id) {
                      <div class="muted font-sm mt-5">Artifact: {{ ev.artifact_ref.artifact_id }}</div>
                    }
                  </div>
                }
              </div>
            </div>
          }
        </div>

        <div class="card">
          <h3 class="no-margin">Review & Resultate</h3>
          @if (reviewState()) {
            <div class="grid cols-2 mt-sm">
              <div>
                <div class="muted">Review Status</div>
                <strong>{{ reviewState()?.status }}</strong>
              </div>
              <div>
                <div class="muted">Reviewed By</div>
                <strong>{{ reviewState()?.reviewed_by || '—' }}</strong>
              </div>
            </div>
            @if (reviewState()?.comment) {
              <div class="mt-10">
                <strong>Kommentar</strong>
                <p>{{ reviewState()?.comment }}</p>
              </div>
            }
          } @else {
            <p class="muted">Kein Review erforderlich oder noch kein Forschungsvorschlag vorhanden.</p>
          }
          @if (researchSources().length) {
            <div class="mt-10">
              <strong>Research Sources</strong>
              <div class="grid mt-5 gap-5">
                @for (source of researchSources(); track source.url) {
                  <div class="card card-light">
                    <div><strong>{{ source.title || source.url }}</strong></div>
                    <div class="muted font-sm">{{ source.kind || 'web' }} · {{ source.confidence ?? 'n/a' }}</div>
                    <a [href]="source.url" target="_blank" rel="noreferrer">{{ source.url }}</a>
                  </div>
                }
              </div>
            </div>
          }
        </div>
      </div>
    }

    @if (activeTab === 'logs') {
      <div class="card">
        <h3>Task Logs (Live)</h3>
        @if (loadingLogs) {
          <div class="skeleton block skeleton-120 mb-md"></div>
          <div class="skeleton line skeleton-40 mb-md"></div>
          <div class="skeleton line skeleton-40"></div>
        }
        @if (logs.length) {
          <div class="grid">
            @for (l of logs; track l) {
              <div class="log-entry">
                <div class="row flex-between">
                  <code class="log-entry-code">{{l.command}}</code>
                  <span class="badge" [class.success]="l.exit_code===0" [class.danger]="l.exit_code!==0">RC: {{l.exit_code}}</span>
                </div>
                @if (l.output) {
                  <pre class="log-output">{{l.output}}</pre>
                }
                @if (l.reason) {
                  <div class="muted log-reason">Reason: {{l.reason}}</div>
                }
              </div>
            }
          </div>
        } @else {
          <p class="muted">Bisher wurden keine Aktionen für diesen Task geloggt.</p>
        }
      </div>
    }
    `
    })
], TaskDetailComponent);
export { TaskDetailComponent };
//# sourceMappingURL=task-detail.component.js.map