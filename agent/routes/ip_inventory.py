from fastapi import APIRouter

from agent.models.ip_inventory import IpInventoryListRequest, IpInventoryLookupRequest
from agent.services.ip_inventory_service import list_requested_cluster_ip_usage, lookup_requested_cluster_ip_usage

router = APIRouter(prefix="/ip-inventory", tags=["ip-inventory"])


@router.post("/list")
async def handle_ip_inventory_list(body: IpInventoryListRequest):
    return list_requested_cluster_ip_usage(cluster=body.cluster)


@router.post("/lookup")
async def handle_ip_inventory_lookup(body: IpInventoryLookupRequest):
    return lookup_requested_cluster_ip_usage(
        cluster=body.cluster,
        ip=body.ip,
    )
