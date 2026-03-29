import { Routes } from '@angular/router';

import { ArtifactsComponent } from '../../components/artifacts.component';
import { TeamsComponent } from '../../components/teams.component';
import { TemplatesComponent } from '../../components/templates.component';

export const adminRoutes: Routes = [
  { path: 'templates', component: TemplatesComponent },
  { path: 'teams', component: TeamsComponent },
  { path: 'artifacts', component: ArtifactsComponent },
];
