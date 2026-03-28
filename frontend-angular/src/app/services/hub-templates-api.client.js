var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { HubApiCoreService } from './hub-api-core.service';
let HubTemplatesApiClient = class HubTemplatesApiClient {
    constructor() {
        this.core = inject(HubApiCoreService);
    }
    listTemplates(baseUrl, token) { return this.core.get(`${baseUrl}/templates`, baseUrl, token, true); }
    createTemplate(baseUrl, tpl, token) { return this.core.post(`${baseUrl}/templates`, tpl, baseUrl, token); }
    updateTemplate(baseUrl, id, patch, token) { return this.core.patch(`${baseUrl}/templates/${id}`, patch, baseUrl, token); }
    deleteTemplate(baseUrl, id, token) { return this.core.delete(`${baseUrl}/templates/${id}`, baseUrl, token); }
};
HubTemplatesApiClient = __decorate([
    Injectable({ providedIn: 'root' })
], HubTemplatesApiClient);
export { HubTemplatesApiClient };
//# sourceMappingURL=hub-templates-api.client.js.map