import {
  SourceControlV1ContractError,
  parseSourceControlProjection,
} from './source-control-v1-api.model';

function projection(connection: Record<string, unknown>): unknown {
  return {
    schema: 'ananta.source-control.projection.v1',
    connection_id: 'connection-example',
    etag: 'a'.repeat(64),
    connection,
    revision: null,
    admission: null,
    index: null,
    active_index: null,
    stale: false,
    grants: [],
    health: {},
    next_actions: [],
  };
}

describe('source-control v1 projection contract', () => {
  it('requires the server-authoritative project without exposing tenant', () => {
    const parsed = parseSourceControlProjection(
      projection({
        connection_id: 'connection-example',
        project_id: 'project-example',
      }),
    );

    expect(parsed.connection.project_id).toBe('project-example');
    expect('tenant_id' in parsed.connection).toBeFalsy();
  });

  it('fails closed when project is absent or tenant is projected', () => {
    expect(() =>
      parseSourceControlProjection(
        projection({ connection_id: 'connection-example' }),
      ),
    ).toThrowError(SourceControlV1ContractError);
    expect(() =>
      parseSourceControlProjection(
        projection({
          connection_id: 'connection-example',
          project_id: 'project-example',
          tenant_id: 'tenant-secret',
        }),
      ),
    ).toThrowError(SourceControlV1ContractError);
  });
});
