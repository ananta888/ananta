import { describe, expect, it } from "vitest";
import { containsSecretKey, redactSensitiveText } from "../src/runtime/redaction";

describe("redaction", () => {
  it("detects secret-like keys", () => {
    expect(containsSecretKey("authorizationToken")).toBe(true);
    expect(containsSecretKey("api_key")).toBe(true);
    expect(containsSecretKey("profileId")).toBe(false);
  });

  it("redacts common secret payloads", () => {
    const raw = "Authorization: Bearer abcdefghijkl token=topsecret password=hunter2";
    const redacted = redactSensitiveText(raw);
    expect(redacted).not.toContain("abcdefghijkl");
    expect(redacted).not.toContain("topsecret");
    expect(redacted).not.toContain("hunter2");
    expect(redacted).toContain("***");
  });

  it("redacts jwt-like values and api-key assignments", () => {
    const raw = "jwt=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.aaaaaaaa.bbbbbbbb api_key: abcdef1234567890";
    const redacted = redactSensitiveText(raw);
    expect(redacted).not.toContain("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9");
    expect(redacted).not.toContain("abcdef1234567890");
    expect(redacted).toContain("***jwt***");
    expect(redacted).toContain("api_key=***");
  });
});
