import json
import logging
import shutil
import subprocess

from agent.config import CLUSTER_API_INSECURE, get_cluster_bearer_token, get_inventory_clusters, is_all_clusters_target
from agent.config import build_cluster_api_url

logger = logging.getLogger(__name__)


def unique_non_empty_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []

    for value in values:
        if not isinstance(value, str):
            continue

        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue

        seen.add(cleaned)
        result.append(cleaned)

    return result


def ensure_curl_available() -> None:
    if shutil.which("curl"):
        return
    raise RuntimeError("curl is not available in this environment")


def run_cluster_curl(cluster: str, api_path: str) -> dict:
    ensure_curl_available()

    bearer_token = get_cluster_bearer_token(cluster)
    base_url = build_cluster_api_url(cluster)
    url = f"{base_url}{api_path}"
    command = [
        "curl",
        "--silent",
        "--show-error",
    ]

    if CLUSTER_API_INSECURE:
        command.append("--insecure")

    command.extend(
        [
            "-H",
            f"Authorization: Bearer {bearer_token}",
            url,
        ]
    )

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
        internal_ips = unique_non_empty_strings(
            [address.get("address") for address in addresses if address.get("type") == "InternalIP"]
        )
        if not internal_ips:
            continue

        entries.append(
            {
                "name": metadata.get("name"),
                "roles": roles,
                "ips": internal_ips,
            }
        )

    return entries


def extract_service_external_ips(item: dict) -> list[str]:
    spec = item.get("spec", {})
    status = item.get("status", {})
    load_balancer = status.get("loadBalancer", {})
    ingress_entries = load_balancer.get("ingress", []) or []

    spec_external_ips = spec.get("externalIPs", []) or []
    load_balancer_ips = [entry.get("ip") for entry in ingress_entries if isinstance(entry, dict)]

    return unique_non_empty_strings([*spec_external_ips, *load_balancer_ips])


def build_service_entries(payload: dict) -> list[dict]:
    items = payload.get("items", [])
    entries: list[dict] = []

    for item in items:
        external_ips = extract_service_external_ips(item)
        if not external_ips:
            continue

        metadata = item.get("metadata", {})
        spec = item.get("spec", {})

        entries.append(
            {
                "namespace": metadata.get("namespace"),
                "name": metadata.get("name"),
                "type": spec.get("type"),
                "clusterIP": spec.get("clusterIP"),
                "externalIPs": external_ips,
            }
        )

    return entries


def build_netnamespace_entries(payload: dict) -> list[dict]:
    items = payload.get("items", [])
    entries: list[dict] = []

    for item in items:
        metadata = item.get("metadata", {})
        egress_ips = item.get("egressIPs", []) or []
        cleaned_egress_ips = unique_non_empty_strings(egress_ips)
        if not cleaned_egress_ips:
            continue

        entries.append(
            {
                "name": metadata.get("name"),
                "netid": item.get("netid"),
                "egressIPs": cleaned_egress_ips,
            }
        )

    return entries


def list_cluster_ip_usage(cluster: str) -> dict:
    try:
        nodes_payload = run_cluster_curl(cluster, "/api/v1/nodes")
        services_payload = run_cluster_curl(cluster, "/api/v1/services")
        netnamespaces_payload = run_cluster_curl(cluster, "/apis/network.openshift.io/v1/netnamespaces")
    except (RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "error": "cluster_query_failed",
            "detail": str(exc),
            "cluster": cluster,
        }

    nodes = build_node_entries(nodes_payload)
    services = build_service_entries(services_payload)
    netnamespaces = build_netnamespace_entries(netnamespaces_payload)

    return {
        "ok": True,
        "cluster": cluster,
        "node_count": len(nodes),
        "service_count": len(services),
        "netnamespace_count": len(netnamespaces),
        "nodes": nodes,
        "services": services,
        "netnamespaces": netnamespaces,
    }


def lookup_cluster_ip_usage(cluster: str, ip: str) -> dict:
    listed = list_cluster_ip_usage(cluster)
    if listed.get("ok") is False:
        return listed

    nodes = listed.get("nodes", [])
    services = listed.get("services", [])
    netnamespaces = listed.get("netnamespaces", [])

    matching_nodes = [node for node in nodes if ip in node.get("ips", [])]
    matching_services = [entry for entry in services if ip in entry.get("externalIPs", [])]
    matching_netnamespaces = [entry for entry in netnamespaces if ip in entry.get("egressIPs", [])]

    return {
        "ok": True,
        "cluster": cluster,
        "ip": ip,
        "matching_nodes": matching_nodes,
        "matching_services": matching_services,
        "matching_netnamespaces": matching_netnamespaces,
        "matched": bool(matching_nodes or matching_services or matching_netnamespaces),
    }


def annotate_cluster_entries(cluster: str, entries: list[dict]) -> list[dict]:
    return [{**entry, "cluster": cluster} for entry in entries]


def list_all_cluster_ip_usage() -> dict:
    results = [list_cluster_ip_usage(cluster) for cluster in get_inventory_clusters()]
    successful_results = [result for result in results if result.get("ok")]

    return {
        "ok": True,
        "cluster": "all",
        "cluster_count": len(results),
        "successful_cluster_count": len(successful_results),
        "failed_cluster_count": len(results) - len(successful_results),
        "node_count": sum(int(result.get("node_count", 0) or 0) for result in successful_results),
        "service_count": sum(int(result.get("service_count", 0) or 0) for result in successful_results),
        "netnamespace_count": sum(int(result.get("netnamespace_count", 0) or 0) for result in successful_results),
        "clusters": results,
    }


def lookup_all_cluster_ip_usage(ip: str) -> dict:
    results = [lookup_cluster_ip_usage(cluster, ip) for cluster in get_inventory_clusters()]
    successful_results = [result for result in results if result.get("ok")]

    matching_nodes: list[dict] = []
    matching_services: list[dict] = []
    matching_netnamespaces: list[dict] = []
    matched_cluster_count = 0

    for result in successful_results:
        cluster = str(result.get("cluster", "") or "")
        cluster_matching_nodes = annotate_cluster_entries(cluster, result.get("matching_nodes", []))
        cluster_matching_services = annotate_cluster_entries(cluster, result.get("matching_services", []))
        cluster_matching_netnamespaces = annotate_cluster_entries(cluster, result.get("matching_netnamespaces", []))

        if cluster_matching_nodes or cluster_matching_services or cluster_matching_netnamespaces:
            matched_cluster_count += 1

        matching_nodes.extend(cluster_matching_nodes)
        matching_services.extend(cluster_matching_services)
        matching_netnamespaces.extend(cluster_matching_netnamespaces)

    return {
        "ok": True,
        "cluster": "all",
        "ip": ip,
        "matched": bool(matching_nodes or matching_services or matching_netnamespaces),
        "cluster_count": len(results),
        "successful_cluster_count": len(successful_results),
        "failed_cluster_count": len(results) - len(successful_results),
        "matched_cluster_count": matched_cluster_count,
        "matching_nodes": matching_nodes,
        "matching_services": matching_services,
        "matching_netnamespaces": matching_netnamespaces,
        "clusters": results,
    }


def list_requested_cluster_ip_usage(cluster: str) -> dict:
    if is_all_clusters_target(cluster):
        return list_all_cluster_ip_usage()
    return list_cluster_ip_usage(cluster)


def lookup_requested_cluster_ip_usage(cluster: str, ip: str) -> dict:
    if is_all_clusters_target(cluster):
        return lookup_all_cluster_ip_usage(ip)
    return lookup_cluster_ip_usage(cluster, ip)
