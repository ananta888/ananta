# AGENTS.md

## Purpose

This file defines the core architectural principles and development rules for the Ananta project.

All AI agents, automation tools, and contributors must follow these rules when modifying the repository.

The goal is to evolve the system without breaking its core architecture.

---

# Core Architecture

Ananta uses a **hub–worker orchestration architecture**.

This is the fundamental design of the system and must not be changed.

## Hub (Control Plane)

The hub is the **central control plane** of the system.

Responsibilities:

- task orchestration
- delegation of work
- routing to workers
- policy and governance decisions
- ownership of the task queue
- coordination of execution flows

The hub **does not perform the actual work if it is possible to delegate it otherwise hub is the worker**.

It coordinates work performed by workers.

## Workers

Workers execute tasks that are **delegated by the hub**.

Workers may:

- run LLM operations
- generate or modify code
- execute tools
- perform analysis
- produce artifacts

Workers must **not orchestrate other workers**.

Workers must **not exchange tasks directly**.

All orchestration flows through the hub.

---

# Task Control Model

All work in the system flows through the **hub task system**.


User / External Trigger
↓
Hub
↓
Task Queue
↓
Workers


Workers must never create independent orchestration loops.

The hub always remains the owner of:

- the task queue
- delegation logic
- workflow decisions

---

# Container Model

Each hub and worker instance runs in its **own Docker container**.

This model is part of the architecture and must be preserved.

New features must:

- respect container boundaries
- avoid implicit shared state
- avoid assumptions about a single process environment

Execution environments must remain reproducible.

---

# Evolution Rules

The system must evolve **without breaking the existing architecture**.

Required principles:

- additive changes instead of breaking changes
- backward compatibility where possible
- gradual migration strategies
- no big-bang refactors
- preserve existing task flows

New capabilities must integrate with the existing hub–worker structure.

---

# API Compatibility

Existing APIs should remain stable.

Prefer:

- new endpoints
- optional fields
- compatibility adapters
- feature flags

Avoid:

- removing existing fields
- renaming endpoints without migration
- forcing immediate client updates

---

# Separation of Responsibilities

Clear responsibility boundaries must be maintained.

Hub:

- planning
- routing
- governance
- orchestration
- policy enforcement

Workers:

- execution of delegated tasks

These roles must remain clearly separated.

---

# Security Principles

When extending the system:

- prefer **least privilege**
- avoid implicit trust between components
- make decisions observable and auditable
- avoid uncontrolled execution paths

---

# Engineering Principles

All AI agents, automation tools, and contributors must apply the **SOLID principles** whenever code is generated, changed, extended, refactored, or analyzed.

## Single Responsibility Principle (SRP)

- Each class, module, and function should have exactly one clear responsibility.
- Avoid god classes, overloaded utility modules, and mixed concerns.

## Open/Closed Principle (OCP)

- Prefer extension over repeated modification of core logic.
- Use interfaces, composition, strategies, policies, adapters, or new implementations instead of repeatedly patching central behavior.

## Liskov Substitution Principle (LSP)

- Derived or alternative implementations must be safely substitutable for their abstractions.
- Do not introduce hidden side effects, stronger preconditions, or broken contracts.

## Interface Segregation Principle (ISP)

- Prefer small, focused interfaces over broad, catch-all interfaces.
- No consumer should depend on methods it does not need.

## Dependency Inversion Principle (DIP)

- Depend on abstractions, not concrete implementations.
- Prefer dependency injection, ports/adapters, and loose coupling.

## Additional Mandatory Rules

- Separate business logic, infrastructure, persistence, API, and configuration concerns cleanly.
- Prefer composition over inheritance.
- Avoid unnecessary global state and hard coupling.
- Write testable code with clear seams and explicit interfaces.
- Use precise, domain-meaningful names for classes, methods, and variables.
- Keep functions small and understandable.
- Avoid duplication, but not at the expense of readability.
- Deliver maintainable, extensible, and understandable solutions rather than merely functional ones.
- When working with existing code, explicitly call out SOLID violations that are being preserved, introduced, or cleaned up.
- If a requested change conflicts with SOLID, name the conflict explicitly and propose a cleaner alternative.

## Expected Output Behavior For Agents

When proposing or implementing code changes:

- do not produce merely working code; produce maintainable structure
- briefly justify important architecture or structure decisions
- explain, where relevant, which SOLID principle is being protected by the chosen design
- refactor unsound existing code toward a more SOLID-compliant form when practical
- prefer simple, clear, production-grade solutions over clever but fragile constructions

Before finalizing a proposed change, explicitly check for:

- SRP violations
- overly strong coupling
- missing abstractions
- interfaces that are too broad
- poor substitutability of implementations
- hidden side effects
- structures that are hard to test

If one of these issues exists:

1. name the problem
2. name the SOLID principle involved
3. propose a better structure
4. only then provide the final code

---

# Long-Term Direction

The long-term user experience should allow **goal-based interaction**.

Example flow:


Goal
↓
Plan
↓
Tasks
↓
Execution
↓
Verification
↓
Artifacts / Results


However, internally the **hub-controlled orchestration model remains unchanged**.

The hub continues to manage:

- planning
- task delegation
- policy decisions
- verification

---

# Development Guidance for Agents

When generating code or tasks:

- respect the hub–worker architecture
- do not introduce worker-to-worker orchestration
- avoid redesigning the core architecture
- prefer incremental improvements
- keep changes small and reviewable

If a change would alter the architecture, it must be explicitly justified and discussed before implementation.

---

# Summary

The following rule overrides all others:

**The hub remains the central control plane and owner of the task system.
Workers execute delegated work only.**
