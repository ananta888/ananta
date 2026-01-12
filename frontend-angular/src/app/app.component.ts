import { Component } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';
import { NotificationsComponent } from './components/notifications.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterLink, RouterOutlet, NotificationsComponent],
  template: `
    <app-notifications />
    <header>
      <h1>Ananta – Agent Control</h1>
      <nav class="row">
        <a routerLink="/dashboard">Dashboard</a>
        <a routerLink="/agents">Agents</a>
        <a routerLink="/board">Board</a>
        <a routerLink="/templates">Templates</a>
        <a routerLink="/teams">Teams</a>
        <a routerLink="/settings">Settings</a>
      </nav>
    </header>
    <main>
      <router-outlet />
    </main>
  `
})
export class AppComponent {}
