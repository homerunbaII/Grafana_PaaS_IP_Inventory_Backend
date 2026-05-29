import json
import logging
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from csv import DictWriter
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO, StringIO
from ipaddress import ip_network
from zipfile import ZIP_DEFLATED, ZipFile

from agent.config import CLUSTER_API_INSECURE, build_cluster_api_url, get_cluster_bearer_token, get_inventory_clusters
from agent.config import is_all_clusters_target

logger = logging.getLogger(__name__)

REPORT_RANGE_GROUPS = [
    {
        "key": "172.23.20",
        "label": "172.23.20.0/24",
        "filename": "01_172.23.20.0_24.csv",
        "cidrs": ["172.23.20.0/24"],
        "clusters": ["dprv-k8s", "eprsv-k8s"],
        "ping_enabled": True,
    },
    {
        "key": "172.23.21",
        "label": "172.23.21.0/24",
        "filename": "02_172.23.21.0_24.csv",
        "cidrs": ["172.23.21.0/24"],
        "clusters": ["sdprmn-paas", "dprmn-k8s", "dpvs-k8s"],
        "ping_enabled": True,
    },
    {
        "key": "172.23.38",
        "label": "172.23.38.0/24",
        "filename": "03_172.23.38.0_24.csv",
        "cidrs": ["172.23.38.0/24"],
        "clusters": ["dprsv-k8s", "dprrt-k8s"],
        "ping_enabled": True,
    },
    {
        "key": "172.23.4-5",
        "label": "172.23.4.0/24 + 172.23.5.0/24",
        "filename": "04_172.23.4.0_24__172.23.5.0_24.csv",
        "cidrs": ["172.23.4.0/24", "172.23.5.0/24"],
        "clusters": ["pprv-k8s", "pprmn-k8s", "pprsv-k8s"],
        "ping_enabled": True,
    },
    {
        "key": "172.23.31",
        "label": "172.23.31.0/24",
        "filename": "05_172.23.31.0_24.csv",
        "cidrs": ["172.23.31.0/24"],
        "clusters": ["pprrt-k8s"],
        "ping_enabled": False,
    },
]

CSV_FIELDNAMES = [
    "IP대역",
    "대상클러스터",
    "IP",
    "사용여부",
    "IP종류",
    "클러스터",
    "리소스종류",
    "리소스명",
    "네임스페이스",
    "Ping상태",
    "상세",
]

PING_MAX_ATTEMPTS = 2
PING_TIMEOUT_SECONDS = 1
PING_MAX_WORKERS = 128


@dataclass
class ClusterApiRequestError(RuntimeError):
    cluster: str
    http_status: int
    detail: str

    def __str__(self) -> str:
        return self.detail


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


def parse_curl_response(output: str) -> tuple[str, int]:
    marker = "__HTTP_STATUS__:"
    if marker not in output:
        return output, 0

    body, _, status_block = output.rpartition(marker)
    status_text = status_block.strip()

    try:
        return body.rstrip(), int(status_text)
    except ValueError:
        return output, 0


def parse_json_payload(body: str) -> dict | None:
    cleaned = body.strip()
    if not cleaned:
        return None

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, dict):
        return parsed

    return None


def build_cluster_api_error(cluster: str, http_status: int, payload: dict | None, raw_body: str) -> str:
    message = str((payload or {}).get("message", "") or "").strip()
    reason = str((payload or {}).get("reason", "") or "").strip()
    lowered = f"{message} {reason}".lower()

    if http_status == 401:
        if "expired" in lowered or "expire" in lowered:
            return f"{cluster}: 토큰 기간이 만료되었습니다. Secret에 저장된 토큰을 갱신해 주세요."
        return f"{cluster}: 토큰이 유효하지 않습니다. Secret에 저장된 토큰 값을 확인해 주세요."

    if http_status == 403:
        return f"{cluster}: 토큰은 유효하지만 조회 권한이 없습니다. cluster-reader 또는 필요한 권한을 확인해 주세요."

    if http_status >= 400:
        detail = message or raw_body.strip() or f"HTTP {http_status}"
        return f"{cluster}: 클러스터 API 요청이 실패했습니다. {detail}"

    return raw_body.strip() or f"{cluster}: 클러스터 API 요청이 실패했습니다."


