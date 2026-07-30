import { SourcesComponent } from './sources.component';

describe('SourcesComponent', () => {
  it('is a dependency-free shell class', () => {
    expect(new SourcesComponent()).toBeInstanceOf(SourcesComponent);
  });
});
