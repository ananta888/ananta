import { CommonModule } from '@angular/common';
import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { Subscription } from 'rxjs';
import { ChatSessionsService, EffectiveChatProcess } from '../services/chat-sessions.service';
import { VisualProcessEditorComponent } from '../features/visual-process/visual-process-editor.component';

@Component({
  selector: 'app-ai-snake-process-panel',
  standalone: true,
  imports: [CommonModule, VisualProcessEditorComponent],
  template: `
    <section class="process-panel">
      <header>
        <div><strong>Session-Prozess</strong><small>{{ effective?.source === 'profile' ? 'vom Profil geerbt' : effective?.source === 'session' ? 'Session-spezifisch' : 'nicht konfiguriert' }}</small></div>
        @if (effective?.source === 'profile') { <button (click)="clone()">Für diese Session klonen</button> }
        <button (click)="reload()">↻ Status</button>
      </header>
      @if (error) { <p class="error">{{ error }}</p> }
      @if (effective?.process_ref; as ref) {
        <div class="meta">{{ ref.graph_id }} · Version {{ ref.version }}</div>
        <app-visual-process-editor [graphId]="ref.graph_id" />
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
  private readonly subscriptions = new Subscription();
  effective: EffectiveChatProcess | null = null;
  error = '';
  private sessionId = '';
  ngOnInit(): void { this.subscriptions.add(this.sessions.activeSessionId$.subscribe(id => { this.sessionId = id; this.reload(); })); }
  ngOnDestroy(): void { this.subscriptions.unsubscribe(); }
  reload(): void {
    if (!this.sessionId) return;
    this.subscriptions.add(this.sessions.getEffectiveProcess(this.sessionId).subscribe({
      next: result => { this.effective = result; this.error = ''; },
      error: error => { this.effective = null; this.error = error?.error?.error || 'Prozess konnte nicht geladen werden'; },
    }));
  }
  clone(): void { if (this.sessionId) this.subscriptions.add(this.sessions.cloneEffectiveProcess(this.sessionId).subscribe({ next: result => this.effective = result, error: error => this.error = error?.error?.error || 'Klonen fehlgeschlagen' })); }
}
