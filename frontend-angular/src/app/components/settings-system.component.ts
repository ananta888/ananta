import { Component, Input } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SettingsState } from './settings-state.service';
import { OperationPolicyInventoryComponent } from './operation-policy-inventory.component';

@Component({
  selector: 'app-settings-system',
  standalone: true,
  imports: [FormsModule, OperationPolicyInventoryComponent],
  templateUrl: './settings-system.component.html',
})
export class SettingsSystemComponent {
  @Input({ required: true }) state!: SettingsState;
}
