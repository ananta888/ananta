"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.AnantaBackendClient = exports.FetchHttpTransport = void 0;
class FetchHttpTransport {
    async request(request) {
        const controller = new AbortController();
        const timeoutHandle = setTimeout(() => controller.abort(), request.timeoutMs);
        try {
            const response = await fetch(request.url, {
                method: request.method,
                headers: request.headers,
                body: request.body ?? undefined,
                signal: controller.signal
            });
            const body = await response.text();
            return {
                status: response.status,
                body
            };
        }
        finally {
            clearTimeout(timeoutHandle);
        }
    }
}
exports.FetchHttpTransport = FetchHttpTransport;
function mapStatusToState(statusCode, parseError) {
    if (parseError) {
        return "malformed_response";
    }
    if (statusCode >= 200 && statusCode < 300) {
        return "healthy";
    }
    if (statusCode === 401) {
        return "auth_failed";
    }
    if (statusCode === 403) {
        return "policy_denied";
    }
    if (statusCode === 404 || statusCode === 422) {
        return "capability_missing";
    }
    if (statusCode === 408 || statusCode === 429) {
        return "backend_timeout";
    }
    if (statusCode === 409) {
        return "stale_state";
    }
    if (statusCode >= 500) {
        return "backend_unreachable";
    }
    return "unknown_error";
}
function isRetriableState(state) {
    return state === "backend_timeout" || state === "backend_unreachable";
}
function metadataPayload(metadata) {
    const payload = {
        operation_preset: String(metadata.operationPreset || "").trim(),
        command_id: String(metadata.commandId || "").trim(),
        profile_id: String(metadata.profileId || "").trim(),
        runtime_target: String(metadata.runtimeTarget || "").trim()
    };
    const mode = String(metadata.mode || "").trim();
    if (mode.length > 0) {
        payload.mode = mode;
    }
    return payload;
}
class AnantaBackendClient {
    settings;
    transport;
    constructor(settings, transport = new FetchHttpTransport()) {
        this.settings = settings;
        this.transport = transport;
    }
    async requestJson(method, path, payload) {
        const url = `${this.settings.baseUrl.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
        const headers = {
            Accept: "application/json",
            "Content-Type": "application/json"
        };
        if (this.settings.authToken) {
            headers.Authorization = `Bearer ${this.settings.authToken}`;
        }
        const body = payload === undefined ? null : JSON.stringify(payload);
        let statusCode = null;
        let rawBody = "";
        try {
            const response = await this.transport.request({
                method,
                url,
                headers,
                body,
                timeoutMs: this.settings.timeoutMs
            });
            statusCode = response.status;
            rawBody = response.body ?? "";
        }
        catch {
            return {
                ok: false,
                statusCode: null,
                state: "backend_unreachable",
                data: null,
                error: "request_failed:backend_unreachable",
                retriable: true
            };
        }
        let parseError = false;
        let parsed = null;
        if (rawBody.trim().length > 0) {
            try {
                parsed = JSON.parse(rawBody);
            }
            catch {
                parseError = true;
            }
        }
        const state = mapStatusToState(statusCode, parseError);
        return {
            ok: state === "healthy",
            statusCode,
            state,
            data: parsed,
            error: state === "healthy" ? null : `request_failed:${state}`,
            retriable: isRetriableState(state)
        };
    }
    getHealth() {
        return this.requestJson("GET", "/health");
    }
    getCapabilities() {
        return this.requestJson("GET", "/capabilities");
    }
    getDashboardReadModel() {
        return this.requestJson("GET", "/dashboard/read-model?benchmark_task_kind=analysis&include_task_snapshot=1");
    }
    getAssistantReadModel() {
        return this.requestJson("GET", "/assistant/read-model");
    }
    listProviders() {
        return this.requestJson("GET", "/providers");
    }
    listProviderCatalog() {
        return this.requestJson("GET", "/providers/catalog");
    }
    getLlmBenchmarks(taskKind = "analysis", topN = 3) {
        const safeTaskKind = encodeURIComponent(String(taskKind || "analysis").trim() || "analysis");
        const safeTopN = Math.max(1, Math.trunc(topN));
        return this.requestJson("GET", `/llm/benchmarks?task_kind=${safeTaskKind}&top_n=${safeTopN}`);
    }
    listGoals() {
        return this.requestJson("GET", "/goals");
    }
    listTasks() {
        return this.requestJson("GET", "/tasks");
    }
    getTask(taskId) {
        return this.requestJson("GET", `/tasks/${encodeURIComponent(String(taskId || "").trim())}`);
    }
    getTaskLogs(taskId) {
        return this.requestJson("GET", `/tasks/${encodeURIComponent(String(taskId || "").trim())}/logs`);
    }
    listArtifacts() {
        return this.requestJson("GET", "/artifacts");
    }
    getArtifact(artifactId) {
        return this.requestJson("GET", `/artifacts/${encodeURIComponent(String(artifactId || "").trim())}`);
    }
    listApprovals() {
        return this.requestJson("GET", "/approvals");
    }
    approveApproval(approvalId, comment = "") {
        const normalizedComment = String(comment || "").trim();
        return this.requestJson("POST", `/approvals/${encodeURIComponent(String(approvalId || "").trim())}/approve`, {
            comment: normalizedComment
        });
    }
    rejectApproval(approvalId, comment = "") {
        const normalizedComment = String(comment || "").trim();
        return this.requestJson("POST", `/approvals/${encodeURIComponent(String(approvalId || "").trim())}/reject`, {
            comment: normalizedComment
        });
    }
    getGoal(goalId) {
        return this.requestJson("GET", `/goals/${encodeURIComponent(String(goalId || "").trim())}`);
    }
    getAuditLogs(limit = 30, offset = 0) {
        const safeLimit = Math.max(1, Math.trunc(limit));
        const safeOffset = Math.max(0, Math.trunc(offset));
        return this.requestJson("GET", `/api/system/audit-logs?limit=${safeLimit}&offset=${safeOffset}`);
    }
    listRepairs() {
        return this.requestJson("GET", "/repairs");
    }
    getRepairSession(sessionId) {
        return this.requestJson("GET", `/repairs/${encodeURIComponent(String(sessionId || "").trim())}`);
    }
    getConfig() {
        return this.requestJson("GET", "/config");
    }
    submitGoal(goalText, context, metadata) {
        return this.requestJson("POST", "/goals", {
            goal_text: String(goalText || "").trim(),
            context,
            ...metadataPayload(metadata)
        });
    }
    analyzeContext(context, metadata, goalText) {
        return this.requestJson("POST", "/tasks/analyze", {
            goal_text: String(goalText || "").trim(),
            context,
            ...metadataPayload(metadata)
        });
    }
    reviewContext(context, metadata, goalText) {
        return this.requestJson("POST", "/tasks/review", {
            goal_text: String(goalText || "").trim(),
            context,
            ...metadataPayload(metadata)
        });
    }
    patchPlan(context, metadata, goalText) {
        return this.requestJson("POST", "/tasks/patch-plan", {
            goal_text: String(goalText || "").trim(),
            context,
            ...metadataPayload(metadata)
        });
    }
    createProjectNew(goalText, context, metadata) {
        return this.requestJson("POST", "/projects/new", {
            goal_text: String(goalText || "").trim(),
            context,
            ...metadataPayload(metadata)
        });
    }
    createProjectEvolve(goalText, context, metadata) {
        return this.requestJson("POST", "/projects/evolve", {
            goal_text: String(goalText || "").trim(),
            context,
            ...metadataPayload(metadata)
        });
    }
}
exports.AnantaBackendClient = AnantaBackendClient;
//# sourceMappingURL=backendClient.js.map