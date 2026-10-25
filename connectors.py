"""Order / configure / remove connectors: catalog entry -> Secret + Deployment + Service."""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.request
from dataclasses import dataclass

from catalog import TYPE_RE, public_view

MANAGED_BY = "sijba-provisioner"
LABEL_MANAGED = "app.kubernetes.io/managed-by"
LABEL_TYPE = "sijba.it/connector"
MAX_VALUE_LEN = 1024
FAILURE_REASONS = {
    "ErrImagePull", "ImagePullBackOff", "InvalidImageName", "CrashLoopBackOff",
    "CreateContainerConfigError", "CreateContainerError", "RunContainerError",
    "Error", "OOMKilled", "ContainerCannotRun",
}


class ProvisionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Settings:
    core_url: str            # where connector pods reach Core (Service DNS)
    core_token: str
    namespace: str
    max_connectors: int = 5
    core_check_url: str = ""  # where *we* ask Core which handlers are registered


def object_name(ctype: str) -> str:
    return f"connector-{ctype}"


def _labels(ctype: str) -> dict:
    return {LABEL_MANAGED: MANAGED_BY, LABEL_TYPE: ctype, "app": object_name(ctype)}


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _unb64(value: str) -> str:
    return base64.b64decode(value).decode()


def merge_fields(entry: dict, existing: dict[str, str], given: dict) -> dict[str, str]:
    """Defaults < existing values < newly given values. Empty secret input keeps the old value."""
    known = {f["name"]: f for f in entry["fields"]}
    unknown = sorted(set(given) - set(known))
    if unknown:
        raise ProvisionError(422, f"unknown field(s): {', '.join(unknown)}")
    merged: dict[str, str] = {}
    for name, f in known.items():
        value = given.get(name)
        if value is not None and not isinstance(value, str):
            raise ProvisionError(422, f"{name}: must be a string")
        value = (value or "").strip()
        if len(value) > MAX_VALUE_LEN or "\n" in value or "\r" in value:
            raise ProvisionError(422, f"{name}: invalid value")
        if not value:
            value = existing.get(name, "") or f.get("default", "")
        if f.get("required") and not value:
            raise ProvisionError(422, f"{name} is required")
        if value:
            merged[name] = value
    return merged


def render(ctype: str, entry: dict, fields: dict[str, str], s: Settings) -> list[dict]:
    name = object_name(ctype)
    labels = _labels(ctype)
    svc_url = f"http://{name}.{s.namespace}.svc.cluster.local:{entry['port']}"
    secret_data = dict(fields)
    secret_data["SIJBA_TOKEN"] = s.core_token
    digest = hashlib.sha256(
        json.dumps(secret_data, sort_keys=True).encode()).hexdigest()[:16]
    env = {
        "HANDLER_ID": ctype,
        **(entry.get("env") or {}),
        "SIJBA_URL": s.core_url,
        "ADVERTISE_URL": svc_url,
        "LISTEN_PORT": str(entry["port"]),
    }
    res = entry.get("resources") or {}
    secret = {
        "apiVersion": "v1", "kind": "Secret",
        "metadata": {"name": name, "labels": labels},
        "type": "Opaque",
        "data": {k: _b64(v) for k, v in secret_data.items()},
    }
    deployment = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "replicas": 1,
            "revisionHistoryLimit": 2,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": labels,
                             "annotations": {"sijba.it/config-hash": digest}},
                "spec": {
                    "automountServiceAccountToken": False,
                    "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [{
                        "name": "connector",
                        "image": entry["image"],
                        "env": [{"name": k, "value": v} for k, v in sorted(env.items())],
                        "envFrom": [{"secretRef": {"name": name}}],
                        "ports": [{"containerPort": entry["port"], "name": "http"}],
                        "securityContext": {
                            "runAsNonRoot": True, "runAsUser": 65534,
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "resources": {
                            "requests": res.get("requests", {"memory": "32Mi", "cpu": "10m"}),
                            "limits": res.get("limits", {"memory": "64Mi"}),
                        },
                        "readinessProbe": {
                            "httpGet": {"path": "/health", "port": "http"},
                            "initialDelaySeconds": 3, "periodSeconds": 10,
                        },
                    }],
                },
            },
        },
    }
    service = {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {"name": name, "labels": labels},
        "spec": {
            "type": "ClusterIP",
            "selector": {"app": name},
            "ports": [{"name": "http", "port": entry["port"], "targetPort": "http"}],
        },
    }
    return [secret, deployment, service]


