# Grafana PaaS IP Inventory Backend

Standalone FastAPI backend for PaaS IP inventory and IP ownership lookup.

## Features

- List current node IP usage in a target cluster
- List current node external IP usage in a target cluster
- List netnamespace egress IP usage in a target cluster
- Look up which node or netnamespace is using a specific IP
- Forward cluster API requests with a user-provided bearer token

## Endpoints

- `GET /`
- `GET /health`
- `POST /ip-inventory/list`
- `POST /ip-inventory/lookup`

## Environment Variables

- `CLUSTER_API_URL_TEMPLATE`
  - Default: `https://api.{cluster}:6443`
- `CORS_ORIGINS`
  - Default: `*`

## Run Locally

```bash
pip install -r agent/requirements.txt
uvicorn agent.main:app --reload --host 0.0.0.0 --port 8000
```
