export type TerritoryVisibility = 'visible' | 'blocked' | 'hidden' | 'redacted';

export interface GameMapTerritoryView {
  id: string;
  name: string;
  path: string;
  visibility: TerritoryVisibility;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
}

export interface GameMapAgentView {
  id: string;
  role: string;
  capabilities: string[];
}

export interface GameMapPolicyView {
  id: string;
  effect: 'allow' | 'deny' | 'review_required';
  scope: string[];
}

export interface GameMapContextGateView {
  territoryId: string;
  visibility: TerritoryVisibility;
  localOnly: boolean;
  secret: boolean;
}

export interface GameMapArtifactView {
  id: string;
  taskId: string;
  status: 'open' | 'failed' | 'verified';
}

export interface GameMapUiContract {
  id: string;
  title: string;
  territories: GameMapTerritoryView[];
  agents: GameMapAgentView[];
  policies: GameMapPolicyView[];
  contextGates: GameMapContextGateView[];
  artifacts: GameMapArtifactView[];
  degraded: boolean;
}

export const DEMO_GAME_MAP: GameMapUiContract = {
  id: 'map:demo-ui',
  title: 'Ananta Strategy Demo Map',
  territories: [
    { id: 't1', name: 'agent/services', path: 'agent/services', visibility: 'visible', riskLevel: 'high' },
    { id: 't2', name: 'docs/ananta-game', path: 'docs/ananta-game', visibility: 'visible', riskLevel: 'low' },
    { id: 't3', name: 'data/secrets', path: 'data/secrets', visibility: 'blocked', riskLevel: 'critical' },
  ],
  agents: [
    { id: 'a1', role: 'hub', capabilities: ['plan', 'delegate', 'approve'] },
    { id: 'a2', role: 'local_worker', capabilities: ['analyze', 'implement'] },
  ],
  policies: [
    { id: 'p1', effect: 'deny', scope: ['secret_paths', 'cloud_worker'] },
    { id: 'p2', effect: 'review_required', scope: ['project_write'] },
  ],
  contextGates: [
    { territoryId: 't1', visibility: 'visible', localOnly: true, secret: false },
    { territoryId: 't3', visibility: 'redacted', localOnly: true, secret: true },
  ],
  artifacts: [
    { id: 'artifact:1', taskId: 'ASG-011', status: 'verified' },
    { id: 'artifact:2', taskId: 'ASG-005', status: 'open' },
  ],
  degraded: false,
};
