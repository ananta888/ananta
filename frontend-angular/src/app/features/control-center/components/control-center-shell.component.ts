import { Component } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';

@Component({
  standalone: true,
  selector: 'app-control-center-shell',
  imports: [RouterOutlet, RouterLink],
  template: `
    <section class="cc-shell">
      <aside class="cc-left">
        <h3>Control Center</h3>
        <nav>
          <a routerLink="dashboard">Dashboard</a>
          <a routerLink="tasks">Tasks</a>
          <a routerLink="sessions">Sessions</a>
          <a routerLink="artifacts">Artifacts</a>
          <a routerLink="workers">Workers</a>
          <a routerLink="policies">Policies</a>
          <a routerLink="codecompass">CodeCompass</a>
        </nav>
      </aside>
      <main class="cc-center"><router-outlet /></main>
      <aside class="cc-right">
        <h4>Agent / Policy Inspector</h4>
        <p class="muted">Worker, Modell, Runtime, erlaubte/verweigerte Tools und Pfade.</p>
      </aside>
    </section>
  `,
  styles: [`
    .cc-shell { display:grid; grid-template-columns: 220px 1fr 280px; gap:12px; min-height: calc(100vh - 120px); }
    .cc-left,.cc-center,.cc-right{ border:1px solid #1f2937; border-radius:12px; background:#0b1220; color:#e5e7eb; padding:12px; }
    nav{ display:flex; flex-direction:column; gap:8px; }
    a{ color:#93c5fd; text-decoration:none; }
    .muted{ color:#94a3b8; font-size:12px; }
    @media (max-width: 1100px){ .cc-shell{ grid-template-columns: 200px 1fr; } .cc-right{ grid-column: 1 / -1; } }
    @media (max-width: 800px){ .cc-shell{ grid-template-columns: 1fr; } }
  `]
})
export class ControlCenterShellComponent {}
