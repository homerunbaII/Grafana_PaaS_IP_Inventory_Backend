from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from io import BytesIO

from agent.models.ip_inventory import IpInventoryListRequest, IpInventoryLookupRequest
from agent.services.ip_inventory_service import build_inventory_report_data, build_inventory_report_zip, list_requested_cluster_ip_usage
from agent.services.ip_inventory_service import lookup_requested_cluster_ip_usage

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


@router.post("/report/download")
async def handle_ip_inventory_report_download(body: IpInventoryListRequest):
    zip_bytes, filename = build_inventory_report_zip()
    return StreamingResponse(
        BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/report/data")
async def handle_ip_inventory_report_data(body: IpInventoryListRequest):
    return build_inventory_report_data()
