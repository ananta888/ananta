export type GeoMapAggregation = 'preaggregated' | 'sum' | 'mean' | 'min' | 'max' | 'count';

export interface GeoMapDefinition {
  id: string;
  label: string;
  level: 'country' | 'continent' | 'subdivision' | 'nuts' | 'custom';
  format: 'geojson' | 'svg';
  source: string;
  featureIdPath: 'properties.id';
  dataJoinKey: string;
  supportedRenderers: Array<'echarts' | 'plotly'>;
  bounds: [number, number, number, number];
  license: string;
  licenseUrl?: string;
  attribution: string;
  minimumMatchRatio?: number;
}

export interface GeoMapCatalog {
  schema: 'ananta.geomap-registry.v1';
  version: 1;
  maps: GeoMapDefinition[];
}

export interface GeoJsonFeatureCollection {
  type: 'FeatureCollection';
  features: Array<{
    type: 'Feature';
    id?: string;
    properties: { id: string; name?: string; [key: string]: unknown };
    geometry: Record<string, unknown>;
  }>;
}

export interface GeoSvgMap {
  type: 'AnantaSvgMap';
  svg: string;
  features: Array<{ properties: { id: string; name: string } }>;
}

export type GeoMapGeometry = GeoJsonFeatureCollection | GeoSvgMap;

export interface GeoMapProjection {
  schema: 'ananta.geomap-projection.v1';
  map_id: string;
  registry_version: number;
  aggregation: GeoMapAggregation;
  values: Array<{ region_id: string; name: string; value: number; source_rows: number }>;
  report: {
    matched: string[];
    unmatched: string[];
    duplicates: string[];
    missing_geometry: string[];
    invalid_values: string[];
    match_ratio: number;
    minimum_match_ratio: number;
    publication_eligible: boolean;
    reason_codes: string[];
  };
  map_attribution: string;
  data_attribution: string;
}

export interface GeoMapDraft {
  schema: 'ananta.geomap-draft.v1';
  mapId: string;
  regionKey: string;
  valueKey: string;
  aggregation: GeoMapAggregation;
  minimumMatchRatio: number;
  dataAttribution: string;
}

export interface GeoMapExportArtifact {
  schema: 'ananta.geomap-export-artifact.v1';
  filename: string;
  media_type: string;
  content_base64: string;
  metadata: Record<string, unknown>;
  publication_eligible: boolean;
}
