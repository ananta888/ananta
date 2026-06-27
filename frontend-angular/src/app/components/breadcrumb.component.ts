import { Component, OnInit, inject } from '@angular/core';
import { Router, NavigationEnd, ActivatedRoute } from '@angular/router';

import { RouterModule } from '@angular/router';
import { filter, distinctUntilChanged } from 'rxjs/operators';
import { APP_ROUTE_META } from '../models/route-metadata';

interface Breadcrumb {
  label: string;
  url: string;
}

@Component({
  selector: 'app-breadcrumb',
  standalone: true,
  imports: [RouterModule],
  template: `
    @if (breadcrumbs.length > 0) {
      <nav class="breadcrumb-nav" aria-label="Breadcrumb">
        <ol class="breadcrumb-list">
          <li class="breadcrumb-item">
            <a [routerLink]="['/dashboard']" class="breadcrumb-link">
              <span aria-label="Home">🏠</span>
            </a>
          </li>
          @for (breadcrumb of breadcrumbs; track breadcrumb; let last = $last) {
            <li class="breadcrumb-item">
              <span class="breadcrumb-separator" aria-hidden="true">/</span>
              @if (!last) {
                <a [routerLink]="breadcrumb.url" class="breadcrumb-link">
                  {{ breadcrumb.label }}
                </a>
              }
              @if (last) {
                <span class="breadcrumb-current" aria-current="page">
                  {{ breadcrumb.label }}
                </span>
              }
            </li>
          }
        </ol>
      </nav>
    }
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
      color: #495057;
      user-select: none;
    }

    .breadcrumb-current {
      color: #495057;
      font-weight: 500;
    }
  `]
})
export class BreadcrumbComponent implements OnInit {
  breadcrumbs: Breadcrumb[] = [];

  private readonly router = inject(Router);
  private readonly activatedRoute = inject(ActivatedRoute);

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

    const meta = APP_ROUTE_META[firstSegment];
    if (meta) {
      if (segments.length > 1) {
        const param = segments[1];
        return `${meta.label}: ${param}`;
      }
      return meta.label;
    }

    // Fallback: capitalize first segment
    return firstSegment.charAt(0).toUpperCase() + firstSegment.slice(1);
  }
}
