"""sijba-provisioner-k8s — order connectors for one sijba instance (single tenant)."""
from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from catalog import load_catalog
from connectors import Connectors, ProvisionError, Settings
from kube import KubeClient, KubeError


class PutBody(BaseModel):
    fields: dict = Field(default_factory=dict)


def create_app(connectors: Connectors, token: str) -> FastAPI:
    app = FastAPI(title="sijba-provisioner-k8s", docs_url=None, redoc_url=None)

    def auth(authorization: str | None = Header(default=None)) -> None:
        got = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(got.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.exception_handler(ProvisionError)
    async def _prov_err(_r, exc: ProvisionError):
        return JSONResponse({"error": str(exc)}, status_code=exc.status)

    @app.exception_handler(KubeError)
    async def _kube_err(_r, exc: KubeError):
        return JSONResponse({"error": str(exc)}, status_code=502)

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/connectors/catalog", dependencies=[Depends(auth)])
    def catalog():
        return {"catalog": connectors.catalog_view()}

    @app.get("/connectors", dependencies=[Depends(auth)])
    def list_active():
        return {"connectors": connectors.list_active()}

    @app.get("/connectors/{ctype}", dependencies=[Depends(auth)])
    def get_one(ctype: str):
        return connectors.get(ctype)

    @app.put("/connectors/{ctype}", dependencies=[Depends(auth)])
    def put_one(ctype: str, body: PutBody):
        return connectors.put(ctype, body.fields)

    @app.delete("/connectors/{ctype}", dependencies=[Depends(auth)])
    def delete_one(ctype: str):
        connectors.delete(ctype)
        return {"ok": True}

    return app


def build_from_env() -> FastAPI:
    token = os.environ.get("PROVISIONER_TOKEN", "").strip()
    if not token:
        raise SystemExit("PROVISIONER_TOKEN is required")
    core_token = os.environ.get("CORE_TOKEN", "").strip()
    if not core_token:
        raise SystemExit("CORE_TOKEN is required (connectors register with it)")
    kube = KubeClient(insecure=os.environ.get("KUBE_INSECURE") == "1",
                      host=os.environ.get("KUBE_HOST") or None,
                      token=os.environ.get("KUBE_TOKEN") or None,
                      namespace=os.environ.get("NAMESPACE") or None)
    settings = Settings(
        core_url=os.environ.get("CORE_URL", "").rstrip("/")
        or f"http://sijba.{kube.namespace}.svc.cluster.local:8080",
        core_token=core_token,
        namespace=kube.namespace,
        max_connectors=int(os.environ.get("MAX_CONNECTORS", "5")),
        core_check_url=os.environ.get("CORE_CHECK_URL", "").rstrip("/"),
    )
    catalog = load_catalog(os.environ.get("CATALOG_FILE", "/catalog/catalog.json"))
    return create_app(Connectors(kube, catalog, settings), token)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(build_from_env(),
                host=os.environ.get("LISTEN_HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8082")))
