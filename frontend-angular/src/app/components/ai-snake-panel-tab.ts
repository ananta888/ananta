export const AI_SNAKE_PANEL_TABS = [
  'chat',
  'sessions',
  'process',
  'trace',
  'login',
  'settings',
] as const;

export type AiSnakePanelTab = (typeof AI_SNAKE_PANEL_TABS)[number];

export function isAiSnakePanelTab(value: unknown): value is AiSnakePanelTab {
  return typeof value === 'string'
    && (AI_SNAKE_PANEL_TABS as readonly string[]).includes(value);
}