def build_selector_requirement_match(labels: dict[str, str], requirement: dict) -> bool:
    key = str(requirement.get("key", "") or "").strip()
    operator = str(requirement.get("operator", "") or "").strip()
    values = requirement.get("values", []) or []
    label_exists = key in labels
    label_value = labels.get(key)

    if operator == "In":
        return label_exists and label_value in values
    if operator == "NotIn":
        return (not label_exists) or label_value not in values
    if operator == "Exists":
        return label_exists
    if operator == "DoesNotExist":
        return not label_exists

    return False


def namespace_matches_selector(namespace_labels: dict, selector: dict) -> bool:
    if not selector:
        return False

    match_labels = selector.get("matchLabels", {}) or {}
    for key, value in match_labels.items():
        if namespace_labels.get(key) != value:
            return False

    match_expressions = selector.get("matchExpressions", []) or []
    for requirement in match_expressions:
        if not isinstance(requirement, dict):
            return False
        if not build_selector_requirement_match(namespace_labels, requirement):
            return False

    return True


def build_ovn_egressip_entries(namespaces_payload: dict, egressips_payload: dict) -> list[dict]:
    namespace_items = namespaces_payload.get("items", [])
    egressip_items = egressips_payload.get("items", [])
    entries_by_namespace: dict[str, dict] = {}

    for namespace in namespace_items:
        metadata = namespace.get("metadata", {})
        namespace_name = str(metadata.get("name", "") or "").strip()
        namespace_labels = metadata.get("labels", {}) or {}
        if not namespace_name:
            continue

        for egressip in egressip_items:
            spec = egressip.get("spec", {}) or {}
            selector = spec.get("namespaceSelector", {}) or {}
            if not namespace_matches_selector(namespace_labels, selector):
                continue

            egress_ips = unique_non_empty_strings(spec.get("egressIPs", []) or [])
            if not egress_ips:
                continue

            existing = entries_by_namespace.setdefault(
                namespace_name,
                {
                    "name": namespace_name,
                    "netid": None,
                    "egressIPs": [],
                },
            )
            existing["egressIPs"] = unique_non_empty_strings([*existing["egressIPs"], *egress_ips])

    return list(entries_by_namespace.values())


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
            "--write-out",
            "\n__HTTP_STATUS__:%{http_code}",
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

    response_body, http_status = parse_curl_response(completed.stdout)
    payload = parse_json_payload(response_body)

    if http_status >= 400:
        raise ClusterApiRequestError(
            cluster=cluster,
            http_status=http_status,
            detail=build_cluster_api_error(cluster, http_status, payload, response_body),
        )

    try:
        return json.loads(response_body)
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


def list_cluster_netnamespace_entries(cluster: str) -> list[dict]:
    try:
        netnamespaces_payload = run_cluster_curl(cluster, "/apis/network.openshift.io/v1/netnamespaces")
        return build_netnamespace_entries(netnamespaces_payload)
    except ClusterApiRequestError as exc:
        if exc.http_status != 404:
            raise

    namespaces_payload = run_cluster_curl(cluster, "/api/v1/namespaces")
    egressips_payload = run_cluster_curl(cluster, "/apis/k8s.ovn.org/v1/egressips")
    return build_ovn_egressip_entries(namespaces_payload, egressips_payload)


def list_cluster_ip_usage(cluster: str) -> dict:
    try:
        nodes_payload = run_cluster_curl(cluster, "/api/v1/nodes")
        services_payload = run_cluster_curl(cluster, "/api/v1/services")
        netnamespaces = list_cluster_netnamespace_entries(cluster)
    except (RuntimeError, ValueError, ClusterApiRequestError) as exc:
        detail = str(exc)
        if detail == f"no bearer token configured for cluster: {cluster}":
            detail = f"{cluster}: 백엔드에 해당 클러스터 토큰이 설정되지 않았습니다."

        return {
            "ok": False,
            "error": "cluster_query_failed",
            "detail": detail,
            "cluster": cluster,
        }

    nodes = build_node_entries(nodes_payload)
    services = build_service_entries(services_payload)

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


