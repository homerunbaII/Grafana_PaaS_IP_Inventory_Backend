# Grafana PaaS IP Inventory Backend

Standalone FastAPI backend for PaaS IP inventory and IP ownership lookup.

## Features

- List current node IP usage in a target cluster
- List current node external IP usage in a target cluster
- List netnamespace egress IP usage in a target cluster
- Look up which node or netnamespace is using a specific IP
- Query one configured cluster or all configured clusters in one request
- Use backend-managed service account bearer tokens from environment/Secret data

## Endpoints

- `GET /`
- `GET /health`
- `POST /ip-inventory/list`
- `POST /ip-inventory/lookup`

## Environment Variables

- `CORS_ALLOW_ORIGINS`
  - Default: `http://paasmon.apps.pcicd-k8s.lguplus.co.kr`
- `CLUSTER_API_URL_TEMPLATE`
  - Default: `https://api.{cluster}.lguplus.co.kr:6443`
- `CLUSTER_API_INSECURE`
  - Default: `true`
- `INVENTORY_CLUSTERS`
  - Default: built-in cluster list used by the current panel
- `CLUSTER_API_TOKENS_JSON`
  - JSON object mapping cluster name to bearer token

Requests no longer accept user-provided bearer tokens. The backend reads per-cluster tokens from environment variables or injected Secret data.

Local `.env` loading is supported from the backend project root. A sample file is provided at `.env.example`.

## Run Locally

```bash
pip install -r agent/requirements.txt
uvicorn agent.main:app --reload --host 0.0.0.0 --port 8000
```
