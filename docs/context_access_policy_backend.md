# Context Access Policy Backend Documentation

## Overview

The Context Access Policy (CAP) backend provides a granular, multi-layered security system for controlling access to context data (source code, secrets, logs, etc.) before it is sent to workers or model providers.

## Key Components

### 1. Policy Model (`worker/core/context_access_policy.py`)
Defines the `ContextAccessPolicy` and `ContextAccessRule` structures. Rules match context blocks by source type, path patterns, or sensitivity levels and define allowed/denied destinations.

### 2. Decision Engine (`agent/services/context_access_policy_service.py`)
The `ContextAccessPolicyService` evaluates policies against context blocks and destination contexts (worker/runtime/model). It supports:
- **Redaction**: Automatically removing secrets from content.
- **Summarization**: Providing only high-level summaries for cloud models.
- **Approval Overrides**: Emergency or manual overrides for blocked data.
- **Deterministic Hashing**: Ensuring decisions are cacheable and traceable.

### 3. Integration with Worker Selection
The `WorkerRuntimeSelectionService` filters candidate workers and runtimes based on their capability to handle the required context class.

## Operator Guidelines

### Defining Policies
Policies can be defined at the system, project, or task level. They follow a precedence order where task-level policies override project-level ones.

### Troubleshooting
- **Audit Logs**: All context access decisions are logged with reason codes (e.g., `cloud_blocked`, `secret_blocked`).
- **Validation**: Use `service.validate_policy(policy)` to check for unsafe rules (e.g., allowing secrets to cloud without approval).

## Traceability
Each `ContextBlock` in a `ContextEnvelope` carries an `access_decision` including the hash of the policy version and the destination parameters used for the decision.
