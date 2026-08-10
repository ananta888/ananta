import { ɵresolveComponentResources } from "@angular/core";
import { ComponentFixture, TestBed } from "@angular/core/testing";
import { By } from "@angular/platform-browser";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { BehaviorSubject, of, Subject } from "rxjs";

import { AgentDirectoryService } from "../../services/agent-directory.service";
import { SpeechReconciliationApiService } from "../../services/speech-reconciliation-api.service";
import { LivekitSfuTransportService } from "../../services/livekit-sfu-transport.service";
import { WebrtcMediaSessionService } from "../../services/webrtc-media-session.service";
import { PublicPairMediaPublicationConsentState } from "../../services/public-pair-media-publication-consent.service";

import {
  SemanticMediaProgramShellComponent,
  SemanticProgramCapabilityView,
} from "./semantic-media-program-shell.component";
import { SpeechReconciliationPanelComponent } from "./speech-reconciliation-panel.component";
import { SemanticRemoteAudioComponent } from "./semantic-remote-audio.component";
import { WebrtcMediaPanelComponent } from "../pair-view/webrtc-media-panel.component";
import {
  PublicPairMediaPublicationConsentPanelComponent,
} from "./public-pair-media-publication-consent-panel.component";

beforeAll(async () => {
  await ɵresolveComponentResources((resource) => {
    const file = path.basename(String(resource));
    const directory = file.startsWith("pair-compute-") ? "pair-view" : "voice";
    return readFile(path.resolve(process.cwd(), "src/app/features", directory, file), "utf8");
  });
});

function publicationConsentState(
  patch: Partial<PublicPairMediaPublicationConsentState> = {},
): PublicPairMediaPublicationConsentState {
  return {
    status: "inactive",
    binding: {
      sessionId: "session-a", securityEpoch: 3, contractDigest: "a".repeat(64), adapterGeneration: 7,
      localPeerId: "alice", remotePeerId: "bob", maxExpiresAtMs: Date.now() + 3_600_000,
    },
    revision: 0,
    term: null,
    slots: [],
    expiresAtMs: null,
    ...patch,
  };
}

