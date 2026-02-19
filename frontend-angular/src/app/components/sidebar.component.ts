import { Component, inject } from '@angular/core';
import { RouterLink, Router } from '@angular/router';
import { UserAuthService } from '../services/user-auth.service';
import { AsyncPipe } from '@angular/common';

@Component({
  selector: 'app-sidebar',
  standalone: true,
  imports: [RouterLink, AsyncPipe],
  template: `
    <aside class="sidebar">
      <nav class="sidebar-nav">
        <div class="nav-section">
          <span class="nav-section-title">Operate</span>
          <a routerLink="/dashboard" class="nav-link" [class.active]="isActive('/dashboard')">
            <span class="nav-icon">📊</span>
            Dashboard
          </a>
          <a routerLink="/agents" class="nav-link" [class.active]="isActive('/agents')">
            <span class="nav-icon">🤖</span>
            Agents
          </a>
          <a routerLink="/board" class="nav-link" [class.active]="isActive('/board')">
            <span class="nav-icon">📋</span>
            Board
          </a>
          <a routerLink="/operations" class="nav-link" [class.active]="isActive('/operations')">
            <span class="nav-icon">⚙️</span>
            Operations
          </a>
          <a routerLink="/archived" class="nav-link" [class.active]="isActive('/archived')">
            <span class="nav-icon">📁</span>
            Archive
          </a>
          <a routerLink="/graph" class="nav-link" [class.active]="isActive('/graph')">
            <span class="nav-icon">🔗</span>
            Graph
          </a>
        </div>

        <div class="nav-section">
          <span class="nav-section-title">Automate</span>
          <a routerLink="/auto-planner" class="nav-link" [class.active]="isActive('/auto-planner')">
            <span class="nav-icon">🎯</span>
            Auto-Planner
          </a>
          <a routerLink="/webhooks" class="nav-link" [class.active]="isActive('/webhooks')">
            <span class="nav-icon">🔗</span>
            Webhooks
          </a>
        </div>

        <div class="nav-section">
          <span class="nav-section-title">Configure</span>
          <a routerLink="/templates" class="nav-link" [class.active]="isActive('/templates')">
            <span class="nav-icon">📝</span>
            Templates
          </a>
          <a routerLink="/teams" class="nav-link" [class.active]="isActive('/teams')">
            <span class="nav-icon">👥</span>
            Teams
          </a>
          @if ((auth.user$ | async)?.role) === 'admin' {
            <a routerLink="/audit-log" class="nav-link" [class.active]="isActive('/audit-log')">
              <span class="nav-icon">📜</span>
              Audit Logs
            </a>
          }
          <a routerLink="/settings" class="nav-link" [class.active]="isActive('/settings')">
            <span class="nav-icon">⚙️</span>
            Settings
          </a>
        </div>
      </nav>
    </aside>
  `,
  styles: [`
    .sidebar {
      width: 220px;
      min-height: 100vh;
      background: var(--card-bg, #f8fafc);
      border-right: 1px solid var(--border, #e2e8f0);
      position: fixed;
      left: 0;
      top: 0;
      padding-top: 60px;
      overflow-y: auto;
    }
    .sidebar-nav {
      padding: 16px 8px;
    }
    .nav-section {
      margin-bottom: 24px;
    }
    .nav-section-title {
      display: block;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--muted, #64748b);
      padding: 8px 12px;
    }
    .nav-link {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      border-radius: 6px;
      color: var(--text, #334155);
      text-decoration: none;
      font-size: 14px;
      transition: all 0.15s ease;
    }
    .nav-link:hover {
      background: var(--hover-bg, #e2e8f0);
    }
    .nav-link.active {
      background: var(--primary-color, #3b82f6);
      color: white;
    }
    .nav-icon {
      font-size: 16px;
    }
    @media (max-width: 900px) {
      .sidebar {
        transform: translateX(-100%);
        transition: transform 0.3s ease;
        z-index: 1000;
      }
      .sidebar.open {
        transform: translateX(0);
      }
    }
  `]
})
export class SidebarComponent {
  auth = inject(UserAuthService);
  private router = inject(Router);

  isActive(path: string): boolean {
    return this.router.url === path || this.router.url.startsWith(path + '/');
  }
}
