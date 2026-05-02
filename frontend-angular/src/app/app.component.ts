import { Component, HostListener, OnInit, OnDestroy, inject } from '@angular/core';
import { RouterLink, RouterOutlet, Router } from '@angular/router';
import { Capacitor } from '@capacitor/core';
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
import { PythonRuntimeService } from './services/python-runtime.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterLink, RouterOutlet, NotificationsComponent, ToastComponent, AsyncPipe, AiAssistantComponent, BreadcrumbComponent],
  template: `
    <a class="skip-link" href="#main-content">Zum Inhalt springen</a>
    <app-notifications />
    <app-toast />
    <header class="app-header">
      <div class="row app-header-top">
        <div class="row app-header-title">
          @if (isAndroidNative && (auth.user$ | async)) {
            <button
              class="secondary android-drawer-toggle"
              (click)="shell.toggleMobileNav()"
              [attr.aria-expanded]="shell.mobileNavOpen()"
              aria-controls="primary-navigation"
              aria-label="Menue oeffnen">
              ☰
            </button>
          }
          <h1>Ananta - Agent Control</h1>
        </div>
        @if (auth.user$ | async; as user) {
          <div class="row app-header-user">
            <span class="muted" style="font-size: 14px;">{{ user.sub }} ({{ user.role }})</span>
            @if (!isAndroidNative) {
              <button class="secondary mobile-nav-toggle" (click)="shell.toggleMobileNav()" [attr.aria-expanded]="shell.mobileNavOpen()" aria-controls="primary-navigation" aria-label="Navigation umschalten">
                {{ shell.mobileNavOpen() ? 'Menue schliessen' : 'Menue' }}
              </button>
            }
            <button (click)="toggleDarkMode()" class="secondary" style="padding: 4px 8px; font-size: 12px;" title="Darstellung umschalten">
              {{ shell.darkMode() ? 'Hell' : 'Dunkel' }}
            </button>
            <button (click)="toggleMode()" class="secondary" style="padding: 4px 8px; font-size: 12px;" title="Navigationstiefe umschalten">
              {{ shell.mode() === 'simple' ? 'Experte' : 'Einfach' }}
            </button>
            <button (click)="onLogout()" class="secondary" style="padding: 4px 8px; font-size: 12px;" aria-label="Logout">Abmelden</button>
          </div>
        }
      </div>
      @if (auth.user$ | async; as user) {
        @if (!isAndroidNative) {
          <nav
            id="primary-navigation"
            class="row app-nav"
            [class.nav-open]="shell.mobileNavOpen()"
            aria-label="Hauptnavigation">
            @for (group of navGroups(user.role); track group.label) {
              <span class="nav-group-label">{{ group.label }}</span>
              @for (item of group.items; track item.path) {
                <a [routerLink]="item.path" (click)="closeMobileNav()">{{ item.label }}</a>
                @if (shell.mode() === 'advanced' && item.expertOnly) {
                  <span class="nav-expert-label">Experte</span>
                }
              }
            }
          </nav>
        }
      }
    </header>
    @if (isAndroidNative) {
      <nav
        id="primary-navigation"
        class="android-fullscreen-menu"
        [class.open]="shell.mobileNavOpen()"
        aria-label="Hauptnavigation">
        @for (group of navGroups((auth.user$ | async)?.role); track group.label) {
          <span class="nav-group-label">{{ group.label }}</span>
          @for (item of group.items; track item.path) {
            <a [routerLink]="item.path" (click)="closeMobileNav()">{{ item.label }}</a>
            @if (shell.mode() === 'advanced' && item.expertOnly) {
              <span class="nav-expert-label">Experte</span>
            }
          }
        }
      </nav>
      @if (shell.mobileNavOpen()) {
        <div class="mobile-nav-backdrop open" (click)="closeMobileNav()" aria-hidden="true"></div>
      }
    }
    @if (isAndroidNative) {
      <button
        type="button"
        class="android-edge-toggle"
        (click)="shell.toggleMobileNav()"
        [attr.aria-expanded]="shell.mobileNavOpen()"
        aria-controls="primary-navigation"
        [attr.aria-label]="shell.mobileNavOpen() ? 'Menue schliessen' : 'Menue oeffnen'">
        {{ shell.mobileNavOpen() ? '×' : '☰' }}
      </button>
    }
    @if (auth.user$ | async) {
      <app-breadcrumb />
    }
    <div class="route-context muted">
      Bereich: {{ shell.area() }} | Modus: {{ shell.mode() === 'simple' ? 'Einfach' : 'Experte' }} | Route: {{ shell.routeUrl() }}
      @if (mobile.isNative) { | Mobile: native }
      @if ((mobile.online$ | async) === false) { | Offline-Modus aktiv }
    </div>
    <main id="main-content" tabindex="-1">
      <router-outlet />
    </main>
    @if (auth.user$ | async) {
      <app-ai-assistant data-testid="assistant-feature-root" />
    }
  `,
  styles: [`
    .skip-link {
      position: fixed;
      left: 12px;
      top: 8px;
      transform: translateY(-160%);
      z-index: 1000;
      background: var(--fg);
      color: var(--bg);
      padding: 8px 10px;
      border-radius: 6px;
    }
    .skip-link:focus {
      transform: translateY(0);
    }
    .app-header-top {
      justify-content: space-between;
      align-items: center;
      width: 100%;
    }
    .app-header-title {
      align-items: center;
      gap: 8px;
    }
    .android-drawer-toggle {
      min-width: 30px;
      height: 30px;
      padding: 0;
      line-height: 1;
      border-radius: 6px;
      font-size: 14px;
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
    .android-fullscreen-menu {
      display: flex;
      position: fixed;
      inset: 0;
      transform: translateX(-108%);
      transition: transform 180ms ease;
      z-index: 20020;
      background: var(--card-bg);
      padding: 64px 14px 18px;
      overflow-y: auto;
      flex-direction: column;
      align-items: stretch;
      gap: 8px;
      pointer-events: none;
    }
    .android-fullscreen-menu.open {
      transform: translateX(0);
      pointer-events: auto;
    }
    .android-fullscreen-menu a {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 10px 12px;
      background: var(--card-bg);
    }
    .mobile-nav-backdrop {
      display: none;
    }
    .android-edge-toggle {
      position: fixed;
      left: 0;
      top: 50%;
      transform: translateY(-50%);
      z-index: 20030;
      min-width: 28px;
      height: 52px;
      border-top-right-radius: 8px;
      border-bottom-right-radius: 8px;
      border-top-left-radius: 0;
      border-bottom-left-radius: 0;
      border: 1px solid var(--border);
      border-left: none;
      background: var(--accent);
      color: #fff;
      font-size: 14px;
      font-weight: 700;
      line-height: 1;
      padding: 0 8px;
    }
    .nav-group-label {
      font-size: 11px;
      opacity: 0.8;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .nav-expert-label {
      font-size: 10px;
      color: var(--muted);
      margin-left: -8px;
      margin-right: 4px;
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
      .mobile-nav-backdrop.open {
        display: block;
        position: fixed;
        inset: 0;
        z-index: 20010;
        background: rgba(2, 6, 23, 0.35);
      }
      main {
        padding-bottom: 84px;
      }
    }
    @media (min-width: 901px) {
      .android-edge-toggle {
        display: none;
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
  private pythonRuntime = inject(PythonRuntimeService);

  private authSub?: Subscription;
  private touchStartX = 0;
  private touchStartY = 0;
  private trackingOpenSwipe = false;
  private trackingCloseSwipe = false;
  private readonly swipeEdgeWidthPx = 28;
  private readonly swipeTriggerPx = 72;
  private readonly verticalTolerancePx = 44;

  get isAndroidNative(): boolean {
    return this.mobile.isNative && Capacitor.getPlatform() === 'android';
  }

  ngOnInit() {
    this.shell.init();
    void this.bootstrapEmbeddedRuntime();
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

  toggleMode() {
    this.shell.toggleMode();
    this.shell.closeMobileNav();
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

  private async bootstrapEmbeddedRuntime(): Promise<void> {
    if (!this.mobile.isNative) return;
    this.ensureLocalMobileAgentDirectory();
    try {
      await this.pythonRuntime.ensureEmbeddedControlPlane();
    } catch (error) {
      // Keep startup resilient. Users can still manage runtime manually via /python-runtime.
      console.error('Embedded runtime startup failed', error);
    }
  }

  private ensureLocalMobileAgentDirectory(): void {
    const current = this.dir.list();
    const hub = current.find((a) => a.name === 'hub') ?? current.find((a) => a.role === 'hub');
    const worker = current.find((a) => a.name === 'worker');
    const legacyAlphaWorker = current.find((a) => a.name === 'alpha' && /^http:\/\/127\.0\.0\.1:500[01]\/?$/.test((a.url || '').trim()));
    const workerUrl = 'http://127.0.0.1:5000';

    if (!hub) {
      this.dir.upsert({ name: 'hub', role: 'hub', url: 'http://127.0.0.1:5000', token: '' });
    } else if ((hub.url || '').trim() !== 'http://127.0.0.1:5000') {
      this.dir.upsert({ ...hub, role: 'hub', url: 'http://127.0.0.1:5000' });
    }

    if (!worker) {
      if (legacyAlphaWorker) {
        this.dir.upsert({ ...legacyAlphaWorker, name: 'worker', role: 'worker', url: workerUrl });
        this.dir.remove('alpha');
      } else {
        this.dir.upsert({ name: 'worker', role: 'worker', url: workerUrl, token: '' });
      }
    } else if ((worker.url || '').trim() !== workerUrl) {
      this.dir.upsert({ ...worker, role: 'worker', url: workerUrl });
      if (legacyAlphaWorker) this.dir.remove('alpha');
    } else if (legacyAlphaWorker) {
      this.dir.remove('alpha');
    }
  }

  @HostListener('document:touchstart', ['$event'])
  onGlobalTouchStart(event: TouchEvent): void {
    if (!this.shouldHandleMobileDrawerSwipe() || event.touches.length !== 1) {
      this.resetSwipeTracking();
      return;
    }
    const touch = event.touches[0];
    this.touchStartX = touch.clientX;
    this.touchStartY = touch.clientY;
    const navOpen = this.shell.mobileNavOpen();
    this.trackingOpenSwipe = !navOpen && touch.clientX <= this.swipeEdgeWidthPx;
    this.trackingCloseSwipe = navOpen;
  }

  @HostListener('document:touchmove', ['$event'])
  onGlobalTouchMove(event: TouchEvent): void {
    if (!this.shouldHandleMobileDrawerSwipe() || event.touches.length !== 1) return;
    if (!this.trackingOpenSwipe && !this.trackingCloseSwipe) return;

    const touch = event.touches[0];
    const dx = touch.clientX - this.touchStartX;
    const dy = Math.abs(touch.clientY - this.touchStartY);
    if (dy > this.verticalTolerancePx) {
      this.resetSwipeTracking();
      return;
    }

    if (this.trackingOpenSwipe && dx >= this.swipeTriggerPx) {
      this.shell.openMobileNav();
      this.resetSwipeTracking();
      return;
    }
    if (this.trackingCloseSwipe && dx <= -this.swipeTriggerPx) {
      this.shell.closeMobileNav();
      this.resetSwipeTracking();
    }
  }

  @HostListener('document:touchend')
  onGlobalTouchEnd(): void {
    this.resetSwipeTracking();
  }

  @HostListener('document:touchcancel')
  onGlobalTouchCancel(): void {
    this.resetSwipeTracking();
  }

  private shouldHandleMobileDrawerSwipe(): boolean {
    return this.isAndroidNative && this.auth.isLoggedIn() && window.innerWidth <= 900;
  }

  private resetSwipeTracking(): void {
    this.trackingOpenSwipe = false;
    this.trackingCloseSwipe = false;
  }
}