describe("SemanticMediaProgramShellComponent", () => {
  let fixture: ComponentFixture<SemanticMediaProgramShellComponent>;
  let capabilities: SemanticProgramCapabilityView[];

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [SemanticMediaProgramShellComponent],
      providers: [
        {
          provide: SpeechReconciliationApiService,
          useValue: { list: () => of({ jobs: [], next_offset: null }) },
        },
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ name: "hub", role: "hub", url: "http://hub:5000" }] },
        },
        {
          provide: WebrtcMediaSessionService,
          useValue: { remoteTracks$: new BehaviorSubject<readonly unknown[]>([]) },
        },
        { provide: LivekitSfuTransportService, useValue: { remoteTrack$: new Subject() } },
      ],
    }).compileComponents();
    fixture = TestBed.createComponent(SemanticMediaProgramShellComponent);
    fixture.componentInstance.scope = {
      direction: "sender_to_receiver",
      dataClass: "text_corrections",
      purpose: "speech_dataset_curation",
      retentionLabel: "1 Stunde",
      trainerLocation: "lokal",
      e2eeMode: "pair-e2ee",
      ordinaryFallback: "aktiv",
    };
    capabilities = [
      {
        capability: "live_speech",
        label: "Live Speech",
        sensitive: false,
        state: "locally_desired",
        requestId: null,
      },
      {
        capability: "raw_audio",
        label: "Raw Audio",
        sensitive: true,
        state: "revoked",
        requestId: null,
      },
      {
        capability: "training",
        label: "Training",
        sensitive: true,
        state: "expired",
        requestId: null,
      },
      {
        capability: "adapter_activation",
        label: "Adapter",
        sensitive: true,
        state: "degraded",
        requestId: null,
      },
      {
        capability: "export",
        label: "Export",
        sensitive: true,
        state: "failed",
        requestId: null,
      },
    ];
    fixture.componentInstance.capabilities = capabilities;
    fixture.detectChanges();
  });

  it("shows scope, all authoritative states and no aggregate sensitive grant", () => {
    const text = fixture.nativeElement.textContent;
    for (const value of [
      "Richtung",
      "Datenklasse",
      "Zweck",
      "Aufbewahrung",
      "Trainerstandort",
      "E2EE-Modus",
      "Ordinary-Fallback",
    ]) {
      expect(text).toContain(value);
    }
    expect(
      fixture.nativeElement.querySelectorAll(".sensitive button:first-of-type")
        .length,
    ).toBe(4);
    expect(text).not.toMatch(/alle freigeben|sammelaktion/i);
  });

  it("fences duplicate clicks and ignores stale Hub responses", () => {
    const emitted: string[] = [];
    fixture.componentInstance.intent.subscribe((value) =>
      emitted.push(value.requestId),
    );
    fixture.componentInstance.request("live_speech", "activate");
    fixture.componentInstance.request("live_speech", "activate");
    expect(emitted).toHaveLength(1);
    expect(capabilities[0].state).toBe("locally_desired");
    expect(fixture.componentInstance.capabilities[0].state).toBe("sent_to_hub");
    expect(
      fixture.componentInstance.applyHubState(
        "live_speech",
        "stale-request",
        "authoritatively_active",
      ),
    ).toBe(false);
    expect(
      fixture.componentInstance.applyHubState(
        "live_speech",
        emitted[0],
        "authoritatively_active",
      ),
    ).toBe(true);
  });

  it("blocks offline activation but leaves explicit pause/revoke controls keyboard reachable", () => {
    fixture.componentRef.setInput("online", false);
    fixture.detectChanges();
    const firstArticle = fixture.nativeElement.querySelector("article");
    expect(firstArticle.querySelector("button").disabled).toBe(true);
    expect(
      firstArticle.querySelectorAll("button")[1].getAttribute("type"),
    ).toBe("button");
    expect(
      firstArticle.querySelectorAll("button")[2].getAttribute("type"),
    ).toBe("button");
    expect(
      fixture.nativeElement.querySelector('[role="status"]').textContent,
    ).toContain("Offline");
  });

  it("replaces generic Public media actions with independent, duration-bound publication consent", () => {
    const grants: any[] = [];
    let revokes = 0;
    fixture.componentInstance.ordinaryMediaPublicationConsentGrant.subscribe(value => grants.push(value));
    fixture.componentInstance.ordinaryMediaPublicationConsentRevoke.subscribe(() => { revokes += 1; });
    fixture.componentRef.setInput("displayMode", "pair_media");
    fixture.componentRef.setInput("online", false);
    fixture.componentRef.setInput("ordinaryMediaAuthority", "public");
    fixture.componentRef.setInput("ordinaryMediaE2eeReady", true);
    fixture.componentRef.setInput("ordinaryMediaPublicationConsent", publicationConsentState());
    fixture.componentRef.setInput("capabilities", [{
      capability: "ordinary_media", label: "Ordinary Audio/Video", sensitive: false,
      state: "revoked", requestId: null,
    }]);
    fixture.detectChanges();

    const consentPanel = fixture.debugElement.query(
      By.directive(PublicPairMediaPublicationConsentPanelComponent),
    );
    expect(consentPanel).not.toBeNull();
    expect(fixture.nativeElement.textContent).toContain("Meine Medienfreigabe");
    expect(fixture.nativeElement.textContent)
      .toContain("Der verschlüsselte Public-Pair-Medienpfad ist bereit");
    expect(fixture.nativeElement.textContent).not.toContain("Pausieren");
    expect(fixture.nativeElement.textContent).not.toContain("Widerrufen");
    (consentPanel.componentInstance as PublicPairMediaPublicationConsentPanelComponent)
      .grant.emit({ kind: "timed", durationMs: 900_000 });
    (consentPanel.componentInstance as PublicPairMediaPublicationConsentPanelComponent).revoke.emit();
    expect(grants).toEqual([{ kind: "timed", durationMs: 900_000 }]);
    expect(revokes).toBe(1);

    const disabledMedia = fixture.debugElement.query(By.directive(WebrtcMediaPanelComponent));
    expect(disabledMedia).not.toBeNull();
    expect((disabledMedia.componentInstance as WebrtcMediaPanelComponent).e2eeProtected).toBe(true);
    expect([...disabledMedia.nativeElement.querySelectorAll("button")]
      .every((button: HTMLButtonElement) => button.disabled)).toBe(true);

    fixture.componentRef.setInput("capabilities", [{
      capability: "ordinary_media", label: "Ordinary Audio/Video", sensitive: false,
      state: "authoritatively_active", requestId: null,
    }]);
    fixture.componentRef.setInput("ordinaryMediaPublicationConsent", publicationConsentState({
      status: "granted", term: { kind: "session" }, expiresAtMs: Date.now() + 60_000,
    }));
    fixture.componentRef.setInput("ordinaryMediaCaptureEnabled", true);
    fixture.componentRef.setInput("ordinaryMediaVideoCaptureEnabled", true);
    fixture.detectChanges();
    const panel = fixture.debugElement.query(By.directive(WebrtcMediaPanelComponent));
    expect((panel.componentInstance as WebrtcMediaPanelComponent).publicPair).toBe(true);
    expect((panel.componentInstance as WebrtcMediaPanelComponent).e2eeProtected).toBe(true);
    expect(panel.nativeElement.textContent).toContain("sitzungs- und verbindungsgebundenen Schlüsseln");

    fixture.componentRef.setInput("ordinaryMediaE2eeReady", false);
    fixture.detectChanges();
    expect(fixture.nativeElement.textContent)
      .toContain("Der verschlüsselte Public-Pair-Medienpfad ist noch nicht bereit");
    expect(fixture.nativeElement.textContent).not.toContain("wird vorbereitet");
  });

  it("mounts reconciliation only after the Hub capability becomes authoritative", () => {
    capabilities.push({
      capability: "speech_reconciliation",
      label: "Offline-Sprachabstimmung",
      sensitive: true,
      state: "sent_to_hub",
      requestId: "request-pending",
      scope: {
        direction: "sender_to_receiver",
        dataClass: "audio, transcript",
        purpose: "speech_reconciliation",
        retentionLabel: "2 Stunde(n)",
        trainerLocation: "Kein Training freigegeben",
        e2eeMode: "strict_e2ee",
        ordinaryFallback: "Ordinary Audio bleibt verfügbar",
        grantLabel: "Roh-Audio, Dataset-Import, Training nicht freigegeben",
      },
    });
    fixture.componentRef.setInput("capabilities", [...capabilities]);
    fixture.detectChanges();
    expect(fixture.nativeElement.querySelector("app-speech-reconciliation-panel")).toBeNull();

    capabilities[capabilities.length - 1].state = "authoritatively_active";
    capabilities[capabilities.length - 1].requestId = null;
    fixture.componentRef.setInput("capabilities", [...capabilities]);
    fixture.detectChanges();
    const panel = fixture.debugElement.query(By.directive(SpeechReconciliationPanelComponent));
    expect(panel).not.toBeNull();
    expect((panel.componentInstance as SpeechReconciliationPanelComponent).hubAuthorized).toBe(false);
    expect(fixture.nativeElement.textContent).toContain("speech_reconciliation");
    expect(fixture.nativeElement.textContent).toContain("Training nicht freigegeben");

    fixture.componentRef.setInput("speechReconciliationHubAuthorized", true);
    fixture.detectChanges();
    expect((fixture.debugElement.query(By.directive(SpeechReconciliationPanelComponent))
      .componentInstance as SpeechReconciliationPanelComponent).hubAuthorized).toBe(true);
  });

  it("mounts explicit ordinary capture controls only after authoritative activation", () => {
    capabilities.push({
      capability: "ordinary_media", label: "Ordinary Audio/Video", sensitive: false,
      state: "sent_to_hub", requestId: "ordinary-pending",
    });
    fixture.componentRef.setInput("capabilities", [...capabilities]);
    fixture.detectChanges();
    expect(fixture.debugElement.query(By.directive(WebrtcMediaPanelComponent))).toBeNull();

    capabilities[capabilities.length - 1] = {
      ...capabilities[capabilities.length - 1], state: "authoritatively_active", requestId: null,
    };
    fixture.componentRef.setInput("capabilities", [...capabilities]);
    fixture.componentRef.setInput("ordinaryMediaCaptureEnabled", true);
    fixture.componentRef.setInput("ordinaryMediaVideoCaptureEnabled", true);
    fixture.componentRef.setInput("ordinaryAudioState", {
      status: "active", trackId: "mic-a", deviceLabelVisible: true, reasonCode: null,
    });
    fixture.detectChanges();
    const panel = fixture.debugElement.query(By.directive(WebrtcMediaPanelComponent));
    expect(panel).not.toBeNull();
    expect(panel.nativeElement.textContent).toContain("Mikrofon");
    expect(panel.nativeElement.textContent).toContain("Kamera freigeben");
    expect(panel.nativeElement.textContent).toContain("Bildschirm freigeben");

    let cameraStarts = 0;
    fixture.componentInstance.ordinaryCameraStart.subscribe(() => { cameraStarts += 1; });
    (panel.componentInstance as WebrtcMediaPanelComponent).startCamera.emit();
    expect(cameraStarts).toBe(1);
  });

  it("projects only Hub-authorized ordinary media controls in the Pair-Dev display mode", () => {
    capabilities.push({
      capability: "ordinary_media", label: "Ordinary Audio/Video", sensitive: false,
      state: "authoritatively_active", requestId: null,
    });
    fixture.componentRef.setInput("displayMode", "pair_media");
    fixture.componentRef.setInput("capabilities", [...capabilities]);
    fixture.componentRef.setInput("ordinaryMediaCaptureEnabled", true);
    fixture.componentRef.setInput("ordinaryMediaVideoCaptureEnabled", true);
    fixture.componentRef.setInput("computeVisible", true);
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain("Audio und Video für Pair Dev");
    expect(fixture.nativeElement.querySelectorAll("[data-capability]")).toHaveLength(1);
    expect(fixture.nativeElement.querySelector('[data-capability="ordinary_media"]')).not.toBeNull();
    expect(fixture.debugElement.query(By.directive(WebrtcMediaPanelComponent))).not.toBeNull();
    expect(fixture.nativeElement.querySelector("app-pair-compute-contract-panel")).not.toBeNull();
    expect(fixture.nativeElement.querySelector("app-semantic-receiver-path-panel")).toBeNull();
    expect(fixture.nativeElement.querySelector("app-speech-evidence-consent-panel")).toBeNull();
    expect(fixture.nativeElement.querySelector("app-semantic-debug-host")).toBeNull();
    expect((fixture.debugElement.query(By.directive(SemanticRemoteAudioComponent))
      .componentInstance as SemanticRemoteAudioComponent).includeSfu).toBe(false);
  });

  it("requires and emits one explicit Pair adapter selection", () => {
    const emitted: any[] = [];
    fixture.componentInstance.intent.subscribe(value => emitted.push(value));
    fixture.componentRef.setInput("speechAdapters", [{
      adapterId: "speech-adapter-a",
      direction: "sender_to_receiver",
      label: "speech-adapter-a · base-a",
      expiresAtMs: Date.now() + 60_000,
    }]);
    fixture.detectChanges();
    const article = fixture.nativeElement.querySelector('[data-capability="adapter_activation"]');
    const select = article.querySelector("select") as HTMLSelectElement;
    const activate = article.querySelector("button") as HTMLButtonElement;
    expect(activate.disabled).toBe(true);
    expect(emitted).toEqual([]);

    select.value = "sender_to_receiver\u001fspeech-adapter-a";
    select.dispatchEvent(new Event("change"));
    fixture.detectChanges();
    expect(activate.disabled).toBe(false);
    activate.click();

    expect(emitted).toEqual([expect.objectContaining({
      capability: "adapter_activation",
      desired: "activate",
      adapterId: "speech-adapter-a",
      direction: "sender_to_receiver",
    })]);
  });
});
