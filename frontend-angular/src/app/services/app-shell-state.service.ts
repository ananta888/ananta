import { Injectable, inject, signal } from '@angular/core';
import { ActivatedRoute, NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs';

import { AppNavGroup, AppRouteArea, AppShellMode, buildNavGroups } from '../models/route-metadata';
import { MobileRuntimeService } from './mobile-runtime.service';

@Injectable({ providedIn: 'root' })
export class AppShellStateService {
  private router = inject(Router);
  private activatedRoute = inject(ActivatedRoute);
  private mobile = inject(MobileRuntimeService);

  readonly mobileNavOpen = signal(false);
  readonly darkMode = signal(false);
  readonly mode = signal<AppShellMode>('simple');
  readonly area = signal<AppRouteArea>('General');
  readonly routeUrl = signal('/');

  init(): void {
    this.mobile.init();
    this.darkMode.set(this.applyStoredTheme());
    this.mode.set(this.applyStoredMode());
    this.updateRouteContext();
    this.router.events.pipe(filter(event => event instanceof NavigationEnd)).subscribe(() => this.updateRouteContext());
  }

  navGroups(role?: string | null): AppNavGroup[] {
    return buildNavGroups(role, this.mode());
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
    return stored === 'advanced' ? 'advanced' : 'simple';
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
