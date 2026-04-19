import { Component, OnInit, OnDestroy, inject } from '@angular/core';
import { RouterLink, RouterOutlet, Router } from '@angular/router';
import { NotificationsComponent } from './components/notifications.component';
import { ToastComponent } from './components/toast.component';
import { AgentDirectoryService } from './services/agent-directory.service';
import { UserAuthService } from './services/user-auth.service';
import { Subscription } from 'rxjs';
import { AsyncPipe } from '@angular/common';
import { AiAssistantComponent } from './components/ai-assistant.component';
import { BreadcrumbComponent } from './components/breadcrumb.component';
import { MobileRuntimeService } from './services/mobile-runtime.service';
import { SystemFacade } from './features/system/system.facade';
import { AppShellStateService } from './services/app-shell-state.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterLink, RouterOutlet, NotificationsComponent, ToastComponent, AsyncPipe, AiAssistantComponent, BreadcrumbComponent],
  template: `
    <app-notifications />
    <app-toast />
    <header class="app-header">
      <div class="row app-header-top">
        <h1>Ananta - Agent Control</h1>
        @if (auth.user$ | async; as user) {
          <div class="row app-header-user">
            <span class="muted" style="font-size: 14px;">{{ user.sub }} ({{ user.role }})</span>
            <button class="secondary mobile-nav-toggle" (click)="shell.toggleMobileNav()" aria-label="Navigation umschalten">
              {{ shell.mobileNavOpen() ? 'Menue schliessen' : 'Menue' }}
            </button>
            <button (click)="toggleDarkMode()" class="secondary" style="padding: 4px 8px; font-size: 12px;" title="Darstellung umschalten">
              {{ shell.darkMode() ? 'Hell' : 'Dunkel' }}
            </button>
            <button (click)="onLogout()" class="secondary" style="padding: 4px 8px; font-size: 12px;" aria-label="Logout">Abmelden</button>
          </div>
        }
      </div>
      @if (auth.user$ | async; as user) {
        <nav class="row app-nav" [class.nav-open]="shell.mobileNavOpen()">
          @for (group of navGroups(user.role); track group.label) {
            <span class="nav-group-label">{{ group.label }}</span>
            @for (item of group.items; track item.path) {
              <a [routerLink]="item.path" (click)="closeMobileNav()">{{ item.label }}</a>
            }
          }
        </nav>
      }
    </header>
    @if (auth.user$ | async) {
      <app-breadcrumb />
    }
    <div class="route-context muted">
      Bereich: {{ shell.area() }} | Route: {{ shell.routeUrl() }}
      @if (mobile.isNative) { | Mobile: native }
      @if ((mobile.online$ | async) === false) { | Offline-Modus aktiv }
    </div>
    <main>
      <router-outlet />
    </main>
    @if (auth.user$ | async) {
      <app-ai-assistant data-testid="assistant-feature-root" />
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
      main {
        padding-bottom: 84px;
      }
    }
  `]
})
export class AppComponent implements OnInit, OnDestroy {
  private dir = inject(AgentDirectoryService);
  auth = inject(UserAuthService);
  private router = inject(Router);
  mobile = inject(MobileRuntimeService);
  private system = inject(SystemFacade);
  shell = inject(AppShellStateService);

  private authSub?: Subscription;

  ngOnInit() {
    this.shell.init();
    this.authSub = this.auth.token$.subscribe((token) => {
      if (token) {
        this.startEventStream();
      } else {
        this.system.disconnectSystemEvents();
      }
    });
  }

  ngOnDestroy() {
    this.authSub?.unsubscribe();
    this.system.disconnectSystemEvents();
  }

  toggleDarkMode() {
    this.shell.toggleDarkMode();
  }

  onLogout() {
    this.auth.logout();
    this.shell.closeMobileNav();
    this.router.navigate(['/login']);
  }

  closeMobileNav() {
    this.shell.closeMobileNav();
  }

  navGroups(role?: string | null) {
    return this.shell.navGroups(role);
  }

  private startEventStream() {
    const hub = this.system.resolveHubAgent();
    if (!hub) return;
    this.system.ensureSystemEvents(hub.url);
  }
}
