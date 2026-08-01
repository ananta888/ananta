import { Routes } from '@angular/router';
import { SourceDetailComponent } from './source-detail.component';
import { SourceImportPageComponent } from './source-import-page.component';
import { SourceOverviewComponent } from './source-overview.component';
import { SourceIndexJourneyComponent } from './source-index-journey.component';
import { projectContextGuard } from '../../guards/project-context.guard';

export const SOURCE_CONTROL_CENTER_ROUTES: Routes = [
  { path: '', pathMatch: 'full', component: SourceOverviewComponent, canActivate: [projectContextGuard] },
  { path: 'add', component: SourceImportPageComponent, canActivate: [projectContextGuard] },
  { path: 'journey', component: SourceIndexJourneyComponent, canActivate: [projectContextGuard] },
  { path: ':sourceId', component: SourceDetailComponent, canActivate: [projectContextGuard] },
];
