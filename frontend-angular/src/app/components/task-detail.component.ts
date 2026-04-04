import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { NotificationService } from '../services/notification.service';
import { UserAuthService } from '../services/user-auth.service';
import { Subscription, finalize } from 'rxjs';
import { isTaskDone, isTaskInProgress } from '../utils/task-status';
import { TaskStatusDisplayPipe } from '../pipes/task-status-display.pipe';
import { TaskManagementFacade } from '../features/tasks/task-management.facade';
import { UiSkeletonComponent } from './ui-skeleton.component';

@Component({
  standalone: true,
  selector: 'app-task-detail',
  imports: [CommonModule, FormsModule, RouterLink, TaskStatusDisplayPipe, UiSkeletonComponent],
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
        @if (latestExecutionCostSummary()) {
          <div class="card card-light mt-10">
            <h3 class="no-margin">Execution Cost Summary</h3>
            <div class="grid cols-2 mt-sm">
              <div>
                <div class="muted">Kosten</div>
                <strong>{{ latestExecutionCostSummary()?.cost_units || 0 | number:'1.2-4' }}</strong>
              </div>
              <div>
                <div class="muted">Tokens</div>
                <strong>{{ latestExecutionCostSummary()?.tokens_total || 0 }}</strong>
              </div>
              <div>
                <div class="muted">Latenz</div>
                <strong>{{ latestExecutionCostSummary()?.latency_ms || 0 }} ms</strong>
              </div>
              <div>
                <div class="muted">Provider / Model</div>
                <strong>{{ latestExecutionCostSummary()?.provider || '—' }} / {{ latestExecutionCostSummary()?.model || '—' }}</strong>
              </div>
              <div>
                <div class="muted">Inference Routing</div>
                <strong>{{ effectiveExecutionRoutingSummary()?.inference_provider || '—' }} / {{ effectiveExecutionRoutingSummary()?.inference_model || '—' }}</strong>
              </div>
              <div>
                <div class="muted">Execution Backend</div>
                <strong>{{ effectiveExecutionRoutingSummary()?.execution_backend || '—' }}</strong>
              </div>
            </div>
          </div>
        }
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
        <app-ui-skeleton [count]="2" [columns]="2" [lineCount]="1" [card]="false" lineClass="skeleton line skeleton-32"></app-ui-skeleton>
        <app-ui-skeleton [count]="1" [lineCount]="1" [card]="false" containerClass="mt-10" lineClass="skeleton block"></app-ui-skeleton>
      </div>
    }

    @if (activeTab === 'interact') {
      <div class="card grid">
        @if (busy) {
          <app-ui-skeleton [count]="1" [lineCount]="1" [card]="false" containerClass="mb-md" lineClass="skeleton block skeleton-120"></app-ui-skeleton>
          <app-ui-skeleton [count]="1" [lineCount]="1" [card]="false" containerClass="mb-md" lineClass="skeleton line skeleton-40"></app-ui-skeleton>
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
            <div class="row gap-sm">
              @if (task?.context_bundle_id) {
                <span class="badge">{{ task.context_bundle_id }}</span>
              }
              @if (isAdmin) {
                <button class="secondary btn-small" type="button" (click)="showAdminDrilldown = !showAdminDrilldown">
                  {{ showAdminDrilldown ? 'Admin-Drilldown ausblenden' : 'Admin-Drilldown zeigen' }}
                </button>
              }
            </div>
          </div>
          @if (!isAdmin) {
            <p class="muted font-sm mt-10">Admin-Drilldown ist ausgeblendet. Sichtbar bleiben nur Summary-Informationen.</p>
          }
          @if (isAdmin && !showAdminDrilldown) {
            <p class="muted font-sm mt-10">Drilldown aktivieren, um Routing-, Policy- und detaillierte Provenance-Daten einzusehen.</p>
          }
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
          @if (isAdmin && showAdminDrilldown && routingDecision()) {
            <div class="mt-10">
              <strong>Routing-Entscheidung</strong>
              <div class="grid cols-2 mt-5">
                <div>
                  <div class="muted">Strategie</div>
                  <strong>{{ routingDecision()?.strategy || '—' }}</strong>
                </div>
                <div>
                  <div class="muted">Task Kind</div>
                  <strong>{{ routingDecision()?.task_kind || '—' }}</strong>
                </div>
              </div>
              @if (routingRequiredCapabilities().length) {
                <div class="mt-10">
                  <div class="muted">Required Capabilities</div>
                  <div class="row mt-5 flex-wrap-gap">
                    @for (capability of routingRequiredCapabilities(); track capability) {
                      <span class="agent-chip">{{ capability }}</span>
                    }
                  </div>
                </div>
              }
              @if (routingMatchedCapabilities().length) {
                <div class="mt-10">
                  <div class="muted">Matched Capabilities</div>
                  <div class="row mt-5 flex-wrap-gap">
                    @for (capability of routingMatchedCapabilities(); track capability) {
                      <span class="agent-chip">{{ capability }}</span>
                    }
                  </div>
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
            <div>
              <div class="muted">CLI Session</div>
              <strong>{{ task?.last_proposal?.routing?.session_id || task?.verification_status?.cli_session?.session_id || '—' }}</strong>
            </div>
            <div>
              <div class="muted">Session Mode</div>
              <strong>{{ task?.last_proposal?.routing?.session_mode || 'stateless' }}</strong>
            </div>
          </div>
          @if (isAdmin && showAdminDrilldown && (task?.last_proposal?.trace || task?.history?.length)) {
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
          @if (isAdmin && showAdminDrilldown && researchSources().length) {
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
          @if (isAdmin && showAdminDrilldown && researchCitations().length) {
            <div class="mt-10">
              <strong>Research Citations</strong>
              <div class="grid mt-5 gap-5">
                @for (citation of researchCitations(); track citation.url + '-' + citation.label) {
                  <div class="card card-light">
                    <div><strong>{{ citation.label || citation.url }}</strong></div>
                    @if (citation.excerpt) {
                      <div class="muted font-sm mt-5">{{ citation.excerpt }}</div>
                    }
                    @if (citation.url) {
                      <a [href]="citation.url" target="_blank" rel="noreferrer">{{ citation.url }}</a>
                    }
                  </div>
                }
              </div>
            </div>
          }
          @if (isAdmin && showAdminDrilldown && researchVerification()) {
            <div class="mt-10">
              <strong>Research Verification</strong>
              <pre class="log-output">{{ researchVerification() | json }}</pre>
            </div>
          }
          @if (isAdmin && showAdminDrilldown && researchBackendMetadata()) {
            <div class="mt-10">
              <strong>Research Backend Metadata</strong>
              <pre class="log-output">{{ researchBackendMetadata() | json }}</pre>
            </div>
          }
        </div>
      </div>
    }

    @if (activeTab === 'logs') {
      <div class="card">
        <h3>Task Logs (Live)</h3>
        @if (loadingLogs) {
          <app-ui-skeleton [count]="1" [lineCount]="1" [card]="false" containerClass="mb-md" lineClass="skeleton block skeleton-120"></app-ui-skeleton>
          <app-ui-skeleton [count]="1" [lineCount]="2" [card]="false" lineClass="skeleton line skeleton-40"></app-ui-skeleton>
        }
        @if (logs.length) {
          <div class="grid">
            @for (l of logs; track l) {
              <div class="log-entry">
                <div class="row flex-between">
                  <code class="log-entry-code">{{l.command || l.event_type || 'history_event'}}</code>
                  @if (l.exit_code !== undefined && l.exit_code !== null) {
                    <span class="badge" [class.success]="l.exit_code===0" [class.danger]="l.exit_code!==0">RC: {{l.exit_code}}</span>
                  } @else if (l.event_type) {
                    <span class="badge">{{l.event_type}}</span>
                  }
                </div>
                @if (l.output) {
                  <pre class="log-output">{{l.output}}</pre>
                }
                @if (l.reason) {
                  <div class="muted log-reason">Reason: {{l.reason}}</div>
                }
                @if (l.cost_summary) {
                  <div class="muted log-reason">
                    Cost: {{ l.cost_summary.cost_units || 0 | number:'1.2-4' }} · Tokens: {{ l.cost_summary.tokens_total || 0 }} · Latency: {{ l.cost_summary.latency_ms || 0 }} ms
                  </div>
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
export class TaskDetailComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private dir = inject(AgentDirectoryService);
  private ns = inject(NotificationService);
  private auth = inject(UserAuthService);
  private taskFacade = inject(TaskManagementFacade);

  hub = this.dir.list().find(a => a.role === 'hub');
  task: any;
  subtasks: any[] = [];
  logs: any[] = [];
  allAgents = this.dir.list();
  assignUrl: string | undefined;
  prompt = '';
  proposed = '';
  proposedTouched = false;
  toolCalls: any[] = [];
  comparisons: Record<string, any> | null = null;
  busy = false;
  activeTab = 'details';
  loadingTask = false;
  loadingLogs = false;
  availableProviders: any[] = [];
  isAdmin = false;
  showAdminDrilldown = false;
  private routeSub?: Subscription;
  private activeLogTaskId?: string;

  constructor() {
    this.loadProviders();
    this.routeSub = this.route.paramMap.subscribe(() => {
      this.stopStreaming();
      this.proposedTouched = false;
      this.proposed = '';
      this.toolCalls = [];
      this.busy = false; // Sicherheits-Reset bei Task-Wechsel
      this.reload();
    });
  }

  ngOnInit() {
    const user = this.auth.decodeTokenPayload(this.auth.token);
    this.isAdmin = user?.role === 'admin';
    if (this.hub?.url) {
      this.taskFacade.connectTaskCollection(this.hub.url);
      this.taskFacade.reloadTaskCollection();
    }
  }

  ngOnDestroy() {
    this.stopStreaming();
    this.routeSub?.unsubscribe();
    this.taskFacade.disconnectTaskCollection(this.hub?.url);
  }

  loadProviders() {
    if (!this.hub) return;
    this.taskFacade.listProviderCatalog(this.hub.url).subscribe({
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

  private flattenCatalogProviders(catalog: any): any[] {
    const blocks = Array.isArray(catalog?.providers) ? catalog.providers : [];
    const result: any[] = [];
    for (const block of blocks) {
      const provider = String(block?.provider || '').trim();
      if (!provider) continue;
      const models = Array.isArray(block?.models) ? block.models : [];
      for (const m of models) {
        const modelId = String(m?.id || '').trim();
        if (!modelId) continue;
        result.push({
          id: `${provider}:${modelId}`,
          name: `${provider} (${modelId})`,
          selected: !!m?.selected,
        });
      }
    }
    return result;
  }

  private loadProvidersFallback() {
    if (!this.hub) return;
    this.taskFacade.listProviders(this.hub.url).subscribe({
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

  get tid(){ return this.route.snapshot.paramMap.get('id')!; }

  setTab(tab: string) {
    this.activeTab = tab;
    if (tab === 'logs') {
      this.startStreaming();
    } else {
      this.stopStreaming();
    }
  }

  reload(){
    if(!this.hub) return;
    this.loadingTask = true;
    this.taskFacade.getTask(this.hub.url, this.tid).subscribe({
      next: t => {
        this.task = t;
        this.assignUrl = t?.assignment?.agent_url;
        if (!this.proposedTouched) {
          this.proposed = t?.last_proposal?.command || '';
          this.toolCalls = t?.last_proposal?.tool_calls || [];
        }
        this.comparisons = t?.last_proposal?.comparisons || null;
        if (this.activeTab === 'logs' && !this.activeLogTaskId) this.startStreaming();
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
    if (!this.hub) return;
    const cachedSubtasks = this.taskFacade.childrenOf(this.tid);
    if (cachedSubtasks.length) {
      this.subtasks = cachedSubtasks;
      return;
    }
    this.taskFacade.listTasks(this.hub.url).subscribe({
      next: (tasks: any) => {
        if (Array.isArray(tasks)) {
          this.subtasks = tasks.filter(t => t.parent_task_id === this.tid);
        }
      }
    });
  }

  startStreaming() {
    if(!this.hub) return;
    this.stopStreaming();
    this.activeLogTaskId = this.tid;
    this.logs = [];
    this.loadingLogs = true;
    this.taskFacade.watchTaskLogs(this.hub.url, this.tid, {
      reset: true,
      onEvent: (log) => {
        const state = this.taskFacade.taskLogState(this.tid);
        this.logs = state.logs;
        this.loadingLogs = state.loading;
        if (this.taskFacade.shouldRefreshTask(log)) {
          this.reload();
        }
      },
      onError: (err) => {
        console.error('SSE Error', err);
        this.ns.error('Live-Logs Verbindung verloren');
        this.loadingLogs = false;
      }
    });
  }

  stopStreaming() {
    this.taskFacade.stopTaskLogs(this.activeLogTaskId);
    this.activeLogTaskId = undefined;
  }

  loadLogs(){
    // Veraltet, wird durch startStreaming() ersetzt, aber wir behalten es falls manuell aufgerufen
    if(!this.hub) return;
    this.loadingLogs = true;
    this.taskFacade.taskLogs(this.hub.url, this.tid).subscribe({
      next: r => this.logs = Array.isArray(r) ? r : [],
      error: () => this.ns.error('Logs konnten nicht geladen werden'),
      complete: () => { this.loadingLogs = false; }
    });
  }

  reviewProposal(action: 'approve' | 'reject') {
    if (!this.hub) return;
    this.busy = true;
    this.taskFacade.reviewTaskProposal(this.hub.url, this.tid, { action }).pipe(
      finalize(() => this.busy = false)
    ).subscribe({
      next: () => {
        this.ns.success(action === 'approve' ? 'Vorschlag freigegeben' : 'Vorschlag abgelehnt');
        this.reload();
      },
      error: (error) => this.ns.error(this.ns.fromApiError(error, 'Review-Aktion fehlgeschlagen'))
    });
  }

  saveStatus(newStatus?: string){
    if(!this.hub || !this.task) return;
    const status = newStatus || this.task.status;
    this.taskFacade.patchTask(this.hub.url, this.tid, { status }).subscribe({
      next: () => {
        this.ns.success(`Status auf ${status} aktualisiert`);
        this.reload();
      },
      error: () => this.ns.error('Status-Update fehlgeschlagen')
    });
  }
  saveAssign(){
    if(!this.hub) return;
    const sel = this.allAgents.find(a => a.url === this.assignUrl);
    this.taskFacade.assignTask(this.hub.url, this.tid, { agent_url: this.assignUrl, token: sel?.token }).subscribe({
      next: () => {
        this.ns.success(this.assignUrl ? 'Agent zugewiesen' : 'Zuweisung aufgehoben');
        this.reload();
      },
      error: () => this.ns.error('Zuweisung fehlgeschlagen')
    });
  }
  propose(multi: boolean = false){
    if(!this.hub) return;
    this.busy = true;
    const body: any = { prompt: this.prompt };
    if (multi) {
      body.providers = this.availableProviders.filter(p => p.selected).map(p => p.id);
      if (body.providers.length === 0) {
        // Fallback falls nichts ausgewählt
        body.providers = ['ollama:llama3', 'openai:gpt-4o'];
      }
    }
    this.taskFacade.proposeTask(this.hub.url, this.tid, body).pipe(
      finalize(() => this.busy = false)
    ).subscribe({
      next: (r:any) => {
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
  execute(){
    if(!this.hub || (!this.proposed && !this.toolCalls.length)) return;
    this.busy = true;
    this.taskFacade.executeTask(this.hub.url, this.tid, {
      command: this.proposed,
      tool_calls: this.toolCalls
    }).pipe(
      finalize(() => this.busy = false)
    ).subscribe({
      next: (r: any) => {
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

  useComparison(val: any) {
    this.proposed = val.command || '';
    this.toolCalls = val.tool_calls || [];
    this.proposedTouched = false;
    this.ns.success('Vorschlag übernommen');
  }

  canExecute(): boolean {
    if (this.busy) return false;
    const hasCommand = !!(this.proposed && this.proposed.trim().length > 0);
    const hasTools = !!(this.toolCalls && this.toolCalls.length > 0);

    // Debugging falls es wieder passiert
    if (!hasCommand && !hasTools && this.proposedTouched) {
      // User hat etwas getippt, aber es ist leer -> disabled ist korrekt.
    }

    return hasCommand || hasTools;
  }

  onProposedChange(value: string) {
    this.proposed = value;
    this.proposedTouched = true;
  }

  isDone(status: string | undefined | null): boolean {
    return isTaskDone(status);
  }

  isInProgress(status: string | undefined | null): boolean {
    return isTaskInProgress(status);
  }

  isFollowup(taskId: string): boolean {
    return taskId?.startsWith('followup-');
  }

  qualityGateReason(): string {
    const out = String(this.task?.last_output || '');
    const marker = '[quality_gate] failed:';
    const idx = out.indexOf(marker);
    if (idx < 0) return '';
    return out.slice(idx + marker.length).trim();
  }

  reviewState(): any {
    return this.task?.last_proposal?.review || null;
  }

  workerContextText(): string {
    return String(this.task?.worker_execution_context?.context?.context_text || '').trim();
  }

  allowedTools(): string[] {
    const tools = this.task?.worker_execution_context?.allowed_tools;
    return Array.isArray(tools) ? tools : [];
  }

  expectedSchema(): any {
    const schema = this.task?.worker_execution_context?.expected_output_schema;
    if (!schema || typeof schema !== 'object' || !Object.keys(schema).length) return null;
    return schema;
  }

  routingDecision(): any {
    const routing = this.task?.worker_execution_context?.routing;
    if (!routing || typeof routing !== 'object') return null;
    return routing;
  }

  routingRequiredCapabilities(): string[] {
    const caps = this.routingDecision()?.required_capabilities;
    return Array.isArray(caps) ? caps : [];
  }

  routingMatchedCapabilities(): string[] {
    const caps = this.routingDecision()?.matched_capabilities;
    return Array.isArray(caps) ? caps : [];
  }

  researchSources(): any[] {
    const sources = this.task?.last_proposal?.research_artifact?.sources;
    return Array.isArray(sources) ? sources : [];
  }

  researchCitations(): any[] {
    const citations = this.task?.last_proposal?.research_artifact?.citations;
    return Array.isArray(citations) ? citations : [];
  }

  researchVerification(): any {
    const verification = this.task?.last_proposal?.research_artifact?.verification;
    if (!verification || typeof verification !== 'object' || !Object.keys(verification).length) return null;
    return verification;
  }

  researchBackendMetadata(): any {
    const metadata = this.task?.last_proposal?.research_artifact?.backend_metadata;
    if (!metadata || typeof metadata !== 'object' || !Object.keys(metadata).length) return null;
    return metadata;
  }

  provenanceEvents(): any[] {
    const history = Array.isArray(this.task?.history) ? this.task.history : [];
    return history.filter((ev: any) => ['proposal_result', 'execution_result', 'proposal_review', 'task_delegated'].includes(ev?.event_type));
  }

  latestExecutionCostSummary(): any {
    const directSummary = this.task?.verification_status?.execution_cost || this.task?.cost_summary;
    if (directSummary && typeof directSummary === 'object') return directSummary;
    const history = Array.isArray(this.task?.history) ? [...this.task.history].reverse() : [];
    const executionEvent = history.find((ev: any) => ev?.cost_summary && (ev?.event_type === 'execution_result' || ev?.event_type === 'proposal_result'));
    return executionEvent?.cost_summary || null;
  }

  proposalRoutingDimensions(): any {
    const routing = this.task?.last_proposal?.routing;
    if (!routing || typeof routing !== 'object') return null;
    return routing;
  }

  effectiveExecutionRoutingSummary(): any {
    const verified = this.task?.verification_status?.execution_routing;
    if (verified && typeof verified === 'object') return verified;
    const cost = this.latestExecutionCostSummary();
    if (cost && typeof cost === 'object' && (cost.inference_provider || cost.execution_backend)) {
      return {
        inference_provider: cost.inference_provider,
        inference_model: cost.inference_model,
        execution_backend: cost.execution_backend,
      };
    }
    const proposalRouting = this.proposalRoutingDimensions();
    if (proposalRouting && typeof proposalRouting === 'object') {
      return {
        inference_provider: proposalRouting.inference_provider,
        inference_model: proposalRouting.inference_model,
        execution_backend: proposalRouting.execution_backend || proposalRouting.effective_backend,
      };
    }
    return null;
  }
}
