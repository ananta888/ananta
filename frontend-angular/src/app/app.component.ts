import { Component } from '@angular/core';
import { RouterLink, RouterOutlet } from '@angular/router';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterLink, RouterOutlet],
  template: `
    <header>
      <h1>Ananta – Agent Control</h1>
      <nav class="row">
        <a routerLink="/agents">Agents</a>
        <a routerLink="/board">Board</a>
        <a routerLink="/templates">Templates</a>
      </nav>
    </header>
    <main>
      <router-outlet />
    </main>
  `
})
export class AppComponent {}
