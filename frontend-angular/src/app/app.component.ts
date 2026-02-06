import { Component, OnInit, OnDestroy } from '@angular/core';
import { RouterLink, RouterOutlet, Router } from '@angular/router';
import { NotificationsComponent } from './components/notifications.component';
import { HubApiService } from './services/hub-api.service';
import { AgentDirectoryService } from './services/agent-directory.service';
import { UserAuthService } from './services/user-auth.service';
import { Subscription } from 'rxjs';
import { AsyncPipe, NgIf } from '@angular/common';
import { AiAssistantComponent } from './components/ai-assistant.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterLink, RouterOutlet, NotificationsComponent, AsyncPipe, NgIf, AiAssistantComponent],
  template: `
    <app-notifications />
    <header>
      <div class="row" style="justify-content: space-between; align-items: center; width: 100%;">
        <h1>Ananta – Agent Control</h1>
        <div *ngIf="auth.user$ | async as user" class="row" style="gap: 12px; align-items: center;">
          <span class="muted" style="font-size: 14px;">{{user.sub}} ({{user.role}})</span>
          <button (click)="onLogout()" class="secondary" style="padding: 4px 8px; font-size: 12px;">Logout</button>
        </div>
      </div>
      <nav class="row" *ngIf="auth.user$ | async as user">
        <a routerLink="/dashboard">Dashboard</a>
        <a routerLink="/agents">Agents</a>
        <a routerLink="/board">Board</a>
        <a routerLink="/graph">Graph</a>
        <a routerLink="/templates">Templates</a>
        <a routerLink="/teams">Teams</a>
        <a *ngIf="user.role === 'admin'" routerLink="/audit-log">Audit Logs</a>
        <a routerLink="/settings">Settings</a>
      </nav>
    </header>
    <main>
      <router-outlet />
    </main>
    <app-ai-assistant *ngIf="auth.user$ | async" />
  `
})
export class AppComponent implements OnInit, OnDestroy {
  private eventSub?: Subscription;

  constructor(
    private hubApi: HubApiService,
    private dir: AgentDirectoryService,
    public auth: UserAuthService,
    private router: Router
  ) {}

  ngOnInit() {
    this.applyTheme();
    this.startEventStream();
  }

  ngOnDestroy() {
    this.eventSub?.unsubscribe();
  }

  private applyTheme() {
    const isDark = localStorage.getItem('ananta.dark-mode') === 'true';
    if (isDark) {
      document.body.classList.add('dark-mode');
    } else {
      document.body.classList.remove('dark-mode');
    }
  }

  onLogout() {
    this.auth.logout();
    this.router.navigate(['/login']);
  }

  private startEventStream() {
    const hub = this.dir.list().find(a => a.role === 'hub');
    if (!hub) return;

    this.eventSub = this.hubApi.streamSystemEvents(hub.url).subscribe({
      next: event => {
        if (event.type === 'token_rotated') {
          console.log('Token rotated event received', event.data);
          const agents = this.dir.list();
          // Suche den Agenten, dessen Token rotiert wurde
          // Falls wir mehrere Agenten haben, müsste das Event die Agent-Info enthalten.
          // Aktuell gehen wir davon aus, dass es der Hub selbst ist oder wir suchen nach URL.
          const agent = agents.find(a => a.url === hub.url); 
          if (agent && event.data.new_token) {
            agent.token = event.data.new_token;
            this.dir.upsert(agent);
          }
        }
      },
      error: err => {
        console.error('System events stream error', err);
      }
    });
  }
}