def build_ip_usage_index(cluster_results: list[dict]) -> dict[str, list[dict]]:
    usage_by_ip: dict[str, list[dict]] = {}

    for result in cluster_results:
        if not result.get("ok"):
            continue

        cluster = str(result.get("cluster", "") or "").strip()
        if not cluster:
            continue

        for node in result.get("nodes", []) or []:
            for node_ip in node.get("ips", []) or []:
                usage_by_ip.setdefault(str(node_ip), []).append(
                    {
                        "ip_type": "nodeIP",
                        "cluster": cluster,
                        "resource_kind": "Node",
                        "resource_name": str(node.get("name", "") or "").strip(),
                        "namespace": "",
                        "details": f"roles={','.join(node.get('roles', []) or [])}",
                    }
                )

        for service in result.get("services", []) or []:
            for external_ip in service.get("externalIPs", []) or []:
                usage_by_ip.setdefault(str(external_ip), []).append(
                    {
                        "ip_type": "externalIP",
                        "cluster": cluster,
                        "resource_kind": "Service",
                        "resource_name": str(service.get("name", "") or "").strip(),
                        "namespace": str(service.get("namespace", "") or "").strip(),
                        "details": f"type={service.get('type', '')};clusterIP={service.get('clusterIP', '')}",
                    }
                )

        for netnamespace in result.get("netnamespaces", []) or []:
            for egress_ip in netnamespace.get("egressIPs", []) or []:
                usage_by_ip.setdefault(str(egress_ip), []).append(
                    {
                        "ip_type": "egressIP",
                        "cluster": cluster,
                        "resource_kind": "Netnamespace",
                        "resource_name": str(netnamespace.get("name", "") or "").strip(),
                        "namespace": str(netnamespace.get("name", "") or "").strip(),
                        "details": f"netid={netnamespace.get('netid', '')}",
                    }
                )

    return usage_by_ip


def join_usage_values(usages: list[dict], field: str) -> str:
    seen: set[str] = set()
    values: list[str] = []

    for usage in usages:
        raw = str(usage.get(field, "") or "").strip()
        if not raw or raw in seen:
            continue
        seen.add(raw)
        values.append(raw)

    return "\n".join(values)


def build_usage_summary(usages: list[dict]) -> str:
    summaries: list[str] = []

    for usage in usages:
        cluster = str(usage.get("cluster", "") or "").strip()
        ip_type = str(usage.get("ip_type", "") or "").strip()
        resource_kind = str(usage.get("resource_kind", "") or "").strip()
        namespace = str(usage.get("namespace", "") or "").strip()
        resource_name = str(usage.get("resource_name", "") or "").strip()
        details = str(usage.get("details", "") or "").strip()

        if namespace:
            base = f"{cluster} | {ip_type} | {resource_kind} | {namespace}/{resource_name}"
        else:
            base = f"{cluster} | {ip_type} | {resource_kind} | {resource_name}"

        summaries.append(f"{base} | {details}" if details else base)

    return "\n".join(summaries)


def ping_binary_available() -> bool:
    return shutil.which("ping") is not None


def ping_ip_once(ip_text: str) -> bool:
    command = ["ping", "-c", "1", "-W", str(PING_TIMEOUT_SECONDS), ip_text]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=PING_TIMEOUT_SECONDS + 2,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False

    return completed.returncode == 0


def ping_ip_with_retries(ip_text: str) -> bool:
    for _ in range(PING_MAX_ATTEMPTS):
        if ping_ip_once(ip_text):
            return True
    return False


def collect_ping_targets(usage_by_ip: dict[str, list[dict]]) -> list[str]:
    targets: list[str] = []

    for range_group in REPORT_RANGE_GROUPS:
        if not range_group["ping_enabled"]:
            continue

        for cidr in range_group["cidrs"]:
            for current_ip in ip_network(cidr):
                ip_text = str(current_ip)
                if ip_text in usage_by_ip:
                    continue
                targets.append(ip_text)

    return targets


def run_ping_scan(usage_by_ip: dict[str, list[dict]]) -> dict[str, bool]:
    if not ping_binary_available():
        logger.warning("ping binary is not available; skipping ping scan")
        return {}

    targets = collect_ping_targets(usage_by_ip)
    if not targets:
        return {}

    results: dict[str, bool] = {}
    max_workers = min(PING_MAX_WORKERS, len(targets))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ip = {executor.submit(ping_ip_with_retries, ip_text): ip_text for ip_text in targets}

        for future in as_completed(future_to_ip):
            ip_text = future_to_ip[future]
            try:
                results[ip_text] = future.result()
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("Ping scan failed for %s: %s", ip_text, exc)
                results[ip_text] = False

    return results


