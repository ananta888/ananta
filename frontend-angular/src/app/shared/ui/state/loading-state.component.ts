import { Component, Input } from '@angular/core';
import { UiSkeletonComponent } from '../../../components/ui-skeleton.component';

@Component({
  standalone: true,
  selector: 'app-loading-state',
  imports: [UiSkeletonComponent],
  template: `
    <section class="shared-loading-state" [attr.aria-label]="ariaLabel || label" aria-busy="true">
      @if (label) {
        <div class="muted font-sm mb-sm">{{ label }}</div>
      }
      <app-ui-skeleton
        [count]="count"
        [lineCount]="lineCount"
        [columns]="columns"
        [card]="card"
        [containerClass]="containerClass"
        [lineClass]="lineClass"
      ></app-ui-skeleton>
    </section>
  `,
})
export class LoadingStateComponent {
  @Input() label = 'Wird geladen...';
  @Input() ariaLabel = '';
  @Input() count = 1;
  @Input() lineCount = 3;
  @Input() columns = 1;
  @Input() card = true;
  @Input() containerClass = '';
  @Input() lineClass = 'skeleton line skeleton-40';
}
