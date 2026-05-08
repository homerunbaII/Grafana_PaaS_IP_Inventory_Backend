import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppSettings:
    cors_origins: list[str]
    cluster_api_url_template: str


def parse_cors_origins(raw_value: str) -> list[str]:
    cleaned = [item.strip() for item in raw_value.split(",") if item.strip()]
    return cleaned or ["*"]


settings = AppSettings(
    cors_origins=parse_cors_origins(
        os.getenv(
            "CORS_ORIGINS",
            "https://paasmon.apps.pcicd-k8s.lguplus.co.kr,http://paasmon.apps.pcicd-k8s.lguplus.co.kr",
        )
    ),
    cluster_api_url_template=os.getenv("CLUSTER_API_URL_TEMPLATE", "https://api.{cluster}:6443"),
)


CORS_ORIGINS = settings.cors_origins
CLUSTER_API_URL_TEMPLATE = settings.cluster_api_url_template


def build_cluster_api_url(cluster: str) -> str:
    cleaned = cluster.strip()
    if not cleaned:
        raise ValueError("cluster must not be empty")
    return CLUSTER_API_URL_TEMPLATE.format(cluster=cleaned)
