import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent.config import CORS_ORIGINS
from agent.routes.health import router as health_router
from agent.routes.ip_inventory import router as ip_inventory_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

app = FastAPI(title="Grafana PaaS IP Inventory Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(ip_inventory_router)
