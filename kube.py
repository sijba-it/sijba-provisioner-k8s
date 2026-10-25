"""Minimal Kubernetes REST client (stdlib only) using the in-cluster account."""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
FIELD_MANAGER = "sijba-provisioner"

_PATHS = {
    "Secret": ("api/v1", "secrets"),
    "Service": ("api/v1", "services"),
    "Pod": ("api/v1", "pods"),
    "Deployment": ("apis/apps/v1", "deployments"),
}


class KubeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"k8s API {status}: {message}")
        self.status = status


class KubeClient:
    def __init__(self, namespace: str | None = None, host: str | None = None,
                 token: str | None = None, ca_file: str | None = None,
                 insecure: bool = False):
        h = host or "https://{}:{}".format(
            os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc"),
            os.environ.get("KUBERNETES_SERVICE_PORT", "443"))
        self.host = h.rstrip("/")
        self.token = token if token is not None else (SA_DIR / "token").read_text().strip()
        self.namespace = namespace or (SA_DIR / "namespace").read_text().strip()
        if insecure:
            self.ctx = ssl._create_unverified_context()
        else:
            self.ctx = ssl.create_default_context(
                cafile=ca_file or str(SA_DIR / "ca.crt"))

    def _url(self, kind: str, name: str | None = None, query: dict | None = None) -> str:
        base, plural = _PATHS[kind]
        url = f"{self.host}/{base}/namespaces/{self.namespace}/{plural}"
        if name:
            url += "/" + urllib.parse.quote(name, safe="")
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def _call(self, method: str, url: str, body: dict | None = None,
              content_type: str = "application/json") -> dict | None:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": content_type,
        })
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=15) as r:
                raw = r.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise KubeError(exc.code, exc.read().decode(errors="replace")[:300]) from exc
        except urllib.error.URLError as exc:
            raise KubeError(503, str(exc.reason)) from exc
        return json.loads(raw) if raw else {}

    def get(self, kind: str, name: str) -> dict | None:
        return self._call("GET", self._url(kind, name))

    def list(self, kind: str, selector: str) -> list[dict]:
        res = self._call("GET", self._url(kind, query={"labelSelector": selector}))
        return (res or {}).get("items", [])

    def apply(self, obj: dict) -> dict:
        kind, name = obj["kind"], obj["metadata"]["name"]
        url = self._url(kind, name, {"fieldManager": FIELD_MANAGER, "force": "true"})
        return self._call("PATCH", url, obj, "application/apply-patch+yaml") or {}

    def delete(self, kind: str, name: str) -> None:
        self._call("DELETE", self._url(kind, name))
