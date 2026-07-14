"""Global Accelerator 信息查看 API:实时读取加速器 DNS / 静态 IP / 监听器 / 各区 endpoint group 权重与健康。"""
from fastapi import APIRouter, Query
from ..core.config import get_settings
from ..db.dynamo import get_dynamo
from ..services.aws import ga as ga_svc

router = APIRouter(prefix="/api")


@router.get("/ga")
async def ga_info(arn: str | None = Query(None)):
    # 解析顺序:前端所选 arn → Config 里 provision 时所选的 GA → env 兜底 → None(不自动挑别人的 GA)
    try:
        configured = arn or get_dynamo().get_config().get("ga_accelerator_arn") \
            or get_settings().ga_accelerator_arn or None
        return ga_svc.describe_topology(configured)
    except Exception as e:  # noqa: BLE001
        return {"configured": False, "accelerator": None, "listeners": [], "error": str(e)}
