import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { RouterLink, RouterOutlet, Router } from '@angular/router';
import { NotificationsComponent } from './components/notifications.component';
import { ToastComponent } from './components/toast.component';
import { HubApiService } from './services/hub-api.service';
import { AgentDirectoryService } from './services/agent-directory.service';
import { UserAuthService } from './services/user-auth.service';
import { Subscription } from 'rxjs';
import { AsyncPipe } from '@angular/common';
import { AiAssistantComponent } from './components/ai-assistant.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterLink, RouterOutlet, NotificationsComponent, ToastComponent, AsyncPipe, AiAssistantComponent],
  template: `
    <app-notifications />
    <app-toast />
    <header class="app-header">
      <div class="row app-header-top">
        <h1>Ananta - Agent Control</h1>
        @if (auth.user$ | async; as user) {
          <div class="row app-header-user">
            <span class="muted" style="font-size: 14px;">{{ user.sub }} ({{ user.role }})</span>
            <button
              class="secondary mobile-nav-toggle"
              (click)="toggleMobileNav()"
              aria-label="Navigation umschalten">
              {{ mobileNavOpen ? 'Menü schließen' : 'Menü' }}
            </button>
            <button (click)="toggleDarkMode()" class="secondary" style="padding: 4px 8px; font-size: 12px;" title="Dark Mode umschalten">
              {{ isDarkMode ? '☀️' : '🌙' }}
            </button>
            <button (click)="onLogout()" class="secondary" style="padding: 4px 8px; font-size: 12px;">Logout</button>
          </div>
        }
      </div>
      @if (auth.user$ | async; as user) {
        <nav class="row app-nav" [class.nav-open]="mobileNavOpen">
          <span class="nav-group-label">Operate</span>
          <a routerLink="/dashboard" (click)="closeMobileNav()">Dashboard</a>
          <a routerLink="/agents" (click)="closeMobileNav()">Agents</a>
          <a routerLink="/board" (click)="closeMobileNav()">Board</a>
          <a routerLink="/operations" (click)="closeMobileNav()">Operations</a>
          <a routerLink="/archived" (click)="closeMobileNav()">Archive</a>
          <a routerLink="/graph" (click)="closeMobileNav()">Graph</a>
          <span class="nav-group-label">Automate</span>
          <a routerLink="/auto-planner" (click)="closeMobileNav()">Auto-Planner</a>
          <a routerLink="/webhooks" (click)="closeMobileNav()">Webhooks</a>
          <span class="nav-group-label">Configure</span>
          <a routerLink="/templates" (click)="closeMobileNav()">Templates</a>
          <a routerLink="/teams" (click)="closeMobileNav()">Teams</a>
          @if (user.role === 'admin') {
            <a routerLink="/audit-log" (click)="closeMobileNav()">Audit Logs</a>
          }
          <a routerLink="/settings" (click)="closeMobileNav()">Settings</a>
        </nav>
      }
    </header>
    <div class="route-context muted">Bereich: {{ currentArea() }} | Route: {{ routeUrl }}</div>
    <main>
      <router-outlet />
    </main>
    @if (auth.user$ | async) {
      <app-ai-assistant />
    }
  `,
  styles: [`
    .app-header-top {
      justify-content: space-between;
      align-items: center;
      width: 100%;
    }
    .app-header-user {
      gap: 12px;
      align-items: center;
    }
    .mobile-nav-toggle {
      display: none;
    }
    .app-nav {
      gap: 10px;
    }
    .nav-group-label {
      font-size: 11px;
      opacity: 0.8;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .route-context {
      padding: 6px 16px;
      border-bottom: 1px solid var(--border);
      font-size: 12px;
    }
    @media (max-width: 900px) {
      .app-header h1 {
        font-size: 17px;
      }
      .mobile-nav-toggle {
        display: inline-block;
      }
      .app-nav {
        display: none;
        width: 100%;
        flex-direction: column;
        align-items: stretch;
        gap: 4px;
      }
      .app-nav.nav-open {
        display: flex;
      }
      .app-nav a {
        border: 1px solid var(--border);
        border-radius: 6px;
        padding: 8px 10px;
        background: var(--card-bg);
      }
    }
  `]
})
export class AppComponent implements OnInit, OnDestroy {
  private hubApi = inject(HubApiService);
  private dir = inject(AgentDirectoryService);
  auth = inject(UserAuthService);
  private router = inject(Router);
  mobileNavOpen = false;
  isDarkMode = false;

  private eventSub?: Subscription;
  private authSub?: Subscription;

  ngOnInit() {
    this.isDarkMode = this.applyTheme();
    this.authSub = this.auth.token$.subscribe((token) => {
      if (token) {
        if (!this.eventSub) this.startEventStream();
      } else if (this.eventSub) {
        this.eventSub.unsubscribe();
        this.eventSub = undefined;
      }
    });
  }

  ngOnDestroy() {
    this.authSub?.unsubscribe();
    this.eventSub?.unsubscribe();
  }

  private applyTheme(): boolean {
    let isDark = localStorage.getItem('ananta.dark-mode');

    // Respect system preference if no user preference is set
    if (isDark === null) {
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      isDark = prefersDark ? 'true' : 'false';
      localStorage.setItem('ananta.dark-mode', isDark);
    }

    const darkMode = isDark === 'true';
    if (darkMode) {
      document.body.classList.add('dark-mode');
    } else {
      document.body.classList.remove('dark-mode');
    }
    return darkMode;
  }

  toggleDarkMode() {
    this.isDarkMode = !this.isDarkMode;
    localStorage.setItem('ananta.dark-mode', String(this.isDarkMode));
    if (this.isDarkMode) {
      document.body.classList.add('dark-mode');
    } else {
      document.body.classList.remove('dark-mode');
    }
  }

  onLogout() {
    this.auth.logout();
    this.mobileNavOpen = false;
    this.router.navigate(['/login']);
  }

  toggleMobileNav() {
    this.mobileNavOpen = !this.mobileNavOpen;
  }

  closeMobileNav() {
    this.mobileNavOpen = false;
  }

  currentArea(): string {
    const url = this.router.url || '';
    if (url.startsWith('/settings') || url.startsWith('/templates') || url.startsWith('/teams')) return 'Configure';
    if (url.startsWith('/auto-planner') || url.startsWith('/webhooks')) return 'Automate';
    if (url.startsWith('/dashboard') || url.startsWith('/agents') || url.startsWith('/board') || url.startsWith('/graph') || url.startsWith('/archived') || url.startsWith('/operations')) return 'Operate';
    return 'General';
  }

  get routeUrl(): string {
    return this.router.url || '/';
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
