import { Component, Input } from '@angular/core';

@Component({
  standalone: true,
  selector: 'app-control-center-placeholder',
  template: `<h2>{{ title }}</h2><p class="muted">{{ text }}</p>`,
  styles: [`.muted{color:#94a3b8}`]
})
export class ControlCenterPlaceholderComponent {
  @Input() title = 'Control Center';
  @Input() text = 'Dieser Bereich wird im naechsten Sprint vertieft.';
}
