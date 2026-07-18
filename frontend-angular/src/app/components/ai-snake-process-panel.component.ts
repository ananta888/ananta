import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { Subscription } from 'rxjs';
import { ChatProcessRunSummary, ChatSessionsService, EffectiveChatProcess } from '../services/chat-sessions.service';
import { VisualProcessCanvasComponent } from '../features/visual-process/visual-process-canvas.component';
import { VpCanvasInteractionService } from '../features/visual-process/vp-canvas-interaction.service';
import { VpGraph, VpRuntimeOverlay } from '../features/visual-process/visual-process-api.service';
import { VpStepInspectorComponent } from '../features/visual-process/vp-step-inspector.component';
import { emptyGraph } from '../features/visual-process/vp-editor-config';
import { VpNavigationService } from '../features/visual-process/vp-navigation.service';
import { OidcAuthService } from '../services/oidc-auth.service';

@Component({
  selector: 'app-ai-snake-process-panel',
  standalone: true,
  imports: [CommonModule, VisualProcessCanvasComponent, VpStepInspectorComponent],
  providers: [VpCanvasInteractionService],
  template: `
    <section class="process-panel">
      <header>
        <div><strong>Session-Prozess</strong><small>{{ effective?.source === 'profile' ? 'vom Profil geerbt' : effective?.source === 'session_override' ? 'Session-spezifisch' : 'nicht konfiguriert' }}</small></div>
        @if (effective?.source === 'profile') { <button (click)="clone()">Für diese Session klonen</button> }
        <button (click)="reload()">↻ Status</button>
        @if (effective?.process_ref) { <button (click)="startRun()">▶ Start</button> }
      </header>
      @if (error) { <p class="error">{{ error }}</p> }
      @if (effective?.process_ref; as ref) {
        @if (runs.length) {
          <select [value]="selectedRunId" (change)="selectRun($any($event.target).value)">
            @for (run of runs; track run.run_id) { <option [value]="run.run_id">{{ run.status }} · {{ run.process_version }} · {{ run.run_id }}</option> }
          </select>
        }
        @if (overlay) {
          <div class="runtime" role="status">Status: {{ overlay.overall_status }} · aktualisiert {{ overlay.updated_at | date:'mediumTime' }}</div>
          @for (step of awaitingGates(); track step.step_id) {
            <div class="gate">Freigabe: {{ step.step_id }} @if(oidc.loggedIn$|async){<button (click)="gate(step.step_id,'approve')">Genehmigen</button><button (click)="gate(step.step_id,'reject')">Ablehnen</button>}</div>
          }
        }
        <div class="meta">{{ ref.graph_id }} · Version {{ ref.version }}</div>
        @if (effective?.graph; as graph) {
          <app-visual-process-canvas [graph]="displayGraph(graph)" [runtimeOverlay]="runtimeOverlay()"
                                     [readOnly]="true" mode="compact-readonly" [selectedId]="selectedId()" (stepSelected)="selectedId.set($event)" />
          <app-vp-step-inspector mode="runtime-readonly" [runtimeOverlay]="runtimeOverlay()" [graph]="graphSignal" [selectedId]="selectedId"
            [taskKindList]="emptyList" [skillProfiles]="emptyList" [modelProfiles]="emptyList" [fallbackGroups]="emptyMap"
            [artifactKinds]="[]" [edgeKinds]="[]" [encodingModes]="[]" [ragChannels]="[]" (traceRequested)="navigation.showTrace($event.runId,$event.stepId)" />
        }
      } @else {
        <p>Dem aktiven Chat ist noch kein Prozess zugeordnet. Die Zuordnung erfolgt im Profil- oder Session-Editor.</p>
      }
    </section>
  `,
  styles: [`
    .process-panel{height:100%;overflow:auto;background:#07111f;color:#dce8f8} header{display:flex;align-items:center;gap:8px;padding:8px;border-bottom:1px solid #1a2d4a}
    header div{display:flex;flex-direction:column;flex:1}small,.meta{opacity:.65}.meta{padding:5px 9px}.error{color:#ff8b8b;padding:8px}
  `],
})
export class AiSnakeProcessPanelComponent implements OnInit, OnDestroy {
  private readonly sessions = inject(ChatSessionsService);
  readonly navigation=inject(VpNavigationService);
  readonly oidc=inject(OidcAuthService);
  private readonly subscriptions = new Subscription();
  effective: EffectiveChatProcess | null = null;
  error = '';
  private sessionId = '';
  runs: ChatProcessRunSummary[]=[]; selectedRunId=''; overlay: any=null;
  graphSignal=signal<VpGraph>(emptyGraph());selectedId=signal<string|null>(null);emptyList=signal<any[]>([]);emptyMap=signal<Record<string,any>>({});
  private pollHandle: ReturnType<typeof setInterval>|null=null;
  ngOnInit(): void { this.subscriptions.add(this.sessions.activeSessionId$.subscribe(id => { this.sessionId = id; this.reload(); })); }
  ngOnDestroy(): void { this.stopPolling(); this.subscriptions.unsubscribe(); }
  reload(): void {
    if (!this.sessionId) return;
    this.subscriptions.add(this.sessions.getEffectiveProcess(this.sessionId).subscribe({
      next: result => { this.effective = result; if(result.graph)this.graphSignal.set(this.asGraph(result.graph)); this.error = ''; this.loadRuns(); },
      error: error => { this.effective = null; this.error = error?.error?.error || 'Prozess konnte nicht geladen werden'; },
    }));
  }
  clone(): void { if (this.sessionId) this.subscriptions.add(this.sessions.cloneEffectiveProcess(this.sessionId).subscribe({ next: result => this.effective = result, error: error => this.error = error?.error?.error || 'Klonen fehlgeschlagen' })); }
  asGraph(graph: Record<string, unknown>): VpGraph { return graph as unknown as VpGraph; }
  displayGraph(graph:Record<string,unknown>):VpGraph { return this.asGraph((this.overlay?.graph_snapshot as Record<string,unknown>)||graph); }
  runtimeOverlay(): VpRuntimeOverlay | null { if(!this.overlay)return null; return {...this.overlay,steps:this.overlay.steps||this.overlay.step_states||{}} as VpRuntimeOverlay; }
  loadRuns():void { this.sessions.listProcessRuns(this.sessionId).subscribe(runs=>{this.runs=runs;if(!this.selectedRunId&&runs.length)this.selectRun(runs[0].run_id);}); }
  startRun():void { this.sessions.startProcessRun(this.sessionId).subscribe({next:run=>{this.runs=[run,...this.runs];this.selectRun(run.run_id);},error:e=>this.error=e?.error?.error||'Start fehlgeschlagen'}); }
  selectRun(runId:string):void { this.selectedRunId=runId;this.refreshRun();this.stopPolling();this.pollHandle=setInterval(()=>this.refreshRun(),3000); }
  refreshRun():void { if(!this.selectedRunId)return;this.sessions.getProcessRun(this.sessionId,this.selectedRunId).subscribe({next:o=>{this.overlay=o;if(o['graph_snapshot'])this.graphSignal.set(this.asGraph(o['graph_snapshot'] as Record<string,unknown>));if(['done','failed','cancelled'].includes(String(o['overall_status'])))this.stopPolling();},error:e=>this.error=e?.error?.error||'Run-Status nicht verfügbar'}); }
  awaitingGates():any[] { return Object.values(this.overlay?.steps||this.overlay?.step_states||{}).filter((step:any)=>step.status==='awaiting_approval'); }
  gate(stepId:string,decision:'approve'|'reject'):void { this.sessions.signalProcessGate(this.sessionId,this.selectedRunId,stepId,decision).subscribe({next:()=>this.refreshRun(),error:e=>this.error=e?.error?.error||'Gate-Aktion fehlgeschlagen'}); }
  private stopPolling():void { if(this.pollHandle)clearInterval(this.pollHandle);this.pollHandle=null; }
}
