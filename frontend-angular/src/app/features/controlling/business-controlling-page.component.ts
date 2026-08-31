import { ChangeDetectionStrategy, Component, inject } from '@angular/core';

import { ProjectContextService } from '../../services/project-context.service';
import { BusinessControllingWorkbenchComponent } from './business-controlling-workbench.component';

@Component({
  selector: 'app-business-controlling-page',
  standalone: true,
  imports: [BusinessControllingWorkbenchComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <app-business-controlling-workbench
      [projectId]="projectContext.selectedProjectId()"
    />
  `,
})
export class BusinessControllingPageComponent {
  protected readonly projectContext = inject(ProjectContextService);
}
