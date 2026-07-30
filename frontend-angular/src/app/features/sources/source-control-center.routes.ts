import { Routes } from '@angular/router';
import { SourceDetailComponent } from './source-detail.component';
import { SourceImportPageComponent } from './source-import-page.component';
import { SourceOverviewComponent } from './source-overview.component';

export const SOURCE_CONTROL_CENTER_ROUTES: Routes = [
  { path: '', pathMatch: 'full', component: SourceOverviewComponent },
  { path: 'add', component: SourceImportPageComponent },
  { path: ':sourceId', component: SourceDetailComponent },
];
