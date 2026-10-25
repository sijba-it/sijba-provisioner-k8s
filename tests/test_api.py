import base64

import pytest

ORDER = {"fields": {"PUSHOVER_APP_TOKEN": "app-123", "PUSHOVER_USER_KEY": "user-456"}}


def test_requires_token(client):
    assert client.get("/connectors").status_code == 401
    assert client.get("/connectors", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/health").status_code == 200


def test_catalog_has_schema_but_no_internals(client, auth):
    cat = client.get("/connectors/catalog", headers=auth).json()["catalog"]
    assert cat[0]["type"] == "pushover"
    names = [f["name"] for f in cat[0]["fields"]]
    assert "PUSHOVER_APP_TOKEN" in names
    assert "image" not in cat[0]


def test_activate_creates_secret_deployment_service(client, auth, kube):
    r = client.put("/connectors/pushover", json=ORDER, headers=auth)
    assert r.status_code == 200, r.text
    assert {k for k in kube.objs} == {
        ("Secret", "connector-pushover"), ("Deployment", "connector-pushover"),
        ("Service", "connector-pushover")}
    secret = kube.objs[("Secret", "connector-pushover")]["data"]
    assert base64.b64decode(secret["PUSHOVER_APP_TOKEN"]) == b"app-123"
    assert base64.b64decode(secret["SIJBA_TOKEN"]) == b"core-secret"
    assert base64.b64decode(secret["PUSHOVER_MATCH_SOURCES"]) == b"webhook:voice"
    dep = kube.objs[("Deployment", "connector-pushover")]
    c = dep["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e["value"] for e in c["env"]}
    assert env["ADVERTISE_URL"] == "http://connector-pushover.sijba.svc.cluster.local:9101"
    assert env["SIJBA_URL"] == "http://sijba.sijba.svc.cluster.local:8080"
    assert env["HANDLER_ID"] == "pushover"
    assert "SIJBA_TOKEN" not in env  # secrets only via secretRef, never inline
    assert c["envFrom"] == [{"secretRef": {"name": "connector-pushover"}}]
    assert c["securityContext"]["runAsNonRoot"] is True
    assert dep["spec"]["template"]["spec"]["automountServiceAccountToken"] is False


def test_response_never_contains_secret_values(client, auth):
    body = client.put("/connectors/pushover", json=ORDER, headers=auth).json()
    assert "app-123" not in str(body) and "user-456" not in str(body)
    assert body["fields"]["PUSHOVER_APP_TOKEN"] == {"set": True}
    assert body["fields"]["PUSHOVER_MATCH_SOURCES"] == {"set": True, "value": "webhook:voice"}
    listed = client.get("/connectors", headers=auth).json()["connectors"]
    assert "app-123" not in str(listed)


def test_missing_required_field_rejected(client, auth, kube):
    r = client.put("/connectors/pushover", json={"fields": {"PUSHOVER_APP_TOKEN": "x"}}, headers=auth)
    assert r.status_code == 422 and "PUSHOVER_USER_KEY" in r.json()["error"]
    assert not kube.objs


def test_unknown_field_and_type_rejected(client, auth):
    r = client.put("/connectors/pushover", json={"fields": {**ORDER["fields"], "EVIL": "1"}}, headers=auth)
    assert r.status_code == 422
    assert client.put("/connectors/nope", json=ORDER, headers=auth).status_code == 404
    assert client.put("/connectors/..%2Fx", json=ORDER, headers=auth).status_code == 404


def test_update_keeps_secrets_when_left_empty_and_rolls_pod(client, auth, kube):
    client.put("/connectors/pushover", json=ORDER, headers=auth)
    h1 = kube.objs[("Deployment", "connector-pushover")]["spec"]["template"]["metadata"]["annotations"]["sijba.it/config-hash"]
    r = client.put("/connectors/pushover",
                   json={"fields": {"PUSHOVER_APP_TOKEN": "", "PUSHOVER_TITLE": "Home"}}, headers=auth)
    assert r.status_code == 200, r.text
    secret = kube.objs[("Secret", "connector-pushover")]["data"]
    assert base64.b64decode(secret["PUSHOVER_APP_TOKEN"]) == b"app-123"
    assert base64.b64decode(secret["PUSHOVER_TITLE"]) == b"Home"
    h2 = kube.objs[("Deployment", "connector-pushover")]["spec"]["template"]["metadata"]["annotations"]["sijba.it/config-hash"]
    assert h1 != h2


def test_delete_removes_everything(client, auth, kube):
    client.put("/connectors/pushover", json=ORDER, headers=auth)
    assert client.delete("/connectors/pushover", headers=auth).status_code == 200
    assert not kube.objs
    assert client.delete("/connectors/pushover", headers=auth).status_code == 200


def test_connector_limit(client, auth, kube, connectors):
    import copy
    connectors.catalog["second"] = copy.deepcopy(connectors.catalog["pushover"])
    connectors.catalog["third"] = copy.deepcopy(connectors.catalog["pushover"])
    for t in ("pushover", "second"):
        assert client.put(f"/connectors/{t}", json=ORDER, headers=auth).status_code == 200
    assert client.put("/connectors/third", json=ORDER, headers=auth).status_code == 409
    # updating an existing one is still fine at the limit
    assert client.put("/connectors/second", json=ORDER, headers=auth).status_code == 200


def test_list_shows_status_from_deployment(client, auth, kube):
    client.put("/connectors/pushover", json=ORDER, headers=auth)
    c = client.get("/connectors", headers=auth).json()["connectors"][0]
    assert c["type"] == "pushover" and c["status"] == "starting" and c["phase"] == "pending"
    kube.objs[("Deployment", "connector-pushover")]["status"] = {"readyReplicas": 1}
    c = client.get("/connectors", headers=auth).json()["connectors"][0]
    assert c["status"] == "running" and c["phase"] == "ok"


def _pod(reason):
    return {"kind": "Pod", "metadata": {"name": "p", "labels": {"sijba.it/connector": "pushover"}},
            "status": {"containerStatuses": [{"state": {"waiting": {"reason": reason}}}]}}


@pytest.mark.parametrize("reason,phase", [
    ("ContainerCreating", "pending"), ("ImagePullBackOff", "error"), ("CrashLoopBackOff", "error")])
def test_phase_separates_waiting_from_failure(client, auth, kube, reason, phase):
    client.put("/connectors/pushover", json=ORDER, headers=auth)
    kube.objs[("Pod", "p")] = _pod(reason)
    c = client.get("/connectors", headers=auth).json()["connectors"][0]
    assert c["phase"] == phase and c["detail"] == reason


def test_catalog_change_reapplies_active_connectors(client, auth, kube, connectors):
    import copy
    client.put("/connectors/pushover", json=ORDER, headers=auth)
    new = copy.deepcopy(connectors.catalog)
    new["pushover"]["image"] = "example/pushover:2"
    assert connectors.replace_catalog(new) == ["pushover"]
    c = kube.objs[("Deployment", "connector-pushover")]["spec"]["template"]["spec"]["containers"][0]
    assert c["image"] == "example/pushover:2"
    secret = kube.objs[("Secret", "connector-pushover")]["data"]
    assert base64.b64decode(secret["PUSHOVER_APP_TOKEN"]) == b"app-123"


def test_list_ignores_foreign_deployments(client, auth, kube):
    kube.objs[("Deployment", "other")] = {"kind": "Deployment", "metadata": {
        "name": "other", "labels": {"app.kubernetes.io/managed-by": "sijba-provisioner",
                                    "sijba.it/connector": "ghost"}}}
    assert client.get("/connectors", headers=auth).json()["connectors"] == []


def test_catalog_rejects_reserved_field(tmp_path):
    import json
    from catalog import CatalogError, load_catalog
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"x": {"label": "X", "image": "i", "port": 1,
                                   "fields": [{"name": "SIJBA_TOKEN"}]}}))
    with pytest.raises(CatalogError):
        load_catalog(p)
