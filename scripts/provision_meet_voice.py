"""Provision one shipped voice's model files only; never keys or service state.

Run from the repository with: python -m scripts.provision_meet_voice DIRECTORY
--voice-id piper.de_DE.thorsten_emotional.medium.neutral
"""

import argparse
import hashlib
import os
import secrets
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from ananta_contracts.meet_voice_catalog import DEFAULT_VOICE_ID, voice_preset
from worker.meet_media.piper_assets import read_pinned_file


class VoiceDownloadRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, request, fp, code, message, headers, newurl):
        parsed = urlsplit(newurl)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not (host == "huggingface.co" or host.endswith((".huggingface.co", ".hf.co")))
        ):
            raise ValueError("meet_voice_download_redirect_denied")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download(url, maximum, *, clock=time.monotonic):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), VoiceDownloadRedirect())
    deadline = clock() + 90
    parts, size = [], 0
    with opener.open(url, timeout=10) as response:
        length = response.headers.get("Content-Length")
        if (
            response.status != 200
            or response.headers.get("Content-Encoding", "identity") != "identity"
            or length is not None
            and (not length.isdecimal() or not 0 < int(length) <= maximum)
        ):
            raise ValueError("meet_voice_download_invalid")
        while True:
            if clock() >= deadline:
                raise ValueError("meet_voice_download_timeout")
            part = response.read(min(1_048_576, maximum + 1 - size))
            if clock() >= deadline:
                raise ValueError("meet_voice_download_timeout")
            if not part:
                break
            parts.append(part)
            size += len(part)
            if size > maximum:
                raise ValueError("meet_voice_download_oversize")
    if not size or length is not None and size != int(length):
        raise ValueError("meet_voice_download_truncated")
    return b"".join(parts)


def _install(directory_fd, name, content, expected, maximum):
    if type(content) is not bytes or not 0 < len(content) <= maximum or hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("meet_voice_download_digest_mismatch")
    temporary = ".voice-download-" + secrets.token_hex(16)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd, follow_symlinks=False)
        except FileExistsError:
            # Accept only an already completed valid install; never overwrite.
            read_pinned_file(name, sha256=expected, maximum=maximum, dir_fd=directory_fd)
    finally:
        os.unlink(temporary, dir_fd=directory_fd)  # Only this invocation's exclusive temporary file.
    os.fsync(directory_fd)


def provision_voice(directory, voice_id=DEFAULT_VOICE_ID, *, fetch=download):
    preset = voice_preset(voice_id)  # Reject caller paths/URLs/unknown voices before any filesystem action.
    directory = Path(directory)
    if not directory.is_absolute() or directory == Path("/") or directory.resolve() != directory:
        raise ValueError("meet_voice_model_directory_invalid")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        model = preset.model
        base = f"https://huggingface.co/rhasspy/piper-voices/resolve/{model.revision}/{model.repository_path}/"
        for name, digest, maximum in (
            (model.name, model.model_sha256, model.model_max_bytes),
            (model.name + ".json", model.config_sha256, model.config_max_bytes),
        ):
            try:
                os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                _install(descriptor, name, fetch(base + name, maximum), digest, maximum)
            else:
                read_pinned_file(name, sha256=digest, maximum=maximum, dir_fd=descriptor)
        return {"voice_id": preset.voice_id, "model": model.name, "verified": True}
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--voice-id", default=DEFAULT_VOICE_ID)
    args = parser.parse_args()
    provision_voice(args.directory, args.voice_id)
    print("Pinned voice files verified. No keys, trust or services changed.")


if __name__ == "__main__":
    main()
