import { Injectable } from '@angular/core';
import { MarkdownWorkspaceDeckAdapter } from './markdown-slide.models';

@Injectable({ providedIn: 'root' })
export class MarkdownSlideWorkspaceService implements MarkdownWorkspaceDeckAdapter {
  readonly supported = false;
  readonly disabledReason = 'Workspace deck storage requires a Hub artifact/file endpoint and must pass Hub policy checks.';

  async loadMarkdownDeck(_sourcePath: string): Promise<string> {
    throw new Error(this.disabledReason);
  }

  async saveMarkdownDeck(_sourcePath: string, _markdown: string): Promise<void> {
    throw new Error(this.disabledReason);
  }
}
