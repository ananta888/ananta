"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.AnantaSecretStore = exports.DEFAULT_SECRET_STORAGE_KEY = void 0;
exports.DEFAULT_SECRET_STORAGE_KEY = "ananta.auth.token";
class AnantaSecretStore {
    secrets;
    defaultKey;
    constructor(secrets, defaultKey = exports.DEFAULT_SECRET_STORAGE_KEY) {
        this.secrets = secrets;
        this.defaultKey = defaultKey;
    }
    async readToken(key = this.defaultKey) {
        const value = await this.secrets.get(key);
        if (!value) {
            return null;
        }
        return value;
    }
    async storeToken(value, key = this.defaultKey) {
        await this.secrets.store(key, value);
    }
    async clearToken(key = this.defaultKey) {
        await this.secrets.delete(key);
    }
}
exports.AnantaSecretStore = AnantaSecretStore;
//# sourceMappingURL=secretStore.js.map