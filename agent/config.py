import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CORS_ALLOW_ORIGINS = "http://paasmon.apps.pcicd-k8s.lguplus.co.kr"
DEFAULT_CLUSTER_API_URL_TEMPLATE = "https://api.{cluster}.lguplus.co.kr:6443"
DEFAULT_CLUSTER_API_INSECURE = True
DEFAULT_INVENTORY_CLUSTERS = ",".join(
    [
        "dprsv-k8s",
        "dprv-k8s",
        "dprmn-k8s",
        "dprrt-k8s",
        "dpvs-k8s",
        "sdprmn-paas",
        "tprsv-k8s",
        "eprsv-k8s",
        "pprsv-k8s",
        "pprv-k8s",
        "pprmn-k8s",
        "pprrt-k8s",
        "ppvs-k8s",
        "papim-paas",
    ]
)
DEFAULT_CLUSTER_API_TOKENS_JSON = "{}"


def _load_env_file(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        cleaned_key = key.strip()
        if not cleaned_key or cleaned_key in os.environ:
            continue

        cleaned_value = value.strip()
        if len(cleaned_value) >= 2 and cleaned_value[0] == cleaned_value[-1] and cleaned_value[0] in {"'", '"'}:
            cleaned_value = cleaned_value[1:-1]

        os.environ[cleaned_key] = cleaned_value


def _bootstrap_env() -> None:
    configured_path = (os.getenv("APP_ENV_FILE") or "").strip()
    candidates: list[Path] = []

    if configured_path:
        candidates.append(Path(configured_path).expanduser())

    backend_root = Path(__file__).resolve().parent.parent
    candidates.append(backend_root / ".env")

    cwd_env = Path.cwd() / ".env"
    if cwd_env not in candidates:
        candidates.append(cwd_env)

    for candidate in candidates:
        if candidate.is_file():
            _load_env_file(candidate)
            break


def _parse_csv(value: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for item in value.split(","):
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue

        seen.add(cleaned)
        result.append(cleaned)

    return result


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _parse_cluster_tokens(value: str) -> dict[str, str]:
    raw = (value or "").strip()
    if not raw:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"CLUSTER_API_TOKENS_JSON must be valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("CLUSTER_API_TOKENS_JSON must be a JSON object")

    result: dict[str, str] = {}

    for cluster_name, token in parsed.items():
        if not isinstance(cluster_name, str):
            raise ValueError("CLUSTER_API_TOKENS_JSON keys must be strings")
        if not isinstance(token, str):
            raise ValueError("CLUSTER_API_TOKENS_JSON values must be strings")

        cleaned_cluster = cluster_name.strip()
        cleaned_token = token.strip()
        if not cleaned_cluster or not cleaned_token:
            continue

        result[cleaned_cluster] = cleaned_token

    return result


_bootstrap_env()


@dataclass(frozen=True)
class AppSettings:
    cors_allow_origins: list[str]
    cluster_api_url_template: str
    cluster_api_insecure: bool
    inventory_clusters: list[str]
    cluster_api_tokens_by_cluster: dict[str, str]


settings = AppSettings(
    cors_allow_origins=_parse_csv(os.getenv("CORS_ALLOW_ORIGINS", DEFAULT_CORS_ALLOW_ORIGINS)),
    cluster_api_url_template=os.getenv("CLUSTER_API_URL_TEMPLATE", DEFAULT_CLUSTER_API_URL_TEMPLATE).strip(),
    cluster_api_insecure=_parse_bool(os.getenv("CLUSTER_API_INSECURE"), DEFAULT_CLUSTER_API_INSECURE),
    inventory_clusters=_parse_csv(os.getenv("INVENTORY_CLUSTERS", DEFAULT_INVENTORY_CLUSTERS)),
    cluster_api_tokens_by_cluster=_parse_cluster_tokens(os.getenv("CLUSTER_API_TOKENS_JSON", DEFAULT_CLUSTER_API_TOKENS_JSON)),
)


CORS_ALLOW_ORIGINS = settings.cors_allow_origins
CLUSTER_API_URL_TEMPLATE = settings.cluster_api_url_template
CLUSTER_API_INSECURE = settings.cluster_api_insecure
INVENTORY_CLUSTERS = settings.inventory_clusters
CLUSTER_API_TOKENS_BY_CLUSTER = settings.cluster_api_tokens_by_cluster


def build_cluster_api_url(cluster: str) -> str:
    cleaned = cluster.strip()
    if not cleaned:
        raise ValueError("cluster must not be empty")
    return CLUSTER_API_URL_TEMPLATE.format(cluster=cleaned)


def get_inventory_clusters() -> list[str]:
    return INVENTORY_CLUSTERS


def is_all_clusters_target(cluster: str) -> bool:
    return cluster.strip().lower() == "all"


def get_cluster_bearer_token(cluster: str) -> str:
    cleaned = cluster.strip()
    if not cleaned:
        raise ValueError("cluster must not be empty")

    token = CLUSTER_API_TOKENS_BY_CLUSTER.get(cleaned, "").strip()
    if not token:
        raise ValueError(f"no bearer token configured for cluster: {cleaned}")

    return token
