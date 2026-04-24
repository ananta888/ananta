export const DEFAULT_SECRET_STORAGE_KEY = "ananta.auth.token";

export interface SecretStorageLike {
  get(key: string): PromiseLike<string | undefined>;
  store(key: string, value: string): PromiseLike<void>;
  delete(key: string): PromiseLike<void>;
}

export class AnantaSecretStore {
  public constructor(
    private readonly secrets: SecretStorageLike,
    private readonly defaultKey: string = DEFAULT_SECRET_STORAGE_KEY
  ) {}

  public async readToken(key: string = this.defaultKey): Promise<string | null> {
    const value = await this.secrets.get(key);
    if (!value) {
      return null;
    }
    return value;
  }

  public async storeToken(value: string, key: string = this.defaultKey): Promise<void> {
    await this.secrets.store(key, value);
  }

  public async clearToken(key: string = this.defaultKey): Promise<void> {
    await this.secrets.delete(key);
  }
}
