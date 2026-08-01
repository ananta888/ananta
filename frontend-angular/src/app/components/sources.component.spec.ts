import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import { RouterLink, provideRouter } from '@angular/router';

import { SourcesComponent } from './sources.component';

describe('SourcesComponent', () => {
  it('preserves the project query context across shell navigation', async () => {
    await TestBed.configureTestingModule({
      imports: [SourcesComponent],
      providers: [provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(SourcesComponent);
    fixture.detectChanges();

    const links = fixture.debugElement
      .queryAll(By.directive(RouterLink))
      .map((element) => element.injector.get(RouterLink));

    expect(links).toHaveLength(2);
    expect(links.every((link) => link.queryParamsHandling === 'preserve')).toBeTruthy();
  });
});
