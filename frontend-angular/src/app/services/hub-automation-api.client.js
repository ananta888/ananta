var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { HubApiCoreService } from './hub-api-core.service';
let HubAutomationApiClient = class HubAutomationApiClient {
    constructor() {
        this.core = inject(HubApiCoreService);
    }
    getAutopilotStatus(baseUrl, token) { return this.core.get(`${baseUrl}/tasks/autopilot/status`, baseUrl, token, true); }
    startAutopilot(baseUrl, body, token) {
        return this.core.post(`${baseUrl}/tasks/autopilot/start`, body || {}, baseUrl, token);
    }
    stopAutopilot(baseUrl, token) { return this.core.post(`${baseUrl}/tasks/autopilot/stop`, {}, baseUrl, token); }
    tickAutopilot(baseUrl, token) { return this.core.post(`${baseUrl}/tasks/autopilot/tick`, {}, baseUrl, token); }
    getAutoPlannerStatus(baseUrl, token) { return this.core.get(`${baseUrl}/tasks/auto-planner/status`, baseUrl, token, false); }
    configureAutoPlanner(baseUrl, config, token) { return this.core.post(`${baseUrl}/tasks/auto-planner/configure`, config, baseUrl, token); }
    planGoal(baseUrl, body, token) {
        return this.core.post(`${baseUrl}/tasks/auto-planner/plan`, body, baseUrl, token, false, 60000);
    }
    analyzeTaskForFollowups(baseUrl, taskId, body, token) {
        return this.core.post(`${baseUrl}/tasks/auto-planner/analyze/${taskId}`, body || {}, baseUrl, token);
    }
    getTriggersStatus(baseUrl, token) { return this.core.get(`${baseUrl}/triggers/status`, baseUrl, token, false); }
    configureTriggers(baseUrl, config, token) { return this.core.post(`${baseUrl}/triggers/configure`, config, baseUrl, token); }
    testTrigger(baseUrl, body, token) { return this.core.post(`${baseUrl}/triggers/test`, body, baseUrl, token); }
};
HubAutomationApiClient = __decorate([
    Injectable({ providedIn: 'root' })
], HubAutomationApiClient);
export { HubAutomationApiClient };
//# sourceMappingURL=hub-automation-api.client.js.map