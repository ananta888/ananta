import { ComponentFixture, TestBed } from '@angular/core/testing';

import { StrategyGameDemoComponent } from './strategy-game-demo.component';

describe('StrategyGameDemoComponent', () => {
  let fixture: ComponentFixture<StrategyGameDemoComponent>;
  let component: StrategyGameDemoComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [StrategyGameDemoComponent],
    }).compileComponents();
    fixture = TestBed.createComponent(StrategyGameDemoComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('renders demo territories from static contract', () => {
    const cards = fixture.nativeElement.querySelectorAll('.territory');
    expect(cards.length).toBe(component.map.territories.length);
  });

  it('marks blocked territories with blocked class', () => {
    const blocked = fixture.nativeElement.querySelectorAll('.territory.blocked');
    expect(blocked.length).toBeGreaterThan(0);
  });
});
