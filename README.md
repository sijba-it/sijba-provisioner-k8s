# sijba-provisioner-k8s

Orders **connectors** (Pushover, later Telegram, …) for one sijba instance
(single tenant) on Kubernetes. The [config tool](../sijba-config-tool) talks
to this API; it never touches Kubernetes itself.

A connector is a separate pod that self-registers with Core (the contract
`sijba-out-pushover` already implements). Ordering one creates, in this
service's namespace:

- `Secret/connector-<type>` — the user-entered fields + `SIJBA_TOKEN`
- `Deployment/connector-<type>` — catalog image, secrets via `envFrom` only
- `Service/connector-<type>` — what Core uses to reach `/invoke`

Deactivating deletes all three. Applies use server-side apply.

## API

Bearer `PROVISIONER_TOKEN` on everything except `/health`.

| Method | Path | |
|---|---|---|
| `GET` | `/connectors/catalog` | what can be ordered, with field schema |
| `GET` | `/connectors` | active connectors + status |
| `GET` | `/connectors/{type}` | one active connector |
| `PUT` | `/connectors/{type}` | activate / change: `{"fields": {...}}` |
| `DELETE` | `/connectors/{type}` | deactivate |

Secrets are **write-only**: responses report `{"set": true}` for secret fields,
never the value. On update, an empty/missing secret keeps the stored value.
Status is `starting` / `running` plus a `detail` (e.g. `ImagePullBackOff`, or
"not registered with Core yet" when `CORE_CHECK_URL` is set).

## Config

| Env | Default | |
|---|---|---|
| `PROVISIONER_TOKEN` | *(required)* | API bearer token |
| `CORE_TOKEN` | *(required)* | token connectors use to register with Core |
| `CORE_URL` | `http://sijba.<ns>.svc.cluster.local:8080` | Core as seen from connector pods |
| `CORE_CHECK_URL` | *(empty)* | Core as seen from here (e.g. `http://127.0.0.1:8080`), for the "registered" check |
| `CATALOG_FILE` | `/catalog/catalog.json` | the catalog |
| `MAX_CONNECTORS` | `5` | each one is a pod |
| `LISTEN_HOST` / `PORT` | `127.0.0.1` / `8082` | the image sets `0.0.0.0` |
| `NAMESPACE` | from service account | |

Catalog format: see `tests/catalog.json`. Images come from the catalog only —
the API cannot start arbitrary images.

## RBAC

Namespaced `Role` only: `get/list/create/patch/delete` on `secrets`,
`deployments`, `services`; `get/list` on `pods`. The manifests live in
[sijba-k8s](../sijba-k8s).

## Tests

```bash
pip install -r requirements-dev.txt
python -m pyflakes *.py tests/ && python -m pytest -q
```
