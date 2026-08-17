# DuckDB snapshot migration

Incompatible schema or embedding dimensions create a **new** snapshot
file. The previous active pointer stays until the new file validates.
There is no in-place rewrite of a reader-open database.
