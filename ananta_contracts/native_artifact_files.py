"""Closed workspace-file declarations shared by Hub compilation and Workers."""

import re
from collections.abc import Mapping
from pathlib import PurePosixPath

from ananta_contracts.native_artifact_transfer import MAX_ARTIFACT_COUNT

NATIVE_ARTIFACT_FILE_KEYS = ("native_input_files", "native_output_files")


def native_workspace_path(value):
    if (
        not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value) or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError("native_artifact_workspace_path_invalid")
    return value


def validate_native_artifact_files(metadata, *, inputs, outputs, require_files=False):
    paths = []
    for key, declared in zip(NATIVE_ARTIFACT_FILE_KEYS, (inputs, outputs), strict=True):
        if key not in metadata:
            if require_files and declared:
                raise ValueError("native_artifact_files_required")
            continue
        values = metadata[key]
        if not isinstance(values, Mapping) or set(values) != set(declared) or len(values) > MAX_ARTIFACT_COUNT:
            raise ValueError("native_artifact_files_binding_invalid")
        for descriptor in values.values():
            if key == "native_input_files":
                path = descriptor
            else:
                if not isinstance(descriptor, Mapping) or set(descriptor) != {"path", "media_type"}:
                    raise ValueError("native_artifact_file_descriptor_invalid")
                media = descriptor["media_type"]
                if (
                    not isinstance(media, str) or len(media) > 127
                    or re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", media) is None
                ):
                    raise ValueError("native_artifact_file_media_type_invalid")
                path = descriptor["path"]
            paths.append(native_workspace_path(path))
    if len(set(paths)) != len(paths) or any(
        left.startswith(right + "/") for left in paths for right in paths if left != right
    ):
        raise ValueError("native_artifact_file_paths_conflict")
