import { TestBed } from "@angular/core/testing";

import { SemanticDebugPanelComponent } from "./semantic-debug-panel.component";

describe("SemanticDebugPanelComponent", () => {
  it("renders only bounded read-only audit fields and paginates explicitly", async () => {
    await TestBed.configureTestingModule({
      imports: [SemanticDebugPanelComponent],
    }).compileComponents();
    const fixture = TestBed.createComponent(SemanticDebugPanelComponent);
    fixture.componentInstance.events = [
      {
        event_id: "audit-one",
        scope_digest: "a".repeat(64),
        event_type: "semantic_contract",
        transition: "activated",
        reason_code: "hub_confirmed",
        epoch: 7,
        contract_ref: "b".repeat(64),
        lease_ref: null,
        job_ref: null,
        created_at_ms: 1_000,
        expires_at_ms: 2_000,
      },
    ];
    fixture.componentInstance.nextCursor = "audit-one";
    const cursors: string[] = [];
    fixture.componentInstance.nextPage.subscribe((cursor) =>
      cursors.push(cursor),
    );
    fixture.detectChanges();
    const text = fixture.nativeElement.textContent;
    expect(text).toContain("Nur lesbar");
    expect(text).toContain("semantic_contract · activated");
    expect(text).not.toMatch(
      /transcript|audio|feature|schlüssel|toolaktion auslösen/i,
    );
    fixture.nativeElement.querySelector("button").click();
    expect(cursors).toEqual(["audit-one"]);
    expect(
      fixture.nativeElement.querySelectorAll("input, textarea, select").length,
    ).toBe(0);
  });

  it("announces unavailable state without exposing mutation controls", async () => {
    await TestBed.configureTestingModule({
      imports: [SemanticDebugPanelComponent],
    }).compileComponents();
    const fixture = TestBed.createComponent(SemanticDebugPanelComponent);
    fixture.componentInstance.errorCode = "semantic_debug_forbidden";
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector('[role="alert"]').textContent,
    ).toContain("semantic_debug_forbidden");
    expect(fixture.nativeElement.querySelector("button")).toBeNull();
  });
});
