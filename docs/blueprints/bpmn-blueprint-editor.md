# BPMN Blueprint Editor

The Angular BPMN editor is available at:

```text
/process-designer/bpmn
```

It uses `bpmn-js` as the diagram editor and the hub API as the source of truth
for Ananta semantics.

## Data Flow

1. The user edits a BPMN diagram.
2. The editor exports BPMN XML from `bpmn-js`.
3. The hub imports XML through `/api/visual-process/bpmn/import`.
4. The hub returns a `VisualProcessGraph` plus validation warnings.
5. The editor can compile the graph through `/api/visual-process/workflow-request`.
6. The editor can start the request through `/api/visual-process/workflow/start`.

BPMN XML is an import/export representation. It is not the runtime authority.
The canonical execution contract is `WorkflowRequest`.

## Supported BPMN Elements

The backend adapter currently maps:

- `StartEvent`
- `EndEvent`
- `Task`
- `ServiceTask`
- `UserTask`
- `ScriptTask`
- `BusinessRuleTask`
- `ExclusiveGateway`
- `ParallelGateway`
- `SequenceFlow`

Unsupported BPMN elements are ignored with structured warnings so diagrams can
be normalized incrementally.

## Ananta Metadata

Ananta-specific fields are stored in `ananta:metadata` extension elements during
backend export. The UI also keeps editable sidecar metadata for selected
elements before compiling:

- task kind
- role
- gate flag
- policy scope
- allowed tools

The hub validates policy scope before creating an executable request. The UI
does not grant permissions by itself.
