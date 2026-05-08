import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AppSettings:
    cluster_api_url_template: str

settings = AppSettings(
    cluster_api_url_template=os.getenv("CLUSTER_API_URL_TEMPLATE", "https://api.{cluster}:6443"),
)


CLUSTER_API_URL_TEMPLATE = settings.cluster_api_url_template


def build_cluster_api_url(cluster: str) -> str:
    cleaned = cluster.strip()
    if not cleaned:
        raise ValueError("cluster must not be empty")
    return CLUSTER_API_URL_TEMPLATE.format(cluster=cleaned)
