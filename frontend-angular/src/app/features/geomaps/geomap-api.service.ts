import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import {
  GeoMapAggregation,
  GeoMapCatalog,
  GeoMapExportArtifact,
  GeoMapGeometry,
  GeoMapProjection,
} from './geomap.models';

@Injectable({ providedIn: 'root' })
export class GeoMapApiService {
  private static readonly geometryTimeoutMs = 60_000;
  private readonly core = inject(HubApiCoreService);

  catalog(hubUrl: string): Observable<GeoMapCatalog> {
    return this.core.get<GeoMapCatalog>(`${this.endpoint(hubUrl)}/registry`, hubUrl);
  }

  geometry(hubUrl: string, mapId: string): Observable<GeoMapGeometry> {
    return this.core.get<GeoMapGeometry>(
      `${this.endpoint(hubUrl)}/${encodeURIComponent(mapId)}/geometry`,
      hubUrl,
      undefined,
      false,
      GeoMapApiService.geometryTimeoutMs,
    );
  }

  project(
    hubUrl: string,
    command: {
      map_id: string;
      rows: Array<Record<string, unknown>>;
      region_key: string;
      value_key: string;
      aggregation: GeoMapAggregation;
      data_attribution: string;
      minimum_match_ratio: number;
    },
  ): Observable<GeoMapProjection> {
    return this.core.post<GeoMapProjection>(`${this.endpoint(hubUrl)}/project`, command, hubUrl);
  }

  export(
    hubUrl: string,
    command: {
      map_id: string;
      rows: Array<Record<string, unknown>>;
      region_key: string;
      value_key: string;
      aggregation: GeoMapAggregation;
      data_attribution: string;
      minimum_match_ratio: number;
      output_format: 'svg' | 'png' | 'pdf' | 'html';
      title: string;
    },
  ): Observable<GeoMapExportArtifact> {
    return this.core.post<GeoMapExportArtifact>(`${this.endpoint(hubUrl)}/export`, command, hubUrl);
  }

  private endpoint(hubUrl: string): string {
    return `${hubUrl.replace(/\/+$/, '')}/api/geomaps`;
  }
}
