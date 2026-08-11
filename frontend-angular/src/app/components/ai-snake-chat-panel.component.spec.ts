import { EventEmitter } from '@angular/core';

import { AiSnakeChatPanelComponent } from './ai-snake-chat-panel.component';
import { isAiSnakePanelTab } from './ai-snake-panel-tab';

describe('AiSnakeChatPanelComponent', () => {
  it('delegates Pair Dev navigation instead of owning a media runtime', () => {
    const component = Object.create(AiSnakeChatPanelComponent.prototype) as AiSnakeChatPanelComponent;
    component.pairDevRequested = new EventEmitter<void>();
    const requested = vi.fn();
    component.pairDevRequested.subscribe(requested);

    component.openPairDev();

    expect(requested).toHaveBeenCalledOnce();
  });

  it('rejects removed legacy tabs from the panel state contract', () => {
    expect(isAiSnakePanelTab('pair')).toBe(false);
    expect(isAiSnakePanelTab('mode')).toBe(false);
    expect(isAiSnakePanelTab('deprecated')).toBe(false);
    expect(isAiSnakePanelTab('process')).toBe(true);
  });
});
