from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from catalog import load_catalog
from connectors import Connectors, Settings
from main import create_app

CATALOG = Path(__file__).parent / "catalog.json"


class FakeKube:
    """In-memory stand-in: apply = upsert by (kind, name)."""

    def __init__(self):
        self.objs: dict[tuple[str, str], dict] = {}
        self.applied: list[dict] = []

    def get(self, kind, name):
        return self.objs.get((kind, name))

    def list(self, kind, selector):
        k, v = selector.split("=", 1)
        return [o for (kd, _), o in self.objs.items()
                if kd == kind and o["metadata"].get("labels", {}).get(k) == v]

    def apply(self, obj):
        self.applied.append(obj)
        self.objs[(obj["kind"], obj["metadata"]["name"])] = obj
        return obj

    def delete(self, kind, name):
        self.objs.pop((kind, name), None)


@pytest.fixture
def kube():
    return FakeKube()


@pytest.fixture
def settings():
    return Settings(core_url="http://sijba.sijba.svc.cluster.local:8080",
                    core_token="core-secret", namespace="sijba", max_connectors=2)


@pytest.fixture
def connectors(kube, settings):
    return Connectors(kube, load_catalog(CATALOG), settings)


@pytest.fixture
def client(connectors):
    return TestClient(create_app(connectors, "api-token"))


@pytest.fixture
def auth():
    return {"Authorization": "Bearer api-token"}
