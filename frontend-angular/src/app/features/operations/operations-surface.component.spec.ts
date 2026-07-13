import { Component, Input } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { OperationsSurfaceComponent } from './operations-surface.component';

@Component({ selector: 'app-git-ops', standalone: true, template: '<p>git-child</p>' })
class GitStubComponent { @Input() baseUrl = ''; @Input() refreshGeneration = 0; }

@Component({ selector: 'app-docker-ops', standalone: true, template: '<p>docker-child</p>' })
class DockerStubComponent { @Input() baseUrl = ''; @Input() refreshGeneration = 0; }

@Component({ selector: 'app-compose-ops', standalone: true, template: '<p>compose-child</p>' })
class ComposeStubComponent { @Input() baseUrl = ''; @Input() refreshGeneration = 0; }

@Component({ selector: 'app-ops-approval-panel', standalone: true, template: '<p>approval-child</p>' })
class ApprovalStubComponent { @Input() baseUrl = ''; @Input() refreshGeneration = 0; }

describe('OperationsSurfaceComponent', () => {
  it('orchestrates focused area components instead of owning domain state', async () => {
    await TestBed.configureTestingModule({ imports: [OperationsSurfaceComponent] })
      .overrideComponent(OperationsSurfaceComponent, {
        set: { imports: [GitStubComponent, DockerStubComponent, ComposeStubComponent, ApprovalStubComponent] },
      })
      .compileComponents();
    const fixture = TestBed.createComponent(OperationsSurfaceComponent);
    fixture.componentRef.setInput('baseUrl', 'http://hub');
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent).toContain('git-child');

    const composeFixture = TestBed.createComponent(OperationsSurfaceComponent);
    composeFixture.componentInstance.area = 'compose';
    composeFixture.componentRef.setInput('baseUrl', 'http://hub');
    composeFixture.detectChanges();
    expect(composeFixture.nativeElement.textContent).toContain('compose-child');
    expect(composeFixture.nativeElement.textContent).not.toContain('git-child');
  });

  it('increments one refresh generation for the active domain component', async () => {
    await TestBed.configureTestingModule({ imports: [OperationsSurfaceComponent] })
      .overrideComponent(OperationsSurfaceComponent, {
        set: { imports: [GitStubComponent, DockerStubComponent, ComposeStubComponent, ApprovalStubComponent] },
      })
      .compileComponents();
    const fixture = TestBed.createComponent(OperationsSurfaceComponent);
    const before = fixture.componentInstance.effectiveRefreshGeneration();
    fixture.componentInstance.refresh();
    expect(fixture.componentInstance.effectiveRefreshGeneration()).toBe(before + 1);
  });
});
