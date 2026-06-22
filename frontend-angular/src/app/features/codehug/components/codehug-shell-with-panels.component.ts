import { Component, ChangeDetectionStrategy, inject, signal } from '@angular/core';
import { Router, NavigationEnd } from '@angular/router';
import { filter } from 'rxjs';
import { CodeHugShellComponent } from './codehug-shell.component';
import { CodeHugLeftPanelComponent } from './codehug-left-panel.component';
import { CodeHugRightPanelComponent } from './codehug-right-panel.component';

@Component({
  selector: 'ch-shell-with-panels',
  standalone: true,
  imports: [CodeHugShellComponent, CodeHugLeftPanelComponent, CodeHugRightPanelComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: [`:host { display: flex; flex-direction: column; flex: 1; min-height: 0; }`],
  template: `
    <ch-shell>
      @if (showPanels()) { <ch-left-panel chLeft /> }
      @if (showPanels()) { <ch-right-panel chRight /> }
    </ch-shell>
  `
})
export class CodeHugShellWithPanelsComponent {
  private router = inject(Router);
  readonly currentUrl = signal(this.router.url);
  readonly showPanels = () => !this.currentUrl().includes('/codehug/internals');

  private _sub = this.router.events
    .pipe(filter(e => e instanceof NavigationEnd))
    .subscribe(() => this.currentUrl.set(this.router.url));
}