class Connectors:
    def __init__(self, kube, catalog: dict[str, dict], settings: Settings):
        self.kube, self.catalog, self.s = kube, catalog, settings

    def _entry(self, ctype: str) -> dict:
        if not TYPE_RE.match(ctype) or ctype not in self.catalog:
            raise ProvisionError(404, f"unknown connector {ctype!r}")
        return self.catalog[ctype]

    def catalog_view(self) -> list[dict]:
        return [public_view(t, e) for t, e in self.catalog.items()]

    def _existing_fields(self, ctype: str) -> dict[str, str] | None:
        sec = self.kube.get("Secret", object_name(ctype))
        if sec is None:
            return None
        return {k: _unb64(v) for k, v in (sec.get("data") or {}).items()}

    def _registered_ids(self) -> set[str] | None:
        if not self.s.core_check_url:
            return None
        req = urllib.request.Request(
            self.s.core_check_url.rstrip("/") + "/api/handlers",
            headers={"Authorization": f"Bearer {self.s.core_token}"})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return {h["id"] for h in json.load(r) if h.get("dynamic")}
        except Exception:
            return None

    def _status(self, ctype: str, registered: set[str] | None) -> tuple[str, str, str]:
        """(status, phase, detail); phase is ok | pending | error."""
        dep = self.kube.get("Deployment", object_name(ctype))
        if dep is None:
            return "missing", "error", ""
        ready = (dep.get("status") or {}).get("readyReplicas", 0) or 0
        if not ready:
            phase, detail = "pending", ""
            for pod in self.kube.list("Pod", f"{LABEL_TYPE}={ctype}"):
                for cs in (pod.get("status") or {}).get("containerStatuses", []):
                    st = cs.get("state") or {}
                    w = st.get("waiting") or st.get("terminated") or {}
                    if w.get("reason"):
                        detail = w["reason"] + (f": {w['message']}" if w.get("message") else "")
                        if w["reason"] in FAILURE_REASONS:
                            phase = "error"
            return "starting", phase, detail
        if registered is not None and ctype not in registered:
            return "running", "pending", "not registered with Core yet"
        return "running", "ok", ""

    def _view(self, ctype: str, fields: dict[str, str], registered: set[str] | None) -> dict:
        entry = self.catalog[ctype]
        status, phase, detail = self._status(ctype, registered)
        out = {}
        for f in entry["fields"]:
            value = fields.get(f["name"], "")
            item = {"set": bool(value)}
            if not f.get("secret"):
                item["value"] = value
            out[f["name"]] = item
        return {"type": ctype, "label": entry["label"], "status": status,
                "phase": phase, "detail": detail, "fields": out}

    def list_active(self) -> list[dict]:
        registered = self._registered_ids()
        result = []
        for dep in self.kube.list("Deployment", f"{LABEL_MANAGED}={MANAGED_BY}"):
            ctype = (dep["metadata"].get("labels") or {}).get(LABEL_TYPE, "")
            if ctype not in self.catalog:
                continue
            fields = self._existing_fields(ctype) or {}
            result.append(self._view(ctype, fields, registered))
        return sorted(result, key=lambda c: c["type"])

    def get(self, ctype: str) -> dict:
        self._entry(ctype)
        fields = self._existing_fields(ctype)
        if fields is None:
            raise ProvisionError(404, f"{ctype} is not active")
        return self._view(ctype, fields, self._registered_ids())

    def put(self, ctype: str, given: dict) -> dict:
        entry = self._entry(ctype)
        existing = self._existing_fields(ctype)
        if existing is None:
            active = self.kube.list("Deployment", f"{LABEL_MANAGED}={MANAGED_BY}")
            if len(active) >= self.s.max_connectors:
                raise ProvisionError(409, f"connector limit reached ({self.s.max_connectors})")
        fields = merge_fields(entry, existing or {}, given)
        for obj in render(ctype, entry, fields, self.s):
            self.kube.apply(obj)
        return self._view(ctype, fields, None)

    def replace_catalog(self, catalog: dict[str, dict]) -> list[str]:
        """Swap in a new catalog and re-apply active connectors (e.g. new image tag)."""
        self.catalog = catalog
        reapplied = []
        for dep in self.kube.list("Deployment", f"{LABEL_MANAGED}={MANAGED_BY}"):
            ctype = (dep["metadata"].get("labels") or {}).get(LABEL_TYPE, "")
            existing = self._existing_fields(ctype) if ctype in catalog else None
            if existing is None:
                continue
            fields = merge_fields(catalog[ctype], existing, {})
            for obj in render(ctype, catalog[ctype], fields, self.s):
                self.kube.apply(obj)
            reapplied.append(ctype)
        return reapplied

    def delete(self, ctype: str) -> None:
        self._entry(ctype)
        name = object_name(ctype)
        for kind in ("Deployment", "Service", "Secret"):
            self.kube.delete(kind, name)
