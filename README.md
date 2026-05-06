# Grafana PaaS IP Inventory Backend

Standalone FastAPI backend for PaaS IP inventory and IP ownership lookup.

## Current Scope

- Minimal application scaffold
- Health endpoint
- Package layout ready for IP inventory routes, models, and services

## Run Locally

```bash
pip install -r agent/requirements.txt
uvicorn agent.main:app --reload --host 0.0.0.0 --port 8000
```
