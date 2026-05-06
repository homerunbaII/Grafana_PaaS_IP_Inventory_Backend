from fastapi import APIRouter

router = APIRouter()


@router.get('/')
async def root() -> dict[str, str]:
    return {
        'status': 'ok',
        'service': 'grafana-paas-ip-inventory-backend',
    }


@router.get('/health')
async def health() -> dict[str, str]:
    return {
        'status': 'ok',
    }
