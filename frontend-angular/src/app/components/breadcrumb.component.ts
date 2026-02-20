import { Component, OnInit } from '@angular/core';
import { Router, NavigationEnd, ActivatedRoute } from '@angular/router';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { filter, distinctUntilChanged } from 'rxjs/operators';

interface Breadcrumb {
  label: string;
  url: string;
}

@Component({
  selector: 'app-breadcrumb',
  standalone: true,
  imports: [CommonModule, RouterModule],
  template: `
    <nav class="breadcrumb-nav" aria-label="Breadcrumb" *ngIf="breadcrumbs.length > 0">
      <ol class="breadcrumb-list">
        <li class="breadcrumb-item">
          <a [routerLink]="['/dashboard']" class="breadcrumb-link">
            <span aria-label="Home">🏠</span>
          </a>
        </li>
        <li *ngFor="let breadcrumb of breadcrumbs; let last = last" class="breadcrumb-item">
          <span class="breadcrumb-separator" aria-hidden="true">/</span>
          <a *ngIf="!last" [routerLink]="breadcrumb.url" class="breadcrumb-link">
            {{ breadcrumb.label }}
          </a>
          <span *ngIf="last" class="breadcrumb-current" aria-current="page">
            {{ breadcrumb.label }}
          </span>
        </li>
      </ol>
    </nav>
  `,
  styles: [`
    .breadcrumb-nav {
      padding: 0.75rem 1rem;
      background-color: #f8f9fa;
      border-bottom: 1px solid #dee2e6;
      margin-bottom: 1rem;
    }

    .breadcrumb-list {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      list-style: none;
      margin: 0;
      padding: 0;
      gap: 0.5rem;
    }

    .breadcrumb-item {
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    .breadcrumb-link {
      color: #007bff;
      text-decoration: none;
      transition: color 0.2s;
    }

    .breadcrumb-link:hover {
      color: #0056b3;
      text-decoration: underline;
    }

    .breadcrumb-link:focus {
      outline: 2px solid #007bff;
      outline-offset: 2px;
      border-radius: 2px;
    }

    .breadcrumb-separator {
      color: #6c757d;
      user-select: none;
    }

    .breadcrumb-current {
      color: #6c757d;
      font-weight: 500;
    }
  `]
})
export class BreadcrumbComponent implements OnInit {
  breadcrumbs: Breadcrumb[] = [];

  private routeLabels: { [key: string]: string } = {
    'dashboard': 'Dashboard',
    'settings': 'Settings',
    'audit-log': 'Audit Log',
    'agents': 'Agents',
    'panel': 'Agent Panel',
    'templates': 'Templates',
    'teams': 'Teams',
    'board': 'Board',
    'archived': 'Archived Tasks',
    'graph': 'Task Graph',
    'operations': 'Operations Console',
    'auto-planner': 'Auto Planner',
    'webhooks': 'Webhooks',
    'task': 'Task Details'
  };

  constructor(
    private router: Router,
    private activatedRoute: ActivatedRoute
  ) {}

  ngOnInit(): void {
    this.router.events
      .pipe(
        filter(event => event instanceof NavigationEnd),
        distinctUntilChanged()
      )
      .subscribe(() => {
        this.breadcrumbs = this.createBreadcrumbs(this.activatedRoute.root);
      });

    // Initial breadcrumbs
    this.breadcrumbs = this.createBreadcrumbs(this.activatedRoute.root);
  }

  private createBreadcrumbs(route: ActivatedRoute, url: string = '', breadcrumbs: Breadcrumb[] = []): Breadcrumb[] {
    const children: ActivatedRoute[] = route.children;

    if (children.length === 0) {
      return breadcrumbs;
    }

    for (const child of children) {
      const routeURL: string = child.snapshot.url.map(segment => segment.path).join('/');
      if (routeURL !== '') {
        url += `/${routeURL}`;
      }

      // Get label from route data or use default
      const label = child.snapshot.data['breadcrumb'] || this.getLabelFromUrl(routeURL, child);

      if (label && routeURL !== '') {
        breadcrumbs.push({ label, url });
      }

      return this.createBreadcrumbs(child, url, breadcrumbs);
    }

    return breadcrumbs;
  }

  private getLabelFromUrl(url: string, route: ActivatedRoute): string {
    const segments = url.split('/');
    const firstSegment = segments[0];

    // Check if we have a predefined label
    if (this.routeLabels[firstSegment]) {
      // For parameterized routes, try to get the parameter value
      if (segments.length > 1) {
        const param = segments[1];
        return `${this.routeLabels[firstSegment]}: ${param}`;
      }
      return this.routeLabels[firstSegment];
    }

    // Fallback: capitalize first segment
    return firstSegment.charAt(0).toUpperCase() + firstSegment.slice(1);
  }
}
