import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { RouterLink, provideRouter } from '@angular/router';
import { vi } from 'vitest';

import { SourceControlCenterFacade } from './source-control-center.facade';
import { SourceOverviewComponent } from './source-overview.component';
import { ProjectContextService } from '../../services/project-context.service';

describe('SourceOverviewComponent', () => {
  it('preserves project query context for every add-source link', async () => {
    const facade = {
      rows: signal<readonly never[]>([]),
      loading: signal(false),
      mutating: signal(false),
      nextCursor: signal<string | null>(null),
      error: signal<{ reasonCode?: string } | null>(null),
      viewState: signal('empty'),
      stateMessage: signal('Keine Quellen'),
      load: vi.fn(),
      loadMore: vi.fn(),
      refreshSource: vi.fn(),
      scanSource: vi.fn(),
    };
    await TestBed.configureTestingModule({
      imports: [SourceOverviewComponent],
      providers: [
        provideRouter([]),
        { provide: SourceControlCenterFacade, useValue: facade },
        {
          provide: ProjectContextService,
          useValue: { hasProject: signal(true) },
        },
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(SourceOverviewComponent);
    fixture.detectChanges();

    const addLinks = fixture.debugElement
      .queryAll(By.directive(RouterLink))
      .map((element) => element.injector.get(RouterLink))
      .filter((link) => link.urlTree.toString().includes('add'));

    expect(addLinks).toHaveLength(2);
    expect(addLinks.every((link) => link.queryParamsHandling === 'preserve')).toBeTruthy();
  });
});
