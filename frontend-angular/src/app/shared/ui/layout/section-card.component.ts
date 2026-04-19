import { Component, Input } from '@angular/core';
import { SectionHeaderComponent } from './section-header.component';

@Component({
  standalone: true,
  selector: 'app-section-card',
  imports: [SectionHeaderComponent],
  template: `
    <section class="card shared-section-card" [class.card-primary]="variant === 'primary'" [attr.aria-label]="ariaLabel || title">
      @if (title || subtitle) {
        <app-section-header [eyebrow]="eyebrow" [title]="title" [subtitle]="subtitle">
          <div section-actions>
            <ng-content select="[section-actions]"></ng-content>
          </div>
        </app-section-header>
      }
      <div [class.mt-md]="title || subtitle">
        <ng-content></ng-content>
      </div>
    </section>
  `,
})
export class SectionCardComponent {
  @Input() title = '';
  @Input() subtitle = '';
  @Input() eyebrow = '';
  @Input() ariaLabel = '';
  @Input() variant: 'default' | 'primary' = 'default';
}
