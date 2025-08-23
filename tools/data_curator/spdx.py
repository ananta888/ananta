from typing import Set

# Strict whitelist (SPDX identifiers)
SPDX_WHITELIST: Set[str] = set(
    [
        "GPL-2.0",
        "GPL-3.0",
        "LGPL-2.1",
        "LGPL-3.0",
        "AGPL-3.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "MIT",
        "Apache-2.0",
        "MPL-2.0",
    ]
)

# Common variants mapping to canonical
_VARIANTS = {
    "GPL-2.0-only": "GPL-2.0",
    "GPL-2.0-or-later": "GPL-2.0",
    "GPL-3.0-only": "GPL-3.0",
    "GPL-3.0-or-later": "GPL-3.0",
    "LGPL-2.1-only": "LGPL-2.1",
    "LGPL-2.1-or-later": "LGPL-2.1",
    "LGPL-3.0-only": "LGPL-3.0",
    "LGPL-3.0-or-later": "LGPL-3.0",
    "AGPL-3.0-only": "AGPL-3.0",
    "AGPL-3.0-or-later": "AGPL-3.0",
    "BSD-2": "BSD-2-Clause",
    "BSD-3": "BSD-3-Clause",
}


def _normalize(lic: str) -> str:
    s = (lic or "").strip()
    # Remove license expressions like ( AND / OR ) naively by taking first token
    for sep in ["(", ")", "AND", "OR", "+", ",", ";", "/"]:
        s = s.replace(sep, " ")
    parts = [p for p in s.split() if p]
    if not parts:
        return ""
    base = parts[0]
    base = _VARIANTS.get(base, base)
    return base


FORBIDDEN_PREFIXES = (
    "CC-BY",
    "CC0",  # CC0 is public domain but often mixed; we keep strict whitelist only
)


def is_whitelisted_license(license_id: str) -> bool:
    if not license_id:
        return False
    lic = _normalize(license_id)
    if not lic:
        return False
    # block CC and non-whitelist explicitly
    for p in FORBIDDEN_PREFIXES:
        if lic.upper().startswith(p):
            return False
    return lic in SPDX_WHITELIST
