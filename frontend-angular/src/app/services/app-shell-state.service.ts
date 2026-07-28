import { Injectable, inject, signal } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs';

import { AppNavGroup, AppRouteArea, AppShellMode, buildNavGroups } from '../models/route-metadata';
import { MobileRuntimeService } from './mobile-runtime.service';
import { DashboardFeatureFlagStore } from '../features/dashboard-foundation/dashboard-feature-flags';
import { CaseFlowScenarioRegistryService } from '../features/caseflow/scenario/caseflow-scenario-registry.service';
import { withCaseFlowScenarios } from '../features/caseflow/scenario/caseflow-navigation';

@Injectable({ providedIn: 'root' })
export class AppShellStateService {
  private router = inject(Router);
  private activatedRoute = inject(ActivatedRoute);
  private mobile = inject(MobileRuntimeService);
  private dashboardFeatures = inject(DashboardFeatureFlagStore);
  private caseFlowScenarios = inject(CaseFlowScenarioRegistryService);

  readonly mobileNavOpen = signal(false);
  readonly darkMode = signal(false);
  readonly mode = signal<AppShellMode>('simple');
  readonly area = signal<AppRouteArea>('General');
  readonly routeUrl = signal('/');

  init(): void {
    this.dashboardFeatures.ensureLoaded().subscribe();
    this.caseFlowScenarios.listScenarios().subscribe();
    this.mobile.init();
    this.darkMode.set(this.applyStoredTheme());
    this.mode.set(this.applyStoredMode());
    this.ensureDistroDefault();
    this.updateRouteContext();
    this.router.events.pipe(filter(event => event instanceof NavigationEnd)).subscribe(() => this.updateRouteContext());
  }

  navGroups(role?: string | null): AppNavGroup[] {
    const groups = buildNavGroups(role, this.mode()).map(group => ({
      ...group,
      items: group.items.filter(item =>
        item.path !== '/board' || this.dashboardFeatures.angularKanban()
      ),
    })).filter(group => group.items.length > 0);
    return withCaseFlowScenarios(groups, this.caseFlowScenarios.scenarios());
  }

  toggleMobileNav(): void {
    this.mobileNavOpen.update(open => !open);
  }

  openMobileNav(): void {
    this.mobileNavOpen.set(true);
  }

  closeMobileNav(): void {
    this.mobileNavOpen.set(false);
  }

  toggleDarkMode(): void {
    const next = !this.darkMode();
    localStorage.setItem('ananta.dark-mode', String(next));
    this.applyThemeClass(next);
    this.darkMode.set(next);
  }

  toggleMode(): void {
    const next: AppShellMode = this.mode() === 'simple' ? 'advanced' : 'simple';
    localStorage.setItem('ananta.shell.mode', next);
    this.mode.set(next);
  }

  private applyStoredMode(): AppShellMode {
    const stored = localStorage.getItem('ananta.shell.mode');
    if (stored === 'advanced') return 'advanced';
    if (stored === 'simple') return 'simple';
    // Android native defaults to advanced (all nav items visible)
    if (this.mobile.isNative) {
      localStorage.setItem('ananta.shell.mode', 'advanced');
      return 'advanced';
    }
    return 'simple';
  }

  private applyStoredTheme(): boolean {
    let stored = localStorage.getItem('ananta.dark-mode');
    if (stored === null) {
      stored = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'true' : 'false';
      localStorage.setItem('ananta.dark-mode', stored);
    }
    const enabled = stored === 'true';
    this.applyThemeClass(enabled);
    return enabled;
  }

  private ensureDistroDefault(): void {
    if (!this.mobile.isNative) return;
    const key = 'ananta.mobile.proot.distro';
    if (!localStorage.getItem(key)) {
      localStorage.setItem(key, 'ubuntu');
    }
  }

  private applyThemeClass(enabled: boolean): void {
    document.body.classList.toggle('dark-mode', enabled);
  }

  private updateRouteContext(): void {
    let current = this.activatedRoute.root;
    while (current.firstChild) current = current.firstChild;
    this.area.set((current.snapshot.data['area'] as AppRouteArea | undefined) || 'General');
    this.routeUrl.set(this.router.url || '/');
  }
}
