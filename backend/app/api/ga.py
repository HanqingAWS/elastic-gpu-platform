"""Global Accelerator 信息查看 API:实时读取加速器 DNS / 静态 IP / 监听器 / 各区 endpoint group 权重与健康。"""
from fastapi import APIRouter
from ..core.config import get_settings
from ..services.aws import ga as ga_svc

router = APIRouter(prefix="/api")


@router.get("/ga")
async def ga_info():
    try:
        return ga_svc.describe_topology(get_settings().ga_accelerator_arn)
    except Exception as e:  # noqa: BLE001
        return {"configured": False, "accelerator": None, "listeners": [], "error": str(e)}
