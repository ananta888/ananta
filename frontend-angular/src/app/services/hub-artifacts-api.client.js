var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { HubApiCoreService } from './hub-api-core.service';
let HubArtifactsApiClient = class HubArtifactsApiClient {
    constructor() {
        this.core = inject(HubApiCoreService);
    }
    listArtifacts(baseUrl, token) {
        return this.core.get(`${baseUrl}/artifacts`, baseUrl, token, true);
    }
    getArtifact(baseUrl, artifactId, token) {
        return this.core.get(`${baseUrl}/artifacts/${artifactId}`, baseUrl, token, true);
    }
    extractArtifact(baseUrl, artifactId, token) {
        return this.core.post(`${baseUrl}/artifacts/${artifactId}/extract`, {}, baseUrl, token);
    }
    uploadArtifact(baseUrl, file, collectionName, token) {
        const form = new FormData();
        form.append('file', file);
        if (collectionName?.trim()) {
            form.append('collection_name', collectionName.trim());
        }
        return this.core.post(`${baseUrl}/artifacts/upload`, form, baseUrl, token, false, 120000);
    }
};
HubArtifactsApiClient = __decorate([
    Injectable({ providedIn: 'root' })
], HubArtifactsApiClient);
export { HubArtifactsApiClient };
//# sourceMappingURL=hub-artifacts-api.client.js.map