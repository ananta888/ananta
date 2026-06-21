import { routes } from '/home/krusty/ananta/frontend-angular/src/app/app.routes';

function flatten(items: any[], acc: any[] = []): any[] {
  for (const route of items) {
    acc.push(route);
    if (route.children) flatten(route.children, acc);
  }
  return acc;
}

describe('debug routes', () => {
  it('list all lazy paths', () => {
    const all = flatten(routes as any);
    const lazy = all.filter(r => typeof r.loadComponent === 'function');
    console.log('PATHS:', JSON.stringify(lazy.map(r => r.path)));
    console.log('COUNT:', lazy.length);
    expect(true).toBe(true);
  });
});
