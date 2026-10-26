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


def parse_catalog(data) -> dict[str, dict]:
    if not isinstance(data, dict):
        raise CatalogError("catalog must be an object keyed by connector type")
    return {t: _check_entry(t, e) for t, e in data.items()}


def load_catalog(path: str | Path) -> dict[str, dict]:
    return parse_catalog(json.loads(Path(path).read_text(encoding="utf-8")))


def image_env_name(ctype: str) -> str:
    return "CONNECTOR_IMAGE_" + ctype.upper().replace("-", "_")


def _with_tag(image: str) -> str:
    last = image.rsplit("/", 1)[-1]
    return image if ":" in last or "@" in last else image + ":latest"


def build_catalog(catalog_dir: str | Path, env: dict[str, str]) -> dict[str, dict]:
    """Built-in entries (one connector.json per file, collected at image build)
    + CATALOG_EXTRA (JSON keyed by type, wins) + CONNECTOR_IMAGE_<TYPE> pins."""
    raw: dict[str, dict] = {}
    for p in sorted(Path(catalog_dir).glob("*.json")):
        entry = json.loads(p.read_text(encoding="utf-8"))
        raw[entry.pop("type", p.stem)] = entry
    extra = env.get("CATALOG_EXTRA", "").strip()
    if extra:
        try:
            raw.update(json.loads(extra))
        except (ValueError, TypeError) as e:
            raise CatalogError(f"CATALOG_EXTRA: {e}") from e
    catalog = parse_catalog(raw)
    for ctype, entry in catalog.items():
        entry["image"] = _with_tag(env.get(image_env_name(ctype), "").strip() or entry["image"])
    return catalog


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
                "advanced": bool(f.get("advanced")),
            }
            for f in entry["fields"]
        ],
    }
