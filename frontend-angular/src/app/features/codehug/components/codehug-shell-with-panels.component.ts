import { Component, ChangeDetectionStrategy } from '@angular/core';
import { CodeHugShellComponent } from './codehug-shell.component';
import { CodeHugLeftPanelComponent } from './codehug-left-panel.component';
import { CodeHugRightPanelComponent } from './codehug-right-panel.component';

/**
 * Parent fuer alle CodeHug-Sub-Routen. Stellt die Shell mit linken/rechten
 * Content-Slots bereit; die mittlere Spalte rendert via <router-outlet> in
 * der Shell.
 *
 * Verwendung:
 * <ch-shell-with-panels>
 *   <router-outlet>  (wird via Shell-inner-outlet gerendert)
 * </ch-shell-with-panels>
 *
 * Layout-Anker fuer CH-001.
 */
@Component({
  selector: 'ch-shell-with-panels',
  standalone: true,
  imports: [CodeHugShellComponent, CodeHugLeftPanelComponent, CodeHugRightPanelComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ch-shell>
      <ch-left-panel chLeft />
      <ch-right-panel chRight />
    </ch-shell>
  `
})
export class CodeHugShellWithPanelsComponent {}