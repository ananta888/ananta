# DuckDB security

- No free-form SQL, ATTACH, INSTALL, LOAD, COPY to arbitrary paths.
- Extension autoinstall/autoload/community/unsigned are rejected.
- `enable_external_access` is off.
- Query templates bind workspace/repository/profile/domain from the
  server capability, not from the client.
- Logs never include vectors, secrets or full chunk text.
