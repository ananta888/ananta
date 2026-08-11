import { EventEmitter } from '@angular/core';

import { AiSnakeChatPanelComponent } from './ai-snake-chat-panel.component';

describe('AiSnakeChatPanelComponent', () => {
  it('delegates Pair Dev navigation instead of owning a media runtime', () => {
    const component = Object.create(AiSnakeChatPanelComponent.prototype) as AiSnakeChatPanelComponent;
    component.pairDevRequested = new EventEmitter<void>();
    const requested = vi.fn();
    component.pairDevRequested.subscribe(requested);

    component.openPairDev();

    expect(requested).toHaveBeenCalledOnce();
  });
});
