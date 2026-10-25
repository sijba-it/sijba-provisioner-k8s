"""Connector catalog: which connectors can be ordered and what they need."""
from __future__ import annotations

import json
import re
from pathlib import Path

TYPE_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
FIELD_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
RESERVED_ENV = {
    "SIJBA_URL", "SIJBA_TOKEN", "ADVERTISE_URL", "LISTEN_HOST", "LISTEN_PORT",
    "HANDLER_ID", "PATH", "HOME",
}


class CatalogError(ValueError):
    pass


def _check_entry(ctype: str, entry: dict) -> dict:
    if not TYPE_RE.match(ctype):
        raise CatalogError(f"bad connector type {ctype!r}")
    for key in ("label", "image", "port", "fields"):
        if key not in entry:
            raise CatalogError(f"{ctype}: missing {key!r}")
    if not isinstance(entry["port"], int) or not 1 <= entry["port"] <= 65535:
        raise CatalogError(f"{ctype}: bad port")
    env = entry.get("env") or {}
    for name in env:
        if not FIELD_RE.match(name) or name in RESERVED_ENV - {"HANDLER_ID"}:
            raise CatalogError(f"{ctype}: bad static env {name!r}")
    seen = set()
    for f in entry["fields"]:
        name = f.get("name", "")
        if not FIELD_RE.match(name) or name in RESERVED_ENV or name in env:
            raise CatalogError(f"{ctype}: bad field name {name!r}")
        if name in seen:
            raise CatalogError(f"{ctype}: duplicate field {name!r}")
        seen.add(name)
    return entry


def load_catalog(path: str | Path) -> dict[str, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CatalogError("catalog must be an object keyed by connector type")
    return {t: _check_entry(t, e) for t, e in data.items()}


def public_view(ctype: str, entry: dict) -> dict:
    return {
        "type": ctype,
        "label": entry["label"],
        "description": entry.get("description", ""),
        "direction": entry.get("direction", ""),
        "fields": [
            {
                "name": f["name"],
                "label": f.get("label", f["name"]),
                "help": f.get("help", ""),
                "secret": bool(f.get("secret")),
                "required": bool(f.get("required")),
                "default": f.get("default", ""),
            }
            for f in entry["fields"]
        ],
    }
