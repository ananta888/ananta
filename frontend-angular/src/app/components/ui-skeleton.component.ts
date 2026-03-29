import { Component, Input } from '@angular/core';

@Component({
  standalone: true,
  selector: 'app-ui-skeleton',
  template: `
    <div class="grid gap-sm" [class.cols-2]="columns === 2" [class.cols-3]="columns === 3" [class.cols-4]="columns === 4">
      @for (_ of blocks(); track $index) {
        <div [class.card]="card" [class]="containerClass">
          @for (__ of lines(); track $index) {
            <div [class]="lineClass"></div>
          }
        </div>
      }
    </div>
  `,
})
export class UiSkeletonComponent {
  @Input() count = 1;
  @Input() lineCount = 3;
  @Input() columns = 1;
  @Input() card = true;
  @Input() containerClass = '';
  @Input() lineClass = 'skeleton line skeleton-40';

  blocks(): number[] {
    return Array.from({ length: Math.max(1, this.count) }, (_, index) => index);
  }

  lines(): number[] {
    return Array.from({ length: Math.max(1, this.lineCount) }, (_, index) => index);
  }
}
