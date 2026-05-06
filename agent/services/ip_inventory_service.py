import json
import logging
import shutil
import subprocess

from agent.config import build_cluster_api_url

logger = logging.getLogger(__name__)


def ensure_curl_available() -> None:
    if shutil.which("curl"):
        return
    raise RuntimeError("curl is not available in this environment")


def run_cluster_curl(cluster: str, bearer_token: str, api_path: str) -> dict:
    ensure_curl_available()

    base_url = build_cluster_api_url(cluster)
    url = f"{base_url}{api_path}"
    command = [
        "curl",
        "--insecure",
        "--silent",
        "--show-error",
        "-H",
        f"Authorization: Bearer {bearer_token}",
        url,
    ]

    logger.info("Running cluster curl for %s %s", cluster, api_path)

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"curl request timed out: {exc}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise RuntimeError(f"curl request failed: {detail}")

    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid json response: {exc}") from exc


def extract_node_roles(labels: dict) -> list[str]:
    roles: list[str] = []

    for key, value in labels.items():
        if not key.startswith("node-role.kubernetes.io/"):
            continue

        suffix = key.split("node-role.kubernetes.io/", 1)[1].strip()
        if suffix:
            roles.append(suffix)
            continue

        if isinstance(value, str) and value.strip():
            roles.append(value.strip())

    return roles


def build_node_entries(payload: dict) -> list[dict]:
    items = payload.get("items", [])
    entries: list[dict] = []

    for item in items:
        metadata = item.get("metadata", {})
        status = item.get("status", {})
        labels = metadata.get("labels", {})
        addresses = status.get("addresses", [])
        roles = extract_node_roles(labels) or ["worker/other"]
        internal_ips = [address.get("address") for address in addresses if address.get("type") == "InternalIP"]
        external_ips = [address.get("address") for address in addresses if address.get("type") == "ExternalIP"]

        entries.append(
            {
                "name": metadata.get("name"),
                "roles": roles,
                "ips": [ip for ip in internal_ips if ip],
                "externalIPs": [ip for ip in external_ips if ip],
                "addresses": [
                    {
                        "type": address.get("type"),
                        "address": address.get("address"),
                    }
                    for address in addresses
                    if address.get("address")
                ],
            }
        )

    return entries


def build_netnamespace_entries(payload: dict) -> list[dict]:
    items = payload.get("items", [])
    entries: list[dict] = []

    for item in items:
        metadata = item.get("metadata", {})
        egress_ips = item.get("egressIPs", []) or []
        entries.append(
            {
                "name": metadata.get("name"),
                "netid": item.get("netid"),
                "egressIPs": [ip for ip in egress_ips if isinstance(ip, str) and ip.strip()],
            }
        )

    return entries


def list_cluster_ip_usage(cluster: str, bearer_token: str) -> dict:
    try:
        nodes_payload = run_cluster_curl(cluster, bearer_token, "/api/v1/nodes")
        netnamespaces_payload = run_cluster_curl(cluster, bearer_token, "/apis/network.openshift.io/v1/netnamespaces")
    except RuntimeError as exc:
        return {
            "ok": False,
            "error": "cluster_query_failed",
            "detail": str(exc),
            "cluster": cluster,
        }

    nodes = build_node_entries(nodes_payload)
    netnamespaces = build_netnamespace_entries(netnamespaces_payload)

    return {
        "ok": True,
        "cluster": cluster,
        "node_count": len(nodes),
        "netnamespace_count": len(netnamespaces),
        "nodes": nodes,
        "netnamespaces": netnamespaces,
    }


def lookup_cluster_ip_usage(cluster: str, bearer_token: str, ip: str) -> dict:
    listed = list_cluster_ip_usage(cluster, bearer_token)
    if listed.get("ok") is False:
        return listed

    nodes = listed.get("nodes", [])
    netnamespaces = listed.get("netnamespaces", [])

    matching_nodes = [
        node
        for node in nodes
        if ip in node.get("ips", [])
        or ip in node.get("externalIPs", [])
        or any(address.get("address") == ip for address in node.get("addresses", []))
    ]
    matching_netnamespaces = [entry for entry in netnamespaces if ip in entry.get("egressIPs", [])]

    return {
        "ok": True,
        "cluster": cluster,
        "ip": ip,
        "matching_nodes": matching_nodes,
        "matching_netnamespaces": matching_netnamespaces,
        "matched": bool(matching_nodes or matching_netnamespaces),
    }
