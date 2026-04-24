import { describe, expect, it } from "vitest";
import { AnantaSecretStore, SecretStorageLike } from "../src/runtime/secretStore";
import { ConfigurationReader, resolveRuntimeSettings } from "../src/runtime/settings";

class InMemorySecrets implements SecretStorageLike {
  private readonly values = new Map<string, string>();

  public async get(key: string): Promise<string | undefined> {
    return this.values.get(key);
  }

  public async store(key: string, value: string): Promise<void> {
    this.values.set(key, value);
  }

  public async delete(key: string): Promise<void> {
    this.values.delete(key);
  }
}

function makeConfig(values: Record<string, unknown>): ConfigurationReader {
  return {
    get<T>(key: string, defaultValue: T): T {
      if (!(key in values)) {
        return defaultValue;
      }
      return values[key] as T;
    }
  };
}

describe("settings resolution", () => {
  it("resolves valid profile with token-based auth", async () => {
    const secrets = new InMemorySecrets();
    await secrets.store("ananta.auth.token", "fixture-token");
    const secretStore = new AnantaSecretStore(secrets);
    const config = makeConfig({
      baseUrl: "http://localhost:8080",
      profileId: "default",
      runtimeTarget: "local",
      timeoutMs: 9000,
      "auth.mode": "session_token",
      "auth.secretStorageKey": "ananta.auth.token"
    });
    const resolved = await resolveRuntimeSettings(config, secretStore);
    expect(resolved.validationErrors).toEqual([]);
    expect(resolved.settings?.authToken).toBe("fixture-token");
    expect(resolved.settings?.baseUrl).toBe("http://localhost:8080");
  });

  it("fails when auth token is required but missing", async () => {
    const secretStore = new AnantaSecretStore(new InMemorySecrets());
    const config = makeConfig({
      baseUrl: "http://localhost:8080",
      profileId: "default",
      runtimeTarget: "local",
      timeoutMs: 8000,
      "auth.mode": "personal_token"
    });
    const resolved = await resolveRuntimeSettings(config, secretStore);
    expect(resolved.settings).toBeNull();
    expect(resolved.validationErrors).toContain("missing_auth_token");
  });

  it("fails invalid URL and invalid auth mode", async () => {
    const secretStore = new AnantaSecretStore(new InMemorySecrets());
    const config = makeConfig({
      baseUrl: "localhost:8080",
      profileId: "default",
      runtimeTarget: "local",
      timeoutMs: 8000,
      "auth.mode": "legacy_magic"
    });
    const resolved = await resolveRuntimeSettings(config, secretStore);
    expect(resolved.settings).toBeNull();
    expect(resolved.validationErrors).toContain("invalid_base_url:localhost:8080");
    expect(resolved.validationErrors).toContain("invalid_auth_mode:legacy_magic");
  });

  it("allows mode none without token", async () => {
    const secretStore = new AnantaSecretStore(new InMemorySecrets());
    const config = makeConfig({
      baseUrl: "https://ananta.example.org",
      profileId: "ops",
      runtimeTarget: "staging",
      timeoutMs: 8000,
      "auth.mode": "none"
    });
    const resolved = await resolveRuntimeSettings(config, secretStore);
    expect(resolved.validationErrors).toEqual([]);
    expect(resolved.settings?.authToken).toBeNull();
  });
});
