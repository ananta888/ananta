import { Component } from '@angular/core';

@Component({
  standalone: true,
  selector: 'app-control-center-dashboard',
  template: `
    <h2>Dashboard</h2>
    <p class="muted">Uebersicht zu laufenden Sessions, blockierten Tasks und Verification-Status.</p>
  `,
  styles: [`.muted{color:#94a3b8}`]
})
export class ControlCenterDashboardComponent {}
