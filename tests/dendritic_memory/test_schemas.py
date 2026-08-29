from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from tests.dendritic_memory.helpers import config, pack, spec

ROOT = Path(__file__).resolve().parents[2]


def test_json_schemas_accept_canonical_contracts() -> None:
    config_schema = json.loads((ROOT / "schemas/dendritic-memory/config.v1.json").read_text())
    job_schema = json.loads((ROOT / "schemas/dendritic-memory/job.v1.json").read_text())
    pack_schema = json.loads((ROOT / "schemas/dendritic-memory/pack.v1.json").read_text())
    for schema in (config_schema, job_schema, pack_schema):
        jsonschema.Draft202012Validator.check_schema(schema)
    resolved_job_schema = {**job_schema, "properties": {**job_schema["properties"], "configuration": config_schema}}
    jsonschema.validate(config().to_dict(), config_schema)
    jsonschema.validate(spec().to_dict(), resolved_job_schema)
    jsonschema.validate(pack()[0].to_dict(), pack_schema)
