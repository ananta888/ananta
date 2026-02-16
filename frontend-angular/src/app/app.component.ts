import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { RouterLink, RouterOutlet, Router } from '@angular/router';
import { NotificationsComponent } from './components/notifications.component';
import { HubApiService } from './services/hub-api.service';
import { AgentDirectoryService } from './services/agent-directory.service';
import { UserAuthService } from './services/user-auth.service';
import { Subscription } from 'rxjs';
import { AsyncPipe } from '@angular/common';
import { AiAssistantComponent } from './components/ai-assistant.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterLink, RouterOutlet, NotificationsComponent, AsyncPipe, AiAssistantComponent],
  template: `
    <app-notifications />
    <header>
      <div class="row" style="justify-content: space-between; align-items: center; width: 100%;">
        <h1>Ananta - Agent Control</h1>
        @if (auth.user$ | async; as user) {
          <div class="row" style="gap: 12px; align-items: center;">
            <span class="muted" style="font-size: 14px;">{{ user.sub }} ({{ user.role }})</span>
            <button (click)="onLogout()" class="secondary" style="padding: 4px 8px; font-size: 12px;">Logout</button>
          </div>
        }
      </div>
      @if (auth.user$ | async; as user) {
        <nav class="row">
          <a routerLink="/dashboard">Dashboard</a>
          <a routerLink="/agents">Agents</a>
          <a routerLink="/board">Board</a>
          <a routerLink="/archived">Archiv</a>
          <a routerLink="/graph">Graph</a>
          <a routerLink="/templates">Templates</a>
          <a routerLink="/teams">Teams</a>
          @if (user.role === 'admin') {
            <a routerLink="/audit-log">Audit Logs</a>
          }
          <a routerLink="/settings">Settings</a>
        </nav>
      }
    </header>
    <main>
      <router-outlet />
    </main>
    @if (auth.user$ | async) {
      <app-ai-assistant />
    }
  `
})
export class AppComponent implements OnInit, OnDestroy {
  private hubApi = inject(HubApiService);
  private dir = inject(AgentDirectoryService);
  auth = inject(UserAuthService);
  private router = inject(Router);

  private eventSub?: Subscription;

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