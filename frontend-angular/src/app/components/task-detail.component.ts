import { Component, OnDestroy, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AgentDirectoryService } from '../services/agent-directory.service';
import { HubApiService } from '../services/hub-api.service';
import { NotificationService } from '../services/notification.service';
import { Subscription, finalize } from 'rxjs';
import { isTaskDone, isTaskInProgress } from '../utils/task-status';
import { TaskStatusDisplayPipe } from '../pipes/task-status-display.pipe';

@Component({
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
    <div class="row" style="justify-content: space-between; align-items: center;">
      <h2>Task #{{tid}}</h2>
      <span class="badge" [class.success]="isDone(task?.status)" [class.warning]="isInProgress(task?.status)">{{task?.status | taskStatusDisplay}}</span>
    </div>
    <p class="muted" style="margin-top: -10px; margin-bottom: 20px;">{{task?.title}}</p>
    
    <div class="row" style="margin-bottom: 16px; border-bottom: 1px solid #ddd;">
      <button class="tab-btn" [class.active]="activeTab === 'details'" (click)="setTab('details')">Details</button>
      <button class="tab-btn" [class.active]="activeTab === 'interact'" (click)="setTab('interact')">Interaktion</button>
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
          <div style="margin-top: 10px;">
            <strong>Parent Task:</strong>
            <a [routerLink]="['/task', task.parent_task_id]" style="margin-left: 10px;">{{task.parent_task_id}}</a>
          </div>
        }
        @if (subtasks.length) {
          <div style="margin-top: 10px;">
            <strong>Subtasks:</strong>
            <div class="grid" style="margin-top: 5px; gap: 5px;">
              @for (st of subtasks; track st) {
                <div class="row board-item" style="margin: 0; padding: 5px 10px;">
                  <a [routerLink]="['/task', st.id]">{{st.title}}</a>
                  <span class="badge" [class.success]="isDone(st.status)">{{st.status | taskStatusDisplay}}</span>
                </div>
              }
            </div>
          </div>
        }
        <div style="margin-top: 10px;">
          <strong>Beschreibung:</strong>
          <p>{{task?.description || 'Keine Beschreibung vorhanden.'}}</p>
        </div>
        @if (qualityGateReason()) {
          <div style="margin-top: 8px; padding: 8px; border: 1px solid #f5c2c7; background: #fff5f6; border-radius: 4px;">
            <strong>Quality Gate:</strong> {{ qualityGateReason() }}
          </div>
        }
      </div>
    }
    @if (activeTab === 'details' && loadingTask) {
      <div class="card grid">
        <div class="grid cols-2">
          <div class="skeleton line" style="height: 32px;"></div>
          <div class="skeleton line" style="height: 32px;"></div>
        </div>
        <div class="skeleton block" style="margin-top: 10px;"></div>
      </div>
    }
    
    @if (activeTab === 'interact') {
      <div class="card grid">
        <div class="grid">
          <label>Spezifischer Prompt (optional)
            <textarea [(ngModel)]="prompt" rows="5" placeholder="Überschreibt den Standard-Prompt für diesen Schritt..."></textarea>
          </label>
          <label>Vorgeschlagener Befehl
            <input [(ngModel)]="proposed" (ngModelChange)="onProposedChange($event)" placeholder="Noch kein Befehl vorgeschlagen" />
          </label>
          @if (comparisons) {
            <div style="margin-top: 10px;">
              <strong>LLM Vergleich (Multi-Response):</strong>
              <div class="grid" style="gap: 10px; margin-top: 5px;">
                @for (entry of comparisons | keyvalue; track entry) {
                  <div class="card"
                    [style.border-color]="entry.value.error ? '#ff4444' : '#eee'"
                    style="padding: 10px; font-size: 0.9em; border-left-width: 4px;">
                    <div class="row" style="justify-content: space-between;">
                      <strong>{{entry.key}}</strong>
                      @if (!entry.value.error) {
                        <button class="button-outline" style="padding: 2px 8px; font-size: 0.8em;" (click)="useComparison(entry.value)">Übernehmen</button>
                      }
                      @if (entry.value.error) {
                        <span class="badge danger">Error</span>
                      }
                    </div>
                    @if (!entry.value.error) {
                      <div class="muted" style="margin-top: 4px; font-style: italic;">{{entry.value.reason}}</div>
                    }
                    @if (entry.value.command) {
                      <code style="display: block; margin-top: 4px; background: #eee; padding: 2px;">{{entry.value.command}}</code>
                    }
                    @if (entry.value.error) {
                      <div class="danger" style="margin-top: 5px; font-weight: bold;">
                        <i class="fas fa-exclamation-triangle"></i> {{entry.value.error}}
                      </div>
                    }
                  </div>
                }
              </div>
            </div>
          }
          @if (toolCalls.length) {
            <div style="margin-top: 10px;">
              <strong>Geplante Tool-Aufrufe:</strong>
              @for (tc of toolCalls; track tc) {
                <div class="agent-chip" style="margin: 5px 0; width: 100%; display: block;">
                  <code>{{tc.name}}({{tc.args | json}})</code>
                </div>
              }
            </div>
          }
          <div class="row" style="margin-top: 15px; flex-wrap: wrap; gap: 10px;">
            @for (p of availableProviders; track p) {
              <div style="display: flex; align-items: center; gap: 5px;">
                <input type="checkbox" [id]="'p-' + p.id" [(ngModel)]="p.selected">
                <label [for]="'p-' + p.id" style="margin: 0; cursor: pointer;">{{p.name}}</label>
              </div>
            }
          </div>
          <div class="row" style="margin-top: 15px;">
            <button (click)="propose()" [disabled]="busy">Vorschlag holen</button>
            <button (click)="propose(true)" [disabled]="busy" class="secondary" style="margin-left: 5px;">Multi-LLM Vergleich</button>
            <button (click)="execute()" [disabled]="!canExecute()" class="success" style="margin-left: 5px;">Ausführen</button>
            @if (busy) {
              <span class="muted">Arbeite...</span>
            }
          </div>
        </div>
      </div>
    }
    
    @if (activeTab === 'logs') {
      <div class="card">
        <h3>Task Logs (Live)</h3>
        @if (loadingLogs) {
          <div class="row" style="gap: 6px;">
            <div class="spinner"></div>
            <span class="muted">Lade Logs...</span>
          </div>
        }
        @if (logs.length) {
          <div class="grid">
            @for (l of logs; track l) {
              <div style="border-bottom: 1px solid #eee; padding: 8px 0;">
                <div class="row" style="justify-content: space-between;">
                  <code style="word-break: break-all;">{{l.command}}</code>
                  <span class="badge" [class.success]="l.exit_code===0" [class.danger]="l.exit_code!==0">RC: {{l.exit_code}}</span>
                </div>
                @if (l.output) {
                  <pre style="font-size: 11px; margin-top: 5px; background: #f4f4f4; padding: 4px; overflow-x: auto;">{{l.output}}</pre>
                }
                @if (l.reason) {
                  <div class="muted" style="font-size: 0.8em; margin-top: 4px;">Reason: {{l.reason}}</div>
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
export class TaskDetailComponent implements OnDestroy {
  private route = inject(ActivatedRoute);
  private dir = inject(AgentDirectoryService);
  private hubApi = inject(HubApiService);
  private ns = inject(NotificationService);

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
  private logSub?: Subscription;
  private routeSub?: Subscription;

  constructor() {
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
    if (!this.hub) return;
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
    this.hubApi.getTask(this.hub.url, this.tid).subscribe({ 
      next: t => { 
        this.task = t; 
        this.assignUrl = t?.assignment?.agent_url; 
        if (!this.proposedTouched) {
          this.proposed = t?.last_proposal?.command || '';
          this.toolCalls = t?.last_proposal?.tool_calls || [];
        }
        this.comparisons = t?.last_proposal?.comparisons || null;
        if (this.activeTab === 'logs') this.startStreaming();
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
    this.hubApi.listTasks(this.hub.url).subscribe({
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

  loadLogs(){ 
    // Veraltet, wird durch startStreaming() ersetzt, aber wir behalten es falls manuell aufgerufen
    if(!this.hub) return; 
    this.loadingLogs = true;
    this.hubApi.taskLogs(this.hub.url, this.tid).subscribe({ 
      next: r => this.logs = Array.isArray(r) ? r : [],
      error: () => this.ns.error('Logs konnten nicht geladen werden'),
      complete: () => { this.loadingLogs = false; }
    }); 
  }
  saveStatus(newStatus?: string){ 
    if(!this.hub || !this.task) return; 
    const status = newStatus || this.task.status;
    this.hubApi.patchTask(this.hub.url, this.tid, { status }).subscribe({ 
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
    this.hubApi.assign(this.hub.url, this.tid, { agent_url: this.assignUrl, token: sel?.token }).subscribe({ 
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
    this.hubApi.propose(this.hub.url, this.tid, body).pipe(
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
    this.hubApi.execute(this.hub.url, this.tid, { 
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

  qualityGateReason(): string {
    const out = String(this.task?.last_output || '');
    const marker = '[quality_gate] failed:';
    const idx = out.indexOf(marker);
    if (idx < 0) return '';
    return out.slice(idx + marker.length).trim();
  }
}
