# Model Architectures: Parallel Decision

**Track:** PDT · **Stand:** 2026-10-14

## Overview

This document describes the model architectures used for parallel decision-making in the Ananta system. The focus is on how multiple tool plans are evaluated concurrently without generating full tool-call JSON, leveraging llama.cpp's parallel-decision endpoint.

## Architectural Components

### 1. Decision Schema Builder

The `parallel_decision` module constructs a lightweight decision schema that defines:
- Candidate tools and their parameters
- Evaluation criteria (latency, accuracy, resource usage)
- Output format for the decision result

### 2. Parallel Execution Layer

Multiple tool plans are evaluated in parallel using:
- **CPU profile:** Sequential evaluation with tokenizer-boundary optimization
- **CUDA profile:** Batched inference with GPU memory management

Both profiles share the same decision logic but differ in execution strategy.

### 3. Router Adapter

The tiny-router adapter wraps `ananta_contracts.tool_decision` and:
- Builds the decision schema per request
- Reads the response from llama.cpp's parallel-decision endpoint
- Handles Hub-specific HTTP runtime concerns

## Design Principles

| Principle | Application |
|-----------|-------------|
| **SRP** | Audit, Contract, Adapter, and Execution are separated into distinct modules |
| **OCP** | New tool plans extend the existing `/v1/decision` path without modification |
| **DIP** | `argument_validator` is injectable for testability |

## Performance Profiles

### CPU Profile (`parallel-decision-profile.cpu.v1.json`)
- Sequential evaluation with tokenizer-boundary optimization
- Suitable for low-latency, single-tool decisions
- Hardware: Linux x86_64 with glibc 2.39

### CUDA Profile (`parallel-decision-profile.cuda.v1.json`)
- Batched inference with GPU memory management
- Suitable for multi-tool parallel evaluation
- Requires CUDA-compatible hardware

## Testing Strategy

Tests are located in `tests/self_development/` and cover:
- Decision schema construction
- Parallel execution correctness
- Router adapter integration
- Profile-specific behavior (CPU vs. CUDA)

Run tests with:
```bash
python -m pytest -q --confcutdir=tests/self_development -o addopts= -o cache_dir=/tmp/ananta-self-dev-cache tests/self_development
```
