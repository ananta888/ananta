import { Component, HostListener, OnInit, OnDestroy, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet, Router } from '@angular/router';
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
import { WindowBridgeService } from './services/window-bridge.service';
import { SnakeOverlayService } from './services/snake-overlay.service';
import { SnakeOverlayComponent } from './components/snake-overlay.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterLink, RouterLinkActive, RouterOutlet, NotificationsComponent, ToastComponent, AsyncPipe, AiAssistantComponent, BreadcrumbComponent, SnakeOverlayComponent],
  template: `
    <a class="skip-link" href="#main-content">Zum Inhalt springen</a>
    <app-notifications />
    <app-toast />
    <header class="app-header">
      <div class="app-hrow-top">
        <!-- Links: Brand + Breadcrumbs -->
        <div class="app-hleft">
          @if (isAndroidNative && headerUser) {
            <button class="secondary android-drawer-toggle"
              (click)="shell.toggleMobileNav()"
              [attr.aria-expanded]="shell.mobileNavOpen()"
              aria-controls="primary-navigation"
              aria-label="Menue oeffnen">☰</button>
          }
          <img src="/assets/ananta.svg" alt="Ananta" class="app-logo" />
          @if (headerUser) { <app-breadcrumb /> }
        </div>
        <!-- Mitte: Nav-Gruppen -->
        @if (headerUser) {
          @if (!isAndroidNative) {
            <nav
              id="primary-navigation"
              class="app-nav"
              [class.nav-open]="shell.mobileNavOpen()"
              aria-label="Hauptnavigation"
              (click)="onNavClick($event)">
              @for (group of navGroups(headerUser.role); track group.label) {
                <details class="nav-menu-group">
                  <summary>
                    <span>{{ group.label }}</span>
                    <span class="nav-count">{{ group.items.length }}</span>
                  </summary>
                  <div class="nav-menu-panel">
                    @for (item of group.items; track item.path) {
                      <a
                        [routerLink]="item.path"
                        routerLinkActive="active"
                        [routerLinkActiveOptions]="{ exact: true }"
                        (click)="closeMobileNav()">
                        <span>{{ item.label }}</span>
                        @if (shell.mode() === 'advanced' && item.expertOnly) {
                          <span class="nav-expert-label">Experte</span>
                        }
                      </a>
                    }
                  </div>
                </details>
              }
            </nav>
          }
        }
        <!-- Rechts: User-Controls -->
        @if (headerUser) {
          <div class="app-hright">
            @if (!isAndroidNative) {
              <button class="secondary app-hbtn mobile-nav-toggle"
                (click)="shell.toggleMobileNav()"
                [attr.aria-expanded]="shell.mobileNavOpen()"
                aria-controls="primary-navigation"
                aria-label="Navigation">☰</button>
            }
            <button (click)="toggleDarkMode()" class="secondary app-hbtn" title="Darstellung">
              {{ shell.darkMode() ? '☀' : '🌙' }}
            </button>
            <button (click)="toggleMode()" class="secondary app-hbtn" title="Modus">
              {{ shell.mode() === 'simple' ? 'Experte' : 'Einfach' }}
            </button>
            <button (click)="snakeOverlay.toggle()" class="secondary app-hbtn snake-toggle"
              [class.snake-on]="snakeOverlay.visible$ | async" title="AI-Snake">🐍</button>
            <span class="app-header-user">{{ headerUser.sub }} ({{ headerUser.role }})</span>
            <button (click)="onLogout()" class="secondary app-hbtn" aria-label="Logout">Abmelden</button>
          </div>
        }
      </div>
    </header>
    @if (isAndroidNative) {
      <nav
        id="primary-navigation"
        class="android-fullscreen-menu"
        [class.open]="shell.mobileNavOpen()"
        aria-label="Hauptnavigation">
        @for (group of navGroups(headerUser?.role); track group.label) {
          <details class="nav-menu-group" open>
            <summary>
              <span>{{ group.label }}</span>
              <span class="nav-count">{{ group.items.length }}</span>
            </summary>
            <div class="nav-menu-panel">
              @for (item of group.items; track item.path) {
                <a
                  [routerLink]="item.path"
                  routerLinkActive="active"
                  [routerLinkActiveOptions]="{ exact: true }"
                  (click)="closeMobileNav()">
                  <span>{{ item.label }}</span>
                  @if (shell.mode() === 'advanced' && item.expertOnly) {
                    <span class="nav-expert-label">Experte</span>
                  }
                </a>
              }
            </div>
          </details>
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
    <main id="main-content" [class.main-flush]="isFullscreenRoute" tabindex="-1">
      <router-outlet />
    </main>
    @if (headerUser) {
      <app-ai-assistant data-testid="assistant-feature-root" />
    }
    @if ((snakeOverlay.visible$ | async) && headerUser) {
      <app-snake-overlay />
    }
  `,
  styles: [`
    .skip-link {
      position: fixed; left: 12px; top: 8px; transform: translateY(-160%);
      z-index: 1000; background: var(--fg); color: var(--bg);
      padding: 8px 10px; border-radius: 6px;
    }
    .skip-link:focus { transform: translateY(0); }

    /* ── Header ── */
    .app-header {
      display: flex; flex-direction: column; gap: 0;
      border-bottom: 1px solid var(--border);
      position: sticky; top: 0; background: var(--header-bg);
      backdrop-filter: blur(6px); z-index: 200;
    }
    .app-hrow-top {
      display: flex; align-items: center; gap: 8px;
      padding: 4px 12px; min-height: 38px; flex-shrink: 0;
    }
    .app-hleft {
      flex: 1; display: flex; align-items: center; gap: 8px; min-width: 0;
    }
    .app-hright {
      flex: 1; display: flex; align-items: center; justify-content: flex-end; gap: 5px; flex-shrink: 0;
    }
    .app-logo { height: 28px; width: 28px; object-fit: contain; flex-shrink: 0; border-radius: 4px; }
    .app-hspace { flex: 1; }
    .app-hbtn { padding: 3px 7px !important; font-size: 11px !important; white-space: nowrap; }
    .app-header-user { font-size: 11px; color: var(--muted); white-space: nowrap; }
    .mobile-nav-toggle { display: none; }
    .android-drawer-toggle { min-width: 28px; height: 28px; padding: 0; line-height: 1; border-radius: 6px; font-size: 14px; }

    /* ── Nav groups (in der Mitte der Top-Zeile) ── */
    .app-nav {
      display: flex; align-items: center; gap: 5px; flex-wrap: nowrap;
      flex-shrink: 0;
    }
    .nav-menu-group { position: relative; }
    .nav-menu-group summary {
      display: flex; align-items: center; gap: 5px; min-height: 26px;
      padding: 3px 8px; border: 1px solid var(--border); border-radius: 6px;
      background: var(--card-bg); color: var(--fg); cursor: pointer;
      font-size: 12px; font-weight: 600; list-style: none; user-select: none; white-space: nowrap;
    }
    .nav-menu-group summary::-webkit-details-marker { display: none; }
    .nav-menu-group summary::after { content: '▾'; color: var(--muted); font-size: 10px; }
    .nav-menu-group[open] summary { border-color: var(--accent); }
    .nav-count {
      display: inline-flex; align-items: center; justify-content: center;
      min-width: 16px; height: 16px; border-radius: 999px;
      background: color-mix(in srgb, var(--accent) 16%, transparent);
      color: var(--fg); font-size: 9px; font-weight: 700;
    }
    .nav-menu-panel {
      display: flex; flex-direction: column; gap: 2px;
      min-width: 200px; padding: 5px; border: 1px solid var(--border);
      border-radius: 7px; background: var(--card-bg); box-shadow: 0 12px 28px rgba(0,0,0,0.2);
      position: absolute; top: calc(100% + 4px); left: 0; z-index: 100;
    }
    .nav-menu-panel a {
      display: flex; align-items: center; justify-content: space-between; gap: 10px;
      padding: 6px 8px; border-radius: 5px; color: var(--fg);
      text-decoration: none; font-size: 12px;
    }
    .nav-menu-panel a:hover, .nav-menu-panel a.active { background: color-mix(in srgb, var(--accent) 14%, transparent); }
    .nav-expert-label { flex: 0 0 auto; font-size: 9px; color: var(--muted); }

    /* ── Android nav (unchanged) ── */
    .android-fullscreen-menu {
      display: flex; position: fixed; inset: 0; transform: translateX(-108%);
      transition: transform 180ms ease; z-index: 20020; background: var(--card-bg);
      padding: 64px 14px 18px; overflow-y: auto; flex-direction: column;
      align-items: stretch; gap: 10px; pointer-events: none;
    }
    .android-fullscreen-menu.open { transform: translateX(0); pointer-events: auto; }
    .android-fullscreen-menu .nav-menu-group { position: static; }
    .android-fullscreen-menu .nav-menu-panel { margin-top: 6px; box-shadow: none; min-width: 0; }
    .nav-menu-group { position: relative; }
    .nav-menu-group summary {
      display: flex; align-items: center; gap: 7px; min-height: 32px;
      padding: 5px 10px; border: 1px solid var(--border); border-radius: 8px;
      background: var(--card-bg); color: var(--fg); cursor: pointer;
      font-size: 13px; font-weight: 600; list-style: none; user-select: none;
    }
    .nav-menu-group summary::-webkit-details-marker { display: none; }
    .nav-menu-group summary::after { content: '▾'; color: var(--muted); font-size: 11px; }
    .nav-count {
      display: inline-flex; align-items: center; justify-content: center;
      min-width: 18px; height: 18px; border-radius: 999px;
      background: color-mix(in srgb, var(--accent) 16%, transparent);
      color: var(--fg); font-size: 10px; font-weight: 700;
    }
    .nav-menu-panel {
      display: flex; flex-direction: column; gap: 3px;
      min-width: 220px; padding: 7px; border: 1px solid var(--border);
      border-radius: 8px; background: var(--card-bg); box-shadow: 0 14px 32px rgba(0,0,0,0.22);
    }
    .nav-menu-panel a {
      display: flex; align-items: center; gap: 12px; padding: 8px 9px;
      border-radius: 6px; color: var(--fg); text-decoration: none; font-size: 13px;
    }
    .nav-menu-panel a:hover, .nav-menu-panel a.active { background: color-mix(in srgb, var(--accent) 14%, transparent); }
    .mobile-nav-backdrop { display: none; }
    .android-edge-toggle {
      position: fixed; left: 0; top: 50%; transform: translateY(-50%);
      z-index: 20030; min-width: 28px; height: 52px;
      border-top-right-radius: 8px; border-bottom-right-radius: 8px;
      border-top-left-radius: 0; border-bottom-left-radius: 0;
      border: 1px solid var(--border); border-left: none;
      background: var(--accent); color: #fff; font-size: 14px; font-weight: 700; line-height: 1; padding: 0 8px;
    }
    .snake-toggle { transition: box-shadow 0.2s; }
    .snake-toggle.snake-on { box-shadow: 0 0 6px 1px #3affaa44; outline: 1px solid #3affaa88; }

    @media (max-width: 900px) {
      .mobile-nav-toggle { display: inline-flex !important; }
      .app-nav { display: none; }
      .app-nav.nav-open {
        display: flex; flex-direction: column; align-items: stretch;
        position: absolute; top: 100%; left: 0; right: 0;
        background: var(--header-bg); border-bottom: 1px solid var(--border);
        padding: 6px 12px; gap: 4px; z-index: 300;
      }
      .app-nav.nav-open .nav-menu-group { position: static; }
      .app-nav.nav-open .nav-menu-panel { position: static; margin-top: 4px; box-shadow: none; min-width: 0; }
      .mobile-nav-backdrop.open { display: block; position: fixed; inset: 0; z-index: 20010; background: rgba(2,6,23,0.35); }
      main { padding-bottom: 84px; }
    }
    @media (min-width: 901px) { .android-edge-toggle { display: none; } }
  `]
})
export class AppComponent implements OnInit, OnDestroy {
  private dir = inject(AgentDirectoryService);
  auth = inject(UserAuthService);
  private router = inject(Router);
  mobile = inject(MobileRuntimeService);
  private system = inject(SystemFacade);
  shell = inject(AppShellStateService);
  readonly snakeOverlay = inject(SnakeOverlayService);
  private pythonRuntime = inject(PythonRuntimeService);
  readonly bridge = inject(WindowBridgeService);

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
    this.bridge.initFromUrlParams();
    this._applyTuiAuthIfPresent();
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

