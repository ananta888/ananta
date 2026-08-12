import { AiSnakeChatPanelComponent } from './ai-snake-chat-panel.component';
import { isAiSnakePanelTab } from './ai-snake-panel-tab';

describe('AiSnakeChatPanelComponent', () => {
  it('emits Pair as a navigation intent without owning its runtime', () => {
    const component = Object.create(AiSnakeChatPanelComponent.prototype) as AiSnakeChatPanelComponent;
    component.tabChange = { emit: vi.fn() } as never;

    component.setTab('pair');

    expect(component.tab).toBe('pair');
    expect(component.tabChange.emit).toHaveBeenCalledWith('pair');
  });

  it('keeps Pair in the panel contract while rejecting removed legacy tabs', () => {
    expect(isAiSnakePanelTab('pair')).toBe(true);
    expect(isAiSnakePanelTab('mode')).toBe(false);
    expect(isAiSnakePanelTab('deprecated')).toBe(false);
    expect(isAiSnakePanelTab('process')).toBe(true);
    expect(isAiSnakePanelTab('media')).toBe(true);
  });
});
