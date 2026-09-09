"""Opted-in test transport adapter; never part of a packaged production Worker."""

import ipaddress
import json
import re
from pathlib import Path


def relay_context_script(url, path):
    match = re.fullmatch(r"turn:([0-9.]+):3478\?transport=(udp|tcp)", url)
    if match is None or not ipaddress.IPv4Address(match[1]).is_private:
        raise ValueError("test_worker_relay_scope_invalid")
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 8192:
        raise ValueError("test_worker_relay_script_invalid")
    source = path.read_text()
    # This is a trusted test-source mount, not remotely supplied JavaScript.
    prefix, separator, body = source.partition("export function installMachineForcedRelay(")
    if not separator or "export " in body:
        raise ValueError("test_worker_relay_script_invalid")
    return "(function installMachineForcedRelay(" + body + ")(" + json.dumps(url) + ");"


def install_relay_context(browser_type, url, path):
    script = relay_context_script(url, path)
    native = browser_type.new_context

    def context(self, *args, **kwargs):
        result = native(self, *args, **kwargs)
        try:
            errors = []

            def console(message):
                match = re.fullmatch(r"test_relay_ice_error:([3-7][0-9]{2})", message.text)
                if match is not None and len(errors) < 8:
                    errors.append(int(match[1]))
                    try:
                        Path("/state/relay-diagnostic.json").write_text(json.dumps(errors))
                    except OSError:
                        pass

            result.on("page", lambda page: page.on("console", console))
            result.add_init_script(script=script)
        except Exception:
            result.close()
            raise
        return result

    browser_type.new_context = context
