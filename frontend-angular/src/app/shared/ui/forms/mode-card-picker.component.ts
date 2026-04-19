import { Component, EventEmitter, Input, Output } from '@angular/core';

export interface ModeCardOption {
  id: string;
  title: string;
  description?: string;
  status?: string;
  disabled?: boolean;
}

@Component({
  standalone: true,
  selector: 'app-mode-card-picker',
  template: `
    <div class="shared-mode-picker" [class.cols-2]="columns === 2" [class.cols-3]="columns === 3" [class.cols-4]="columns === 4" [attr.aria-label]="ariaLabel">
      @for (option of options; track option.id) {
        <button
          type="button"
          class="card card-light shared-mode-card"
          [class.active]="option.id === selectedId"
          [disabled]="option.disabled"
          (click)="selectOption.emit(option)"
          [attr.aria-pressed]="option.id === selectedId"
        >
          <span class="shared-mode-card-title">{{ option.title }}</span>
          @if (option.description) {
            <span class="muted font-sm">{{ option.description }}</span>
          }
          @if (option.status) {
            <span class="badge">{{ option.status }}</span>
          }
        </button>
      }
    </div>
  `,
})
export class ModeCardPickerComponent {
  @Input() options: ModeCardOption[] = [];
  @Input() selectedId = '';
  @Input() columns: 2 | 3 | 4 = 3;
  @Input() ariaLabel = 'Auswahl';
  @Output() selectOption = new EventEmitter<ModeCardOption>();
}
