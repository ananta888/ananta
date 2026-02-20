import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-not-found',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="not-found-container">
      <div class="not-found-content">
        <h1 class="not-found-title">404</h1>
        <h2 class="not-found-subtitle">Seite nicht gefunden</h2>
        <p class="not-found-message">
          Die angeforderte Seite existiert nicht oder wurde verschoben.
        </p>

        <div class="not-found-links">
          <h3>Hilfreiche Links:</h3>
          <div class="link-grid">
            <a routerLink="/dashboard" class="link-card">
              <span class="link-icon">📊</span>
              <span class="link-text">Dashboard</span>
            </a>
            <a routerLink="/board" class="link-card">
              <span class="link-icon">📋</span>
              <span class="link-text">Board</span>
            </a>
            <a routerLink="/agents" class="link-card">
              <span class="link-icon">🤖</span>
              <span class="link-text">Agents</span>
            </a>
            <a routerLink="/settings" class="link-card">
              <span class="link-icon">⚙️</span>
              <span class="link-text">Settings</span>
            </a>
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [`
    .not-found-container {
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 70vh;
      padding: 24px;
    }
    .not-found-content {
      text-align: center;
      max-width: 600px;
    }
    .not-found-title {
      font-size: 96px;
      font-weight: 700;
      margin: 0;
      color: var(--accent);
      line-height: 1;
    }
    .not-found-subtitle {
      font-size: 32px;
      font-weight: 600;
      margin: 16px 0;
      color: var(--fg);
    }
    .not-found-message {
      font-size: 16px;
      color: var(--muted);
      margin: 16px 0 32px;
    }
    .not-found-links h3 {
      font-size: 18px;
      font-weight: 600;
      margin-bottom: 16px;
      color: var(--fg);
    }
    .link-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
      margin-top: 16px;
    }
    .link-card {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
      padding: 20px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--card-bg);
      text-decoration: none;
      color: var(--fg);
      transition: all 0.2s ease;
    }
    .link-card:hover {
      border-color: var(--accent);
      transform: translateY(-2px);
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
    }
    .link-icon {
      font-size: 32px;
    }
    .link-text {
      font-size: 14px;
      font-weight: 500;
    }
    @media (max-width: 600px) {
      .not-found-title {
        font-size: 64px;
      }
      .not-found-subtitle {
        font-size: 24px;
      }
      .link-grid {
        grid-template-columns: 1fr;
      }
    }
  `]
})
export class NotFoundComponent {}
