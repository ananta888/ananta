import { TestBed } from "@angular/core/testing";
import { of } from "rxjs";

import { HubApiCoreService } from "./hub-api-core.service";
import {
  SemanticComputeContractApiService,
  parseSemanticComputeContract,
} from "./semantic-compute-contract-api.service";

function contract(patch: Record<string, unknown> = {}) {
  return {
    contract_id: "semantic-contract-a",
    session_id: "session-a",
    room_id: null,
    epoch: 3,
    revision: 2,
    digest: "a".repeat(64),
    status: "active",
    profile: "balanced",
    quality_level: "standard",
    security_mode: "strict_e2ee",
    consent_version: 4,
    policy_version: "semantic-compute-v1",
    delay_ms: 5_000,
    roles: { primary: ["worker-a"], validator: ["worker-b"] },
    task_types: ["visual_extract"],
    max_artifact_bytes: 1_048_576,
    deadline_ms: 5_000,
    expires_at_ms: Date.now() + 60_000,
    reason_code: "activate_accepted",
    ...patch,
  };
}

describe("SemanticComputeContractApiService", () => {
  const request = vi.fn();
  let service: SemanticComputeContractApiService;

  beforeEach(() => {
    request.mockReset();
    TestBed.configureTestingModule({
      providers: [
        SemanticComputeContractApiService,
        { provide: HubApiCoreService, useValue: { request } },
      ],
    });
    service = TestBed.inject(SemanticComputeContractApiService);
  });

  it("reads the Hub-authoritative list and rejects malformed read models", async () => {
    request.mockReturnValue(
      of({ ok: true, contracts: { items: [contract()] } }),
    );
    const values = await new Promise<readonly unknown[]>((resolve, reject) =>
      service
        .list("http://hub.test", "session-a", 3, "grant-subscribe")
        .subscribe({ next: resolve, error: reject }),
    );
    expect(values).toHaveLength(1);
    expect(values[0]).toMatchObject({
      status: "active",
      revision: 2,
      profile: "balanced",
    });
    expect(request.mock.calls[0][1]).toContain("session_id=session-a&epoch=3");
    expect(() =>
      parseSemanticComputeContract(contract({ delay_ms: 20_001 })),
    ).toThrow("semantic_compute_integer_invalid");
  });

  it("uses idempotency and revision preconditions for mutations", () => {
    request.mockReturnValue(
      of({ contract: contract({ revision: 3, status: "fallback" }) }),
    );
    service
      .mutate(
        "http://hub.test",
        "semantic-contract-a",
        "fallback",
        {
          sessionId: "session-a",
          epoch: 3,
          expectedRevision: 2,
          consentVersion: 4,
        },
        "semantic-compute-fallback-1",
        "grant-publish",
      )
      .subscribe();
    expect(request).toHaveBeenCalledWith(
      "POST",
      "http://hub.test/v1/semantic-media/contracts/semantic-contract-a/fallback",
      "http://hub.test",
      expect.objectContaining({
        headers: {
          "Idempotency-Key": "semantic-compute-fallback-1",
          "If-Match": '"2"',
          "X-Semantic-Capability-Grant": "grant-publish",
        },
      }),
    );
  });

  it("uses current revision preconditions for Hub scheduling and parses authoritative leases", async () => {
    const issuedAt = Date.now();
    const expiresAt = issuedAt + 4_000;
    const signedLease = {
      schema: "ananta.semantic-task-lease.v1",
      lease_id: "lease-a",
      contract_id: "semantic-contract-a",
      contract_digest: "a".repeat(64),
      session_id: "session-a",
      epoch: 3,
      task_type: "visual_extract",
      role: "primary",
      executor_id: "http://semantic-worker:5000",
      audience: "alice",
      sequence_start: 0,
      sequence_end: 0,
      fencing_token: 1,
      resource_budget: {
        cpu_ms: 1_000,
        memory_bytes: 67_108_864,
        artifact_bytes: 65_536,
      },
      issued_at_ms: issuedAt,
      expires_at_ms: expiresAt,
      deadline_ms: 4_000,
      issuer: "hub",
      signature: {
        algorithm: "hmac-sha256",
        key_id: "semantic-task-lease-v1",
        value: "a".repeat(64),
      },
    };
    request.mockReturnValue(
      of({
        schedule: {
          leases: [
            {
              lease_id: "lease-a",
              contract_id: "semantic-contract-a",
              contract_digest: "a".repeat(64),
              session_id: "session-a",
              epoch: 3,
              task_type: "visual_extract",
              audience: "alice",
              role: "primary",
              executor_id: "http://semantic-worker:5000",
              sequence_start: 0,
              sequence_end: 0,
              fencing_token: 1,
              resource_budget: {
                cpu_ms: 1_000,
                memory_bytes: 67_108_864,
                artifact_bytes: 65_536,
              },
              status: "active",
              issued_at_ms: issuedAt,
              expires_at_ms: expiresAt,
              deadline_at_ms: expiresAt,
              version: 1,
              authoritative_source: "hub",
              task_lease: signedLease,
            },
          ],
        },
      }),
    );
    const active = parseSemanticComputeContract(contract());
    const values = await new Promise<readonly unknown[]>((resolve, reject) =>
      service
        .schedule(
          "http://hub.test",
          active,
          {
            sessionId: "session-a",
            epoch: 3,
            taskType: "visual_extract",
            audience: "alice",
            sequenceStart: 0,
            sequenceEnd: 0,
            deadlineEpochMs: Date.now() + 4_000,
            resourceBudget: {
              cpu_ms: 1_000,
              memory_bytes: 67_108_864,
              artifact_bytes: 65_536,
            },
          },
          "semantic-compute-schedule-1",
          "grant-compute",
        )
        .subscribe({ next: resolve, error: reject }),
    );
    expect(values).toEqual([
      expect.objectContaining({ lease_id: "lease-a", status: "active" }),
    ]);
    expect((values[0] as { task_lease: unknown }).task_lease).toEqual(
      expect.objectContaining({
        issuer: "hub",
        executor_id: "http://semantic-worker:5000",
        signature: {
          algorithm: "hmac-sha256",
          key_id: "semantic-task-lease-v1",
          value: "a".repeat(64),
        },
      }),
    );
    expect(
      JSON.stringify((values[0] as { task_lease: unknown }).task_lease),
    ).not.toContain("secret");
    expect(request.mock.calls[0][3]).toEqual(
      expect.objectContaining({
        headers: {
          "Idempotency-Key": "semantic-compute-schedule-1",
          "If-Match": '"2"',
          "X-Semantic-Capability-Grant": "grant-compute",
        },
      }),
    );
  });

  it("issues a purpose-bound Hub grant and sends it on protected reads", () => {
    request.mockReturnValue(
      of({
        grant: {
          grant_id: "grant-subscribe",
          subject_id: "alice",
          capability: "subscribe",
          scope_kind: "session",
          scope_id: "session-a",
          direction: "ingress",
          epoch: 3,
          expires_at: Date.now() / 1_000 + 300,
          revoked_at: null,
        },
      }),
    );
    service
      .issueCapabilityGrant(
        "http://hub.test",
        {
          sessionId: "session-a",
          epoch: 3,
          subjectId: "alice",
          capability: "subscribe",
          direction: "ingress",
          expiresAtMs: Date.now() + 300_000,
        },
        "semantic-grant-subscribe-1",
      )
      .subscribe();
    expect(request.mock.calls[0][3]).toEqual(
      expect.objectContaining({
        body: expect.objectContaining({
          subject_id: "alice",
          capability: "subscribe",
          scope_kind: "session",
          scope_id: "session-a",
          data_type: "application/vnd.ananta.semantic-media-control+json",
          purpose: "semantic_media_control",
        }),
        headers: { "Idempotency-Key": "semantic-grant-subscribe-1" },
      }),
    );
  });
});
