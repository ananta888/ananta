# Tool Contracts

Ananta tools follow a stable, hub-owned contract. Tools are extension points, not independent orchestrators.

## Contract Version

- Version: `v1`
- Catalog builder: `agent.tool_contracts.build_tool_contract_catalog`
- Source capability map: `agent.tool_capabilities.DEFAULT_TOOL_CAPABILITIES`

## Required Fields

Every tool contract includes:

- `name`
- `category`: `read`, `write` or `admin`
- `requires_admin`
- `mutates_state`
- `description`
- `input_contract`
- `output_contract`
- `audit`
- `security`

## Security Rules

- Unknown tools fail closed.
- Mutating tools require admin unless an explicit policy says otherwise.
- Allowlist and denylist checks run before tool execution.
- Hub owns validation, routing, audit and orchestration.
- Tools must not delegate work to workers directly.

## Output Contract

Tools should return either the standard API envelope or a tool result object with a clear `status`. Errors should use the standard API error shape and may include `data.error_help`.

## Extension Rule

New tools must be added to the contract catalog before they are exposed to LLM or external execution paths. This protects DIP/OCP: integrations depend on a stable contract instead of ad hoc route behavior.
