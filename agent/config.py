import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CORS_ALLOW_ORIGINS = "http://paasmon.apps.pcicd-k8s.lguplus.co.kr"
DEFAULT_CLUSTER_API_URL_TEMPLATE = "https://api.{cluster}.lguplus.co.kr:6443"
DEFAULT_CLUSTER_API_INSECURE = True


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
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


_bootstrap_env()


@dataclass(frozen=True)
class AppSettings:
    cors_allow_origins: list[str]
    cluster_api_url_template: str
    cluster_api_insecure: bool


settings = AppSettings(
    cors_allow_origins=_parse_csv(os.getenv("CORS_ALLOW_ORIGINS", DEFAULT_CORS_ALLOW_ORIGINS)),
    cluster_api_url_template=os.getenv("CLUSTER_API_URL_TEMPLATE", DEFAULT_CLUSTER_API_URL_TEMPLATE).strip(),
    cluster_api_insecure=_parse_bool(os.getenv("CLUSTER_API_INSECURE"), DEFAULT_CLUSTER_API_INSECURE),
)


CORS_ALLOW_ORIGINS = settings.cors_allow_origins
CLUSTER_API_URL_TEMPLATE = settings.cluster_api_url_template
CLUSTER_API_INSECURE = settings.cluster_api_insecure


def build_cluster_api_url(cluster: str) -> str:
    cleaned = cluster.strip()
    if not cleaned:
        raise ValueError("cluster must not be empty")
    return CLUSTER_API_URL_TEMPLATE.format(cluster=cleaned)
