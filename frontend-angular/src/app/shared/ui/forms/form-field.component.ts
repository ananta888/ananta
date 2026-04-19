import { Component, Input } from '@angular/core';

@Component({
  standalone: true,
  selector: 'app-form-field',
  template: `
    <label class="shared-form-field" [class.invalid]="error" [class.inline]="inline">
      <span class="shared-form-field-label">
        {{ label }}
        @if (required) {
          <span aria-hidden="true">*</span>
        }
      </span>
      <ng-content></ng-content>
      @if (error) {
        <span class="error-text">{{ error }}</span>
      } @else if (hint) {
        <span class="hint-text">{{ hint }}</span>
      }
    </label>
  `,
})
export class FormFieldComponent {
  @Input() label = '';
  @Input() hint = '';
  @Input() error = '';
  @Input() required = false;
  @Input() inline = false;
}
