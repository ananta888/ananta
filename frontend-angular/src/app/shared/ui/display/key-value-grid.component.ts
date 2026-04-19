import { Component, Input } from '@angular/core';

export interface KeyValueItem {
  label: string;
  value: string | number | null | undefined;
  hint?: string;
}

@Component({
  standalone: true,
  selector: 'app-key-value-grid',
  template: `
    <div class="grid shared-key-value-grid" [class.cols-2]="columns === 2" [class.cols-3]="columns === 3" [class.cols-4]="columns === 4">
      @for (item of items; track item.label) {
        <div class="shared-key-value-item">
          <div class="muted font-sm">{{ item.label }}</div>
          <strong>{{ item.value ?? emptyValue }}</strong>
          @if (item.hint) {
            <div class="muted font-sm">{{ item.hint }}</div>
          }
        </div>
      }
    </div>
  `,
})
export class KeyValueGridComponent {
  @Input() items: KeyValueItem[] = [];
  @Input() columns: 2 | 3 | 4 = 2;
  @Input() emptyValue = '-';
}
