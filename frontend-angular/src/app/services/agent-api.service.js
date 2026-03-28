var __decorate = (this && this.__decorate) || function (decorators, target, key, desc) {
    var c = arguments.length, r = c < 3 ? target : desc === null ? desc = Object.getOwnPropertyDescriptor(target, key) : desc, d;
    if (typeof Reflect === "object" && typeof Reflect.decorate === "function") r = Reflect.decorate(decorators, target, key, desc);
    else for (var i = decorators.length - 1; i >= 0; i--) if (d = decorators[i]) r = (c < 3 ? d(r) : c > 3 ? d(target, key, r) : d(target, key)) || r;
    return c > 3 && r && Object.defineProperty(target, key, r), r;
};
import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { timeout, retry, map } from 'rxjs';
import { AgentDirectoryService } from './agent-directory.service';
import { UserAuthService } from './user-auth.service';
let AgentApiService = class AgentApiService {
    constructor() {
        this.http = inject(HttpClient);
        this.dir = inject(AgentDirectoryService);
        this.userAuth = inject(UserAuthService);
        this.timeoutMs = 15000;
        this.retryCount = 2;
    }
    getHeaders(baseUrl, token) {
        let headers = new HttpHeaders();
        if (!token) {
            const hub = this.dir.list().find(a => a.role === 'hub');
            if (hub && baseUrl.startsWith(hub.url) && this.userAuth.token) {
                token = this.userAuth.token;
            }
            else {
                const agent = this.dir.list().find(a => baseUrl.startsWith(a.url));
                token = agent?.token;
            }
        }
        if (token) {
            headers = headers.set('Authorization', `Bearer ${token}`);
        }
        return { headers };
    }
    unwrapResponse(obs) {
        return obs.pipe(map((response) => {
            if (response && typeof response === 'object' && 'data' in response && 'status' in response) {
                return response.data;
            }
            return response;
        }));
    }
    health(baseUrl, token) {
        return this.unwrapResponse(this.http.get(`${baseUrl}/health`, this.getHeaders(baseUrl, token)).pipe(timeout(5000), retry(this.retryCount)));
    }
    ready(baseUrl, token) {
        return this.unwrapResponse(this.http.get(`${baseUrl}/ready`, this.getHeaders(baseUrl, token)).pipe(timeout(5000), retry(this.retryCount)));
    }
    getConfig(baseUrl, token) {
        return this.unwrapResponse(this.http.get(`${baseUrl}/config`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
    }
    setConfig(baseUrl, cfg, token) {
        return this.unwrapResponse(this.http.post(`${baseUrl}/config`, cfg, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
    }
    propose(baseUrl, body, token) {
        return this.unwrapResponse(this.http.post(`${baseUrl}/step/propose`, body, this.getHeaders(baseUrl, token)).pipe(timeout(60000))); // LLM calls take longer
    }
    execute(baseUrl, body, token) {
        return this.unwrapResponse(this.http.post(`${baseUrl}/step/execute`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
    }
    logs(baseUrl, limit = 200, taskId, token) {
        const q = new URLSearchParams({ limit: String(limit), ...(taskId ? { task_id: taskId } : {}) });
        return this.unwrapResponse(this.http.get(`${baseUrl}/logs?${q.toString()}`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
    }
    rotateToken(baseUrl, token) {
        return this.unwrapResponse(this.http.post(`${baseUrl}/rotate-token`, {}, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs)));
    }
    getMetrics(baseUrl, token) {
        // Metrics endpoint returns raw text, not JSON, so no unwrapping needed
        return this.http.get(`${baseUrl}/metrics`, {
            headers: this.getHeaders(baseUrl, token).headers,
            responseType: 'text'
        }).pipe(timeout(this.timeoutMs));
    }
    llmGenerate(baseUrl, prompt, config, token, options) {
        const body = { prompt, config };
        if (options) {
            if (options.history)
                body.history = options.history;
            if (options.context)
                body.context = options.context;
            if (options.tool_calls)
                body.tool_calls = options.tool_calls;
            if (options.confirm_tool_calls)
                body.confirm_tool_calls = options.confirm_tool_calls;
        }
        return this.unwrapResponse(this.http.post(`${baseUrl}/llm/generate`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
    }
    sgptExecute(baseUrl, prompt, options = [], token, useHybridContext = false, backend) {
        const body = { prompt, options, use_hybrid_context: useHybridContext };
        if (backend)
            body.backend = backend;
        return this.unwrapResponse(this.http.post(`${baseUrl}/api/sgpt/execute`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
    }
    sgptContext(baseUrl, query, token, includeContextText = true) {
        const body = { query, include_context_text: includeContextText };
        return this.unwrapResponse(this.http.post(`${baseUrl}/api/sgpt/context`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
    }
    sgptSource(baseUrl, sourcePath, token) {
        const body = { source_path: sourcePath };
        return this.unwrapResponse(this.http.post(`${baseUrl}/api/sgpt/source`, body, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
    }
    sgptBackends(baseUrl, token) {
        return this.unwrapResponse(this.http.get(`${baseUrl}/api/sgpt/backends`, this.getHeaders(baseUrl, token)).pipe(timeout(120000)));
    }
    getLlmHistory(baseUrl, token) {
        return this.unwrapResponse(this.http.get(`${baseUrl}/llm/history`, this.getHeaders(baseUrl, token)).pipe(timeout(this.timeoutMs), retry(this.retryCount)));
    }
};
AgentApiService = __decorate([
    Injectable({ providedIn: 'root' })
], AgentApiService);
export { AgentApiService };
//# sourceMappingURL=agent-api.service.js.map