def build_report_row(
    *,
    range_group: dict,
    cidr: str,
    ip_text: str,
    usages: list[dict],
    ping_results: dict[str, bool],
    ping_available: bool,
) -> dict:
    ping_enabled = bool(range_group["ping_enabled"])
    ping_checked = ping_enabled and ping_available and not usages
    ping_responded = bool(ping_results.get(ip_text, False)) if ping_checked else False
    status = "사용중" if usages or ping_responded else ""

    ip_type = join_usage_values(usages, "ip_type")
    if not ip_type and ping_responded:
        ip_type = "ping응답"

    resource_kind = join_usage_values(usages, "resource_kind")
    if not resource_kind and ping_responded:
        resource_kind = "외부응답"

    detail = build_usage_summary(usages)
    if not detail and ping_responded:
        detail = "OKD 미사용, ping 응답 확인"
    elif not detail and not ping_enabled:
        detail = "ping 스캔 제외 대역"

    ping_status = ""
    if ping_responded:
        ping_status = "응답"
    elif not ping_enabled:
        ping_status = "제외"

    return {
        "rangeLabel": range_group["label"],
        "cidr": cidr,
        "targetClusters": list(range_group["clusters"]),
        "ip": ip_text,
        "status": status,
        "okdUsed": bool(usages),
        "ipType": ip_type,
        "cluster": join_usage_values(usages, "cluster"),
        "resourceKind": resource_kind,
        "resourceName": join_usage_values(usages, "resource_name"),
        "namespace": join_usage_values(usages, "namespace"),
        "pingStatus": ping_status,
        "pingChecked": ping_checked,
        "pingResponded": ping_responded,
        "detail": detail,
        "csv": {
            "IP대역": cidr,
            "대상클러스터": ",".join(range_group["clusters"]),
            "IP": ip_text,
            "사용여부": status,
            "IP종류": ip_type,
            "클러스터": join_usage_values(usages, "cluster"),
            "리소스종류": resource_kind,
            "리소스명": join_usage_values(usages, "resource_name"),
            "네임스페이스": join_usage_values(usages, "namespace"),
            "Ping상태": ping_status,
            "상세": detail,
        },
    }


def build_range_report(range_group: dict, usage_by_ip: dict[str, list[dict]], ping_results: dict[str, bool], ping_available: bool) -> dict:
    rows: list[dict] = []

    for cidr in range_group["cidrs"]:
        for current_ip in ip_network(cidr):
            ip_text = str(current_ip)
            usages = usage_by_ip.get(ip_text, [])
            rows.append(
                build_report_row(
                    range_group=range_group,
                    cidr=cidr,
                    ip_text=ip_text,
                    usages=usages,
                    ping_results=ping_results,
                    ping_available=ping_available,
                )
            )

    used_count = sum(1 for row in rows if row["status"] == "사용중")
    okd_used_count = sum(1 for row in rows if row["okdUsed"])
    ping_used_count = sum(1 for row in rows if row["pingResponded"])

    return {
        "key": range_group["key"],
        "label": range_group["label"],
        "filename": range_group["filename"],
        "cidrs": list(range_group["cidrs"]),
        "targetClusters": list(range_group["clusters"]),
        "pingEnabled": bool(range_group["ping_enabled"]),
        "rowCount": len(rows),
        "usedCount": used_count,
        "okdUsedCount": okd_used_count,
        "pingUsedCount": ping_used_count,
        "rows": rows,
    }


def build_csv_bytes(rows: list[dict]) -> bytes:
    output = StringIO(newline="")
    writer = DictWriter(output, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()
    writer.writerows(row["csv"] for row in rows)
    return output.getvalue().encode("utf-8-sig")


def build_inventory_report_data() -> dict:
    all_cluster_result = list_all_cluster_ip_usage()
    cluster_results = all_cluster_result.get("clusters", []) or []
    usage_by_ip = build_ip_usage_index(cluster_results)
    ping_available = ping_binary_available()
    ping_results = run_ping_scan(usage_by_ip) if ping_available else {}

    ranges = [
        build_range_report(
            range_group=range_group,
            usage_by_ip=usage_by_ip,
            ping_results=ping_results,
            ping_available=ping_available,
        )
        for range_group in REPORT_RANGE_GROUPS
    ]

    return {
        **all_cluster_result,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ping_available": ping_available,
        "ping_target_count": len(collect_ping_targets(usage_by_ip)) if ping_available else 0,
        "ping_responded_count": sum(1 for responded in ping_results.values() if responded),
        "ranges": ranges,
    }


def build_inventory_report_zip() -> tuple[bytes, str]:
    report = build_inventory_report_data()
    ranges = report.get("ranges", []) or []

    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as zip_file:
        for range_report in ranges:
            csv_bytes = build_csv_bytes(range_report.get("rows", []) or [])
            zip_file.writestr(range_report["filename"], csv_bytes)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return buffer.getvalue(), f"ip_inventory_report_{timestamp}.zip"


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
