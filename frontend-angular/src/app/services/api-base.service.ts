import { Injectable, inject } from '@angular/core';
import { HubApiCoreService } from './hub-api-core.service';

@Injectable({ providedIn: 'root' })
export class ApiBaseService {
  protected core = inject(HubApiCoreService);
}