  get isFullscreenRoute(): boolean {
    return this.shell.routeUrl().startsWith('/codehug');
  }

  onNavClick(event: Event) {
    const summary = (event.target as Element).closest('summary');
    if (!summary) return;
    const clicked = summary.closest('details');
    document.querySelectorAll('.app-nav details').forEach(el => {
      if (el !== clicked) (el as HTMLDetailsElement).open = false;
    });
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: Event) {
    if (!(event.target as Element).closest('.app-nav')) {
      document.querySelectorAll('.app-nav details[open]').forEach(el => {
        (el as HTMLDetailsElement).open = false;
      });
    }
  }

  private _applyTuiAuthIfPresent(): void {
    const ctx = this.bridge.tuiAuthContext;
    if (!ctx.hubToken && !ctx.oidcToken) return;
    if (ctx.hubUrl) {
      this.dir.upsert({ name: 'hub', role: 'hub', url: ctx.hubUrl, token: '' });
    }
    if (ctx.hubToken) {
      // Always apply TUI token — replaces expired tokens too
      this.auth.setTokens(ctx.hubToken);
    }
    if (ctx.oidcToken) {
      this.auth.setOidcAccessToken(ctx.oidcToken);
    }
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

  get headerUser(): { sub: string; role: string } | null {
    const user = this.auth.userPayload;
    if (user && typeof user === 'object') {
      const sub = String((user as any).sub || (user as any).username || (user as any).preferred_username || '').trim() || 'angemeldet';
      const role = String((user as any).role || '').trim() || 'user';
      return { sub, role };
    }
    if (this.auth.isLoggedIn()) {
      return { sub: 'angemeldet', role: 'user' };
    }
    return null;
  }
